"""A harness-driven buyer that shops at an A2A v1.0 seller (JSON-RPC binding).

    python buyer.py --url http://127.0.0.1:9999 --item lamp --budget 120

The harness (see harness.py) decides every offer and when to accept or walk
away. This file only speaks A2A (Agent Card, SendMessage, CancelTask, the
A2A-Version header, taskId follow-ups), keeps the history, and enforces the
rules of the game: never offer above the budget, send the offer as a bare
number, and stop within the time limit.
"""

import argparse
import re
import time
import uuid

import harness
import httpx

HEADERS = {"A2A-Version": "1.0"}
TIME_LIMIT = 270  # seconds; the trade test stops a buyer after 300
HARNESS_TIMEOUT = 60  # seconds per harness call
MAX_TURNS = 12  # offers sent in one negotiation
ACTIVE = "TASK_STATE_INPUT_REQUIRED"


# ---------------------------------------------------------------- decision (written by hand)


def build_buyer_prompt(item: str, budget: int, history: list[dict]) -> str:
    """Builds the prompt that asks the harness for the buyer's next move.

    Args:
        item: The item to buy ("lamp", "chair" or "bookshelf").
        budget: The most the buyer may pay.
        history: Every message so far, oldest first, as
            {"role": "buyer" | "seller", "text": str, "price": int | float | None}.

    Returns:
        The full prompt for harness.ask().
    """
    raise NotImplementedError("write the buyer prompt")


def parse_buyer_decision(answer: str) -> dict:
    """Reads the buyer's move from the harness's answer.

    Returns:
        {"action": "offer" | "accept" | "walk", "price": int | None}

    Raises:
        ValueError: If the answer holds no readable decision.
    """
    raise NotImplementedError("write the buyer answer parser")


# ---------------------------------------------------------------- plumbing


def log(line: str) -> None:
    print(line, flush=True)


def last_number(text: str):
    nums = re.findall(r"\d+(?:\.\d+)?", text)
    if not nums:
        return None
    n = float(nums[-1])
    return int(n) if n.is_integer() else n


def status_text(task: dict) -> str:
    parts = task.get("status", {}).get("message", {}).get("parts", [])
    return " ".join(p.get("text", "") for p in parts).strip()


class Seller:
    """JSON-RPC client for one A2A seller, found through its Agent Card."""

    def __init__(self, base_url: str):
        self.http = httpx.Client(headers=HEADERS, timeout=200)
        card = (
            self.http.get(base_url.rstrip("/") + "/.well-known/agent-card.json")
            .raise_for_status()
            .json()
        )
        iface = next(
            (
                i
                for i in card.get("supportedInterfaces", [])
                if i.get("protocolBinding") == "JSONRPC"
            ),
            None,
        )
        if iface is None:
            raise RuntimeError("the seller's card has no JSONRPC interface")
        self.url = iface["url"]
        log(f"card: {card.get('name')!r} -> {self.url}")

    def call(self, method: str, params: dict):
        body = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": method,
            "params": params,
        }
        r = self.http.post(self.url, json=body).json()
        if "error" in r:
            raise RuntimeError(
                f"{method} error {r['error'].get('code')}: {r['error'].get('message')}"
            )
        return r["result"]

    def send(self, text: str, task: dict | None = None) -> dict:
        msg = {
            "role": "ROLE_USER",
            "messageId": str(uuid.uuid4()),
            "parts": [{"text": text}],
        }
        if task is not None:
            msg["taskId"], msg["contextId"] = task["id"], task["contextId"]
        result = self.call("SendMessage", {"message": msg})
        return result.get("task", result)

    def cancel(self, task: dict) -> None:
        self.call("CancelTask", {"id": task["id"]})


def decide(item: str, budget: int, history: list[dict], deadline: float):
    """Asks the harness for a move; tries twice, returns None if both fail or time runs out."""
    prompt = build_buyer_prompt(item, budget, history)
    for attempt in (1, 2):
        left = deadline - time.monotonic()
        if left < 15:
            log("not enough time left for another harness call")
            return None
        try:
            answer = harness.ask(prompt, timeout=min(HARNESS_TIMEOUT, left - 10))
            log(f"harness answer #{attempt}:\n{answer}")
            return parse_buyer_decision(answer)
        except (harness.HarnessError, ValueError) as e:
            log(f"harness attempt {attempt} failed: {e}")
    return None


def enforce(decision, ask, last_offer, budget: int):
    """Turns the harness's decision into a legal move: (action, price, notes)."""
    hold = ("offer", last_offer) if last_offer is not None else ("walk", None)
    if decision is None:
        return (*hold, [f"no usable decision: {hold[0]} {hold[1]}"])
    action, price = decision["action"], decision.get("price")
    if action == "walk":
        return "walk", None, []
    if action == "accept":
        if ask is None or ask > budget:
            return (
                *hold,
                [
                    f"accept of ask {ask} not allowed (budget {budget}): {hold[0]} {hold[1]}"
                ],
            )
        return "accept", ask, []
    notes = []
    if price is None:
        return (*hold, [f"offer without a price: {hold[0]} {hold[1]}"])
    if price > budget:
        notes.append(f"offer {price} above budget: lowered to {budget}")
        price = budget
    if ask is not None and price >= ask:
        return "accept", ask, notes + [f"offer {price} >= ask {ask}: accept the ask"]
    return "offer", price, notes


def main(url: str, item: str, budget: int) -> None:
    deadline = time.monotonic() + TIME_LIMIT
    seller = Seller(url)
    opening = f"I want to buy a {item}"
    history = [{"role": "buyer", "text": opening, "price": None}]
    log(f"-> {opening}")
    task = seller.send(opening)
    last_offer = None

    for _ in range(MAX_TURNS):
        if "status" not in task:  # a plain Message: nothing to continue
            log(f"<- message without a task: {task}")
            return
        state, text = task["status"]["state"], status_text(task)
        log(f"<- {state} | {text}")
        if state != ACTIVE:
            for a in task.get("artifacts", []):
                log(
                    f"   artifact {a.get('name')}: {[p.get('data', p.get('text')) for p in a.get('parts', [])]}"
                )
            return
        ask = last_number(text)
        history.append({"role": "seller", "text": text, "price": ask})

        if ask is not None and last_offer is not None and ask <= last_offer:
            action, price, notes = (
                "accept",
                ask,
                [f"ask {ask} <= our last offer {last_offer}: accept"],
            )
        else:
            decision = decide(item, budget, history, deadline)
            action, price, notes = enforce(decision, ask, last_offer, budget)
        for n in notes:
            log(f"rule: {n}")
        log(f"move: {action} {price}")
        if action == "walk":
            break
        # Send the bare number: some sellers read the first number in the message.
        task = seller.send(str(price), task)
        history.append({"role": "buyer", "text": str(price), "price": price})
        last_offer = price
    else:
        log(f"no deal after {MAX_TURNS} offers")

    if task.get("status", {}).get("state") == ACTIVE:
        seller.cancel(task)
        log("walked away (CancelTask)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--item", required=True)
    ap.add_argument("--budget", type=int, required=True)
    args = ap.parse_args()
    main(args.url, args.item, args.budget)
