"""A harness-driven seller exposed as an A2A server (a2a-sdk 1.2.x, JSON-RPC binding).

    python seller.py --port 9999

The harness (see harness.py) decides the asking price, every counteroffer, and
when to accept or reject. This file only speaks A2A, keeps each task's history,
and enforces the rules of the game: never ask or sell below the reserve, end
every INPUT_REQUIRED message with the asking price, and close the deal when the
buyer meets the current ask.
"""

import argparse
import asyncio
import json
import math
import re

import uvicorn
from a2a.helpers import (
    get_message_text,
    new_data_part,
    new_task_from_user_message,
    new_text_part,
)
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from starlette.applications import Starlette

import harness

DEFAULT = {
    "name": "Oak & Brass Workshop",
    "description": (
        "Premium furniture workshop: solid-brass desk lamps, hardwood chairs and hardwood bookshelves, "
        "each made by hand and covered by a 2-year warranty. Prices reflect the materials and the craft."
    ),
    "skill_description": (
        "Name the piece you want (lamp, chair or bookshelf) and we quote a price for it. "
        "Send your offer as a number; we answer with a counteroffer or close the sale with a receipt."
    ),
    "port": 9999,
    "reserve": {"lamp": 100, "chair": 180, "bookshelf": 260},
}
HARNESS_TIMEOUT = (
    80  # seconds per call; one retry stays under the kit's 200 s per request
)


# ---------------------------------------------------------------- decision (written by hand)


def build_seller_prompt(item: str, reserve: int, history: list[dict]) -> str:
    """Builds the prompt that asks the harness for the seller's next move.

    Args:
        item: The item under negotiation ("lamp", "chair" or "bookshelf").
        reserve: The lowest price the shop may accept for this item.
        history: Every message so far, oldest first, as
            {"role": "buyer" | "seller", "text": str, "price": int | float | None}.

    Returns:
        The full prompt for harness.ask().
    """
    raise NotImplementedError("write the seller prompt")


def parse_seller_decision(answer: str) -> dict:
    """Reads the seller's move from the harness's answer.

    Returns:
        {"action": "counter" | "accept" | "reject", "price": int | None, "message": str}

    Raises:
        ValueError: If the answer holds no readable decision.
    """
    raise NotImplementedError("write the seller answer parser")


# ---------------------------------------------------------------- plumbing


def log(line: str) -> None:
    print(line, flush=True)


def single_number(text: str):
    """Returns the one number in text, or None when there is none or several."""
    nums = {float(n) for n in re.findall(r"\d+(?:\.\d+)?", text)}
    if len(nums) != 1:
        return None
    n = nums.pop()
    return int(n) if n.is_integer() else n


class Seller(AgentExecutor):
    def __init__(self, cfg):
        self.cfg = cfg
        self.open = {}  # task id -> {"item", "ask", "history"}

    def decide(self, deal: dict, reserve: int, task_id: str):
        """Asks the harness for a move; tries twice, returns None if both fail."""
        prompt = build_seller_prompt(deal["item"], reserve, deal["history"])
        for attempt in (1, 2):
            try:
                answer = harness.ask(prompt, timeout=HARNESS_TIMEOUT)
                log(f"[{task_id[:8]}] harness answer #{attempt}:\n{answer}")
                return parse_seller_decision(answer)
            except (harness.HarnessError, ValueError) as e:
                log(f"[{task_id[:8]}] harness attempt {attempt} failed: {e}")
        return None

    def enforce(self, decision, deal: dict, offer, reserve: int):
        """Turns the harness's decision into a legal move: (action, price, message, notes)."""
        hold = deal["ask"] if deal["ask"] is not None else math.ceil(reserve * 1.3)
        if decision is None:
            return "counter", hold, "", ["no usable decision: hold the current ask"]
        action, price, message = (
            decision["action"],
            decision.get("price"),
            decision.get("message") or "",
        )
        notes = []
        if action == "accept":
            if offer is None or offer < reserve:
                return (
                    "counter",
                    hold,
                    message,
                    [f"accept without a valid offer ({offer}): hold {hold}"],
                )
            return "accept", offer, message, notes
        if action == "reject":
            return "reject", None, message, notes
        if price is None:
            return (
                "counter",
                hold,
                message,
                ["counter without a price: hold the current ask"],
            )
        if price < reserve:
            notes.append(f"counter {price} below reserve: raised to {reserve}")
            price = reserve
        if offer is not None and offer >= reserve and price <= offer:
            return (
                "accept",
                offer,
                message,
                notes + [f"counter {price} <= offer {offer}: accept the offer"],
            )
        return "counter", price, message, notes

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        up = TaskUpdater(
            event_queue=event_queue, task_id=task.id, context_id=task.context_id
        )
        say = lambda text: up.new_agent_message(parts=[new_text_part(text)])
        text = get_message_text(context.message) or ""
        tid = task.id
        deal = self.open.get(tid)

        if deal is None:  # first message: which item?
            item = next((k for k in self.cfg["reserve"] if k in text.lower()), None)
            if item is None:
                await up.reject(
                    say("We only sell: " + ", ".join(self.cfg["reserve"]) + ".")
                )
                return
            deal = self.open[tid] = {"item": item, "ask": None, "history": []}
        reserve = self.cfg["reserve"][deal["item"]]
        offer = single_number(text)
        deal["history"].append({"role": "buyer", "text": text, "price": offer})
        log(
            f"[{tid[:8]}] buyer: {text!r} (offer {offer}, ask {deal['ask']}, reserve {reserve})"
        )

        if offer is not None and deal["ask"] is not None and offer >= deal["ask"]:
            log(f"[{tid[:8]}] rule: offer {offer} meets ask {deal['ask']} -> accept")
            action, price, message = "accept", offer, ""
        else:
            decision = await asyncio.to_thread(self.decide, deal, reserve, tid)
            action, price, message, notes = self.enforce(decision, deal, offer, reserve)
            for n in notes:
                log(f"[{tid[:8]}] rule: {n}")
        log(f"[{tid[:8]}] move: {action} {price}")

        if action == "accept":
            receipt = {"seller": self.cfg["name"], "item": deal["item"], "price": price}
            await up.add_artifact(parts=[new_data_part(receipt)], name="receipt")
            await up.complete(
                say(f"{message} Deal: {deal['item']} at {price}.".strip())
            )
            del self.open[tid]
        elif action == "reject":
            await up.reject(say(message or "No deal."))
            del self.open[tid]
        else:  # counter: the kit reads the asking price as the last number of the message
            deal["ask"] = price
            reply = f"{message} Asking price: {price}.".strip()
            deal["history"].append({"role": "seller", "text": reply, "price": price})
            await up.requires_input(say(reply))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task
        self.open.pop(task.id, None)
        await TaskUpdater(
            event_queue=event_queue, task_id=task.id, context_id=task.context_id
        ).cancel()


def build(cfg):
    url = f"http://127.0.0.1:{cfg['port']}/"
    card = AgentCard(
        name=cfg["name"],
        description=cfg["description"],
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["application/json", "text/plain"],
        capabilities=AgentCapabilities(streaming=True),
        supported_interfaces=[
            AgentInterface(protocol_binding="JSONRPC", url=url, protocol_version="1.0")
        ],
        skills=[
            AgentSkill(
                id="negotiate",
                name="Negotiate a purchase",
                description=cfg["skill_description"],
                tags=["shopping", "furniture", "premium", "handmade"],
                examples=["I want to buy a lamp", "150"],
            )
        ],
    )
    handler = DefaultRequestHandler(
        agent_executor=Seller(cfg), task_store=InMemoryTaskStore(), agent_card=card
    )
    return Starlette(
        routes=create_agent_card_routes(card) + create_jsonrpc_routes(handler, "/")
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config")
    ap.add_argument("--port", type=int)
    args = ap.parse_args()
    cfg = dict(DEFAULT)
    if args.config:
        cfg.update(json.load(open(args.config)))
    if args.port:
        cfg["port"] = args.port
    uvicorn.run(build(cfg), host="127.0.0.1", port=cfg["port"], log_level="warning")
