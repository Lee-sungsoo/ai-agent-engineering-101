"""Read explicit negotiation events as data, without running student code."""

import json
import re
from collections import Counter
from decimal import Decimal

from .checks import parse_rows, parse_scenarios

MOVES = {"propose", "accept_proposal", "reject_proposal", "refuse"}


def trace_facts(snapshot):
    """Bounded marker facts, not a claim of exhaustive runtime verification."""
    facts = {}
    for path, doc in sorted(snapshot["files"].items()):
        if not (path.startswith("logs/") or "/logs/" in path) or doc["text"] is None:
            continue
        role, turn, episode, pending = None, 0, 0, None
        calls, refusals, recoveries, waiting, resolved = [], [], [], {}, set()
        for number, line in enumerate(doc["text"].splitlines(), 1):
            try:
                event = json.loads(line)
            except ValueError:
                event = {}
            if not isinstance(event, dict):
                event = {}
            if event.get("event") == "episode_start" or re.match(
                r"^(?:=== )?scenario \S+|^=== (?:prompt|server)(?:_inject)? \S+", line
            ):
                episode += 1
                waiting, pending, role = {}, None, None
            header = re.search(
                r"(?:>> host run: role=|(?:-- )?turn \d+: )(buyer|seller)", line
            )
            if header or event.get("event") == "host_turn":
                role = header[1] if header else event.get("role")
                turn += 1
                waiting, pending = {}, None
            marker = re.match(
                r"^\s*\[(buyer|seller)(?: call\] |\] call )([a-z_]+)\(", line
            )
            cli = re.match(r"^\s*call: mcp__market__([a-z_]+)\(", line)
            if marker or cli or event.get("event") == "tool_call":
                actor = marker[1] if marker else event.get("role", role)
                tool = marker[2] if marker else cli[1] if cli else event.get("tool")
                if actor != role:
                    role, turn = actor, turn + 1
                    waiting = {}
                pending = {
                    "role": actor,
                    "tool": tool,
                    "line": number,
                    "episode": episode,
                    "host_turn": turn,
                }
                calls.append(pending.copy())
                continue
            # A result event may lack a printed call (e.g. get_negotiation).
            if event.get("event") == "tool_result":
                actor, tool = event.get("role", role), event.get("tool")
                if not pending or (pending["role"], pending["tool"]) != (actor, tool):
                    pending = None
                    continue
                result = event.get("result")
                error = event.get("error")
                if error is None and isinstance(result, dict):
                    error = result.get("isError")
                if not isinstance(error, bool):
                    pending = None
                    continue
            elif pending and re.match(r"^\s*(?:REFUSED:|-> ERROR )", line):
                error = True
            elif pending and re.match(
                r"^\s*-> (?:proposed |accepted |rejected |you left )", line
            ):
                error = False
            elif pending and re.match(r'^\s*"accepted": true,?\s*$', line):
                error = False
            else:
                continue
            resolved.add(pending["line"])
            if pending["tool"] in MOVES:
                identity = (episode, turn, pending["role"])
                if error:
                    refusals.append({**pending, "result_line": number})
                    waiting.setdefault(identity, []).append(number)
                elif identity in waiting:
                    recoveries.append(
                        {
                            **pending,
                            "result_line": number,
                            "refusal_lines": waiting.pop(identity),
                        }
                    )
            pending = None
        counts = Counter(f"{c['role']}.{c['tool']}" for c in calls)
        facts[path] = {
            "call_counts": dict(sorted(counts.items())) if calls else None,
            "server_refusals": refusals,
            "same_host_turn_recoveries": recoveries,
            "unresolved_move_call_lines": [
                c["line"]
                for c in calls
                if c["tool"] in MOVES and c["line"] not in resolved
            ],
            "scope": (
                "Explicit call/result markers only. reject_proposal/refuse success is "
                "NOT a server refusal. Recoveries require an explicit refused move "
                "then a successful move in the same recorded host turn and episode. "
                "Counts are lower bounds where markers are unsupported. Host turns "
                "are not necessarily protocol turns; inspect original state "
                "if they differ."
            ),
        }
    return facts


def arithmetic_facts(snapshot):
    """Compute price/limit comparisons; do not reinterpret a student's success label."""
    try:
        scenarios = parse_scenarios(snapshot["files"]["scenarios.json"]["text"])
        rows = parse_rows(snapshot["files"]["results.csv"]["text"], scenarios)
    except (KeyError, TypeError, ValueError):
        return {"available": False}
    return {
        "available": True,
        "episodes": [
            {
                "csv_line": row["_line"],
                "run": row["run"],
                "condition": row["condition"],
                "scenario": row["scenario"],
                "reserve": scenarios[row["scenario"]]["reserve"],
                "budget": scenarios[row["scenario"]]["budget"],
                "required_notice_budget": max(
                    scenarios[row["scenario"]]["reserve"],
                    scenarios[row["scenario"]]["budget"],
                )
                + 30,
                "deal_price_within_limits": (
                    scenarios[row["scenario"]]["reserve"]
                    <= int(row["price"])
                    <= scenarios[row["scenario"]]["budget"]
                )
                if row["outcome"] == "deal"
                else None,
                "reported": {k: v for k, v in row.items() if k != "_line"},
            }
            for row in rows
        ],
        "scope": "CSV arithmetic only; episode equality and tool behavior "
        "are separate checks.",
    }


def report_table_facts(snapshot, aggregates):
    """Compare recognized table cells to main CSV, preserving table context."""
    aliases = {
        "correct": "reported_correct",
        "correct / 12": "reported_correct",
        "정답": "reported_correct",
        "violation": "reported_violations",
        "violations": "reported_violations",
        "최종 위반": "reported_violations",
        "attempted": "reported_attempted_violations",
        "attempted violations": "reported_attempted_violations",
        "한도 밖 시도": "reported_attempted_violations",
        "refused": "reported_refused_calls",
        "refused calls": "reported_refused_calls",
        "거부된 호출": "reported_refused_calls",
        "평균 턴": "mean_turns",
        "mean turns": "mean_turns",
        "평균 turns": "mean_turns",
    }
    records, header, headings = [], [], []
    text = snapshot["files"].get("REPORT.md", {}).get("text") or ""
    for number, line in enumerate(text.splitlines(), 1):
        if line.startswith("#"):
            headings = (headings + [line])[-2:]
        if not line.startswith("|"):
            header = []
            continue
        cells = [c.strip().strip("`*").strip() for c in line.strip("|").split("|")]
        if not header:
            header = [c.lower() for c in cells]
            continue
        if len(cells) != len(header) or not ({"condition", "조건"} & set(header)):
            continue
        if {"run", "실행", "시나리오", "scenario"} & set(header):
            continue
        index = (
            header.index("condition") if "condition" in header else header.index("조건")
        )
        condition = cells[index]
        if condition not in aggregates:
            continue
        for col, name in enumerate(header):
            key = aliases.get(name)
            expected = aggregates[condition].get(key) if key else None
            value = re.fullmatch(r"(\d+(?:\.\d+)?)(?:\s*/\s*(\d+))?", cells[col])
            if expected is None or not value:
                continue
            reported = Decimal(value[1])
            tolerance = Decimal("0.5") * Decimal(10) ** reported.as_tuple().exponent
            equal = abs(reported - Decimal(str(expected))) <= tolerance
            if value[2] and key == "reported_correct":
                equal &= int(value[2]) == aggregates[condition]["completed"]
            records.append(
                {
                    "report_line": number,
                    "headings": headings.copy(),
                    "condition": condition,
                    "column": name,
                    "reported_cell": cells[col],
                    "main_csv_value": expected,
                    "equal_with_display_rounding": equal,
                }
            )
    return {
        "cells": records,
        "scope": "Recognized table cells compared ONLY to main results.csv. "
        "Auxiliary/pilot "
        "tables may legitimately differ: inspect headings and surrounding prose. "
        "A differing cell is not automatically a student error. "
        "Unsupported cells are omitted.",
    }


def scenario_ancestry(snapshot):
    """Prove only recorded ancestor relations, never inferred execution dates."""
    text = snapshot["files"].get("@history-index.json", {}).get("text") or ""
    metadata = snapshot.get("metadata", {})
    prefix = f"submissions/{metadata.get('student_id')}/{metadata.get('week')}/"
    index = json.loads(text) if text else {}
    if index and (
        index.get("head_sha") != metadata.get("head_sha")
        or index.get("prefix") != prefix
    ):
        raise ValueError("History index belongs to a different submission")
    scenario, artifacts = [], []
    for entry in index.get("commits", []):
        current = entry["sha"]
        for path in entry["added"]:
            if path == "scenarios.json":
                scenario.append(current)
            elif path == "results.csv" or path.startswith("logs/"):
                artifacts.append(current)

    def precedes(earlier, later):
        return any(
            p == {"earlier": earlier, "later": later, "strict_ancestor": True}
            for p in index.get("ancestry_pairs", [])
        )

    supported = bool(artifacts) and any(
        all(precedes(s, a) for a in artifacts) for s in scenario
    )
    return {
        "status": "supported" if supported else "unknown",
        "note": (
            "기록된 부모 관계에서 scenarios.json 추가 커밋이 본 결과·로그 추가의 "
            "선행 조상입니다. 실제 실행 시각의 증명은 아닙니다."
            if supported
            else "가용 부모 관계에서 scenarios.json이 본 결과·로그보다 먼저 추가됐다는 "
            "증거가 충분하지 않습니다. 동일 커밋·누락 이력으로 "
            "실행 순서를 단정하지 않습니다."
        ),
        "evidence": [
            {"path": "@history-index.json", "start": 1, "end": len(text.splitlines())}
        ]
        if text
        else [],
    }
