"""Locked, immutable judgments and explicit instructor policy answers."""

import fcntl
import os
from contextlib import contextmanager
from pathlib import Path

from .storage import canonical, digest, read_json, write_new


@contextmanager
def locked(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


class Ledger:
    """Cache successful validated results; failed attempts require explicit retry."""

    def __init__(self, root):
        self.root = Path(root)

    def decide(self, request, compute, validate, retry_failed=False):
        key = digest(request)
        root = self.root / "judgments" / key
        with locked(root / ".lock"):
            completed = root / "result.json"
            if completed.exists():
                record = read_json(completed)
                if record["request"] != request or record["id"] != digest(
                    {k: v for k, v in record.items() if k != "id"}
                ):
                    raise ValueError("Judgment ledger integrity mismatch")
                validate(record["result"])
                return record, True
            attempts = sorted(root.glob("attempt-*"))
            if attempts and not retry_failed:
                raise ValueError("Unfinished judgment; use explicit retry-failed")
            attempt = root / f"attempt-{len(attempts) + 1:03d}"
            write_new(attempt / "request.json", request)
            try:
                result, metadata = compute(attempt)
                validate(result)
            except Exception as error:
                write_new(
                    attempt / "failure.json", {"error_type": type(error).__name__}
                )
                raise
            record = {"request": request, "result": result, "metadata": metadata}
            record["id"] = digest(record)
            staged = attempt / "validated.json"
            write_new(staged, record)
            # Atomic visibility: a killed writer never leaves a partial cache hit.
            os.link(staged, completed)
            return record, False


class QuestionBox:
    def __init__(self, root):
        self.root = Path(root) / "questions"

    def register(self, question, task, waiter=None):
        key = digest(question)
        root = self.root / key
        with locked(root / ".lock"):
            path = root / "question.json"
            if not path.exists():
                write_new(path, question)
            waiting = {"task": task, "waiter": waiter or task}
            pending = root / "waiters" / f"{digest(waiting)}.json"
            if not pending.exists():
                write_new(pending, waiting)
        return key

    def policies(self, task):
        policies = {}
        for root in sorted(self.root.glob("*")):
            if not any(
                read_json(p)["task"] == task for p in (root / "waiters").glob("*.json")
            ):
                continue
            question = read_json(root / "question.json")
            answer_path = root / "answer.json"
            policies[question["policy"]] = {
                "question": question,
                "answer": read_json(answer_path) if answer_path.exists() else None,
            }
        return policies

    def answer(self, key, value, reviewer):
        if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise ValueError("Use the full question SHA256")
        root = self.root / key
        with locked(root / ".lock"):
            question = read_json(root / "question.json")
            if value not in question["options"] or not reviewer.strip():
                raise ValueError("Answer must be an offered option with a reviewer")
            path = root / "answer.json"
            previous = read_json(path) if path.exists() else None
            if previous and previous["value"] == value:
                return
            answer = {"value": value, "reviewer": reviewer, "previous": previous}
            # Policy revisions are append-only; the pointer is an atomic view.
            write_new(root / "answers" / f"{digest(answer)}.json", answer)
            temporary = root / "answer.tmp"
            temporary.write_text(canonical(answer), encoding="utf-8")
            temporary.replace(path)

    def list(self):
        return [
            {
                "id": root.name,
                "question": read_json(root / "question.json"),
                "answer": read_json(root / "answer.json")
                if (root / "answer.json").exists()
                else None,
                "waiting_tasks": len(list((root / "waiters").glob("*.json"))),
            }
            for root in sorted(self.root.glob("*"))
            if (root / "question.json").exists()
        ]
