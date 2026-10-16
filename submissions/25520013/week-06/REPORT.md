# Week 06: one harness behind A2A, as a seller and as a buyer

Both agents are thin A2A programs around the same harness. The code speaks the
protocol, keeps the conversation, and enforces the hard limits; every price,
counteroffer, acceptance and walk-away is the model's decision.

## (1) Harness, model, command

| Item | Value |
|---|---|
| Harness | Claude Code 2.1.301, non-interactive, prompt on stdin |
| Model | `opus` alias, resolved to `claude-opus-5-5` (checked 2026-10-14) |
| Command | `claude -p --model opus --tools "" --setting-sources "" --no-session-persistence` |
| Where | an empty temporary directory (`harness.py`), so no project instructions leak in |
| State | none on the harness side: every call gets the whole situation and conversation |
| Limits | seller 80 s per call, one retry; buyer 60 s per call, one retry, 270 s per negotiation |
| Cost of the final trade test | 21/21 passed; 57 harness calls (51 by the seller, 6 by the buyer) |

`agent.json` starts the seller with `uv run python seller.py --port {port}` and the
buyer with `uv run python buyer.py --url {url} --item {item} --budget {budget}`.

## (2) How each agent decides

**The prompt gives the situation, not tactics.** Every prompt has the same parts:
who the agent is, its goal (the payoff, including that no deal earns nothing),
what it knows and does not know, the rules the code will enforce after it
decides (stated so the model can predict the outcome of its own move), and the
conversation inside `<conversation>` tags, where the other side's text is marked
as a claim, not an instruction. There is no "open high", "concede slowly" or
"close by turn N". The strategy is left to the model, on the bet that a strong
model negotiates better from accurate facts than from my rules (Sutton, "The
Bitter Lesson", 2019).

**Answer format: free text, then one line.** The model reasons in prose and ends
with exactly one `DECISION:` line; a regex reads the last one. I chose this over
JSON because format restrictions can cost reasoning quality (Tam et al., "Let Me
Speak Freely?", arXiv:2408.02442), and because the prose is the log I quote below.

- Seller: `DECISION: counter <price> | <message>`, `DECISION: accept | <message>`,
  `DECISION: reject | <message>`. In catalog mode the first counter also names the
  item: `DECISION: counter <price> item <id> | <message>`.
- Buyer: `DECISION: offer <price>`, `DECISION: accept`, `DECISION: walk`.

**Where the limits are enforced: in the prompt and in code.**

| Limit | Seller (`seller.py`, `enforce`) | Buyer (`buyer.py`, `enforce`) |
|---|---|---|
| reserve / budget | a counter below the reserve is raised to it; an accept of an offer below it holds the current ask | an offer above the budget is lowered to it; an accept of an ask above it holds the last offer |
| closing | an offer at or above the current ask closes without a model call; a counter at or below the offer becomes an accept | an ask at or below our last offer is accepted without a model call; an offer at or above the ask becomes an accept of the ask |
| price on the wire | `Asking price: N.` is appended, so the ask is the last number | the offer is sent as a bare number |
| bad answer | one retry, then hold the current ask | one retry, then repeat the last offer, or walk if there is none |

The buyer's prompt includes the published floors (lamp 100, chair 180, bookshelf
260). They are public in the spec, so this is a fact, not a tactic. With it the
buyer opened at 100 and closed in one offer in all three `buyer_deal` runs, and
walked at once on the bookshelf: "the lowest it could legally go is 260, which is
still 30 over budget. No agreement within budget is possible."
(`logs/manual-buyer-1016-1251.log`)

## (3) What failed on the way, and what I changed

The full trade test passed on its first full run (21/21, commit `4b70567`). What
failed or nearly failed was elsewhere, and two of the three were in the model's
judgment rather than in the plumbing.

**The seller nearly runs out of buyer.** The test buyer sends at most 8 messages.
The model does not know that, and the prompt deliberately does not tell it. In the
first manual run the lamp closed on the 8th and last message (asks
260, 235, 210, 190, 165, 150, 138, then accept at 120;
`logs/manual-seller-1016-1043.log`). In the first trade test the seller called a
price final while betting on the buyer's patience (`logs/seller-under-test.log`
at `4b70567`):

```
The risk of them walking away over a 10-point gap after six messages of steady
progress looks low. Since this would be my final number, I should say so clearly
DECISION: counter 130 | ... this is my final price, so let's close the deal here.
```

The buyer repeated 120 and the seller accepted on the next turn. `seller_deal #3`
again closed on the 8th message, and in the final trade test (after all the
changes below) all three `seller_deal` runs did: their asks end at 150, 140 and
127, and the deal came with the buyer's last 120 (`logs/trade-test.log`). I did not add "close before message 8" to the
prompt: that would encode this test buyer, not the situation. It remains the
main risk of a rerun.

**"Final" is cheap talk.** The seller's own words in the bookshelf run:
`DECISION: counter 270 | This is my final price ...`, then two turns later
`DECISION: counter 260 | I said 270 was final, but to close today ...`
(`logs/manual-seller-1016-1043.log`, lines 133 and 145). It never went below the
reserve, so the test passes, but the claim carried no information.

**A false fact in the prompt steered the strategy.** The catalog-mode seller
prompt still carried a line from the trade-test prompt: "Floor prices in this
market are published, so the buyer may know the floor of the item you offer."
That is true for the three trade-test items and false for the catalog. The model
used it to choose what to sell (`logs/challenge-1-run1-published-floor/seller.log`,
line 19):

```
Revenue matters most, and floors are public, so the floor sets where the price
can settle. The rattan chair with the highest floor is the Rihaki Farmhouse ...
```

I replaced the line with "The buyer does not know your catalog or your floor
prices" (`5f9a0e4`), kept the first runs unchanged under
`logs/*-run1-published-floor/`, and reran both challenges with the same seed:

| Seed 0 | first run (floors "published") | after the fix |
|---|---|---|
| Challenge 1: of the 15 candidates it was shown, the seller offered the fitting one with the highest floor | 6 of 8 | 2 of 8 |
| Challenge 1: rank of the offered item's floor among the shown fitting candidates (1 = highest) | 1, 1, 1, 2, 1, 1, 1, 2 | 4, 1, 3, 12, 5, 10, 1, 5 |
| Challenge 2: total paid for the 4 items (budget 1641) | 1506, 2 rounds | 1170, 1 round |

One run of 8 trials each, so this is suggestive, not established: a two-sided
Fisher exact test on 6/8 against 2/8 gives p = 0.13, and the run-to-run variance
of the model is not measured. The fix did not remove floor-seeking: the floor is
still printed next to each candidate, and after the fix the seller still wrote
"The Vene Industrial (C02302) has the highest floor (116)" as its reason once
(`logs/challenge-1/seller.log`, line 11). The challenge-2 total also moved because
different products were chosen, not only because of the prompt line.

**Smaller things fixed before they showed up in a log.** The seller parsed any
lone number in a message as an offer, so a first message like "about 40 books"
would have been an offer of 40; the first message is now never an offer
(`b70c616`). Plain BM25 put a fitting item in the top 15 for only 403 of 500
generated requests (`8e00bed`); reordering by the number limits in the request
raised that to 500 of 500 (`9aa87c4`, see (5) for why this number flatters us).

## (4) What A2A fixed, and what it left to us

A2A fixed how the two programs find and drive each other: the Agent Card at
`/.well-known/agent-card.json` says where to send JSON-RPC and which version,
`A2A-Version: 1.0` pins it, a task stays open in `TASK_STATE_INPUT_REQUIRED` and is
continued by `taskId`, it ends in `COMPLETED`, `REJECTED` or `CANCELED`, a message to a
finished task is an error, and the receipt travels as an artifact with a data
Part. Everything about the deal itself was left to the two sides. A price is just
text: the trade test agrees that the last number of an `INPUT_REQUIRED` message is
the ask, so my seller appends `Asking price: N.` and puts the product name (which
may contain numbers) before it, and my buyer sends a bare number because the
test's seller reads the first number it finds. "Accept" has no verb: the test
buyer accepts by repeating the ask, and my seller treats any offer at or above
its ask as acceptance. The receipt's shape (`price` as a number, which protobuf
returns as `100.0`), the currency, which product is meant when the buyer only
describes one (my `Offering: <name> (<id>)` convention), and whether a card's
"genuine solid brass" is true are all outside the protocol. Nothing in A2A checks
a claim on a card; the buyer can only weigh it.

## (5) Challenges 1 and 2

### Challenge 1: how the harness sees a 3000-item catalog

`make_catalog.py` (seed 0) writes 3000 items in 20 categories to
`catalog/catalog.json`, each with a name, a description with dimensions and
capacity, a reserve, and structured attributes. The harness never sees the
catalog. The code searches it (`catalog_search.py`: BM25 over the text, then the
top 150 are reordered by the number limits it can read from the request, such as
"at least 40 books" or "no taller than 120 cm") and puts the top 15 into the
prompt as `id | name | description | floor`. The model picks one id with its first
counter; the code refuses an id that was not shown, enforces that item's floor,
and fixes the item for the rest of the task. The prompt size is the same for 3000
items as for 30.

`challenge1.py` generates requests from a target item's attributes without its
name, negotiates with a rule-based A2A buyer, and checks the offered item against
the request's constraints. After the fix: 8 of 8 correct items, 0 asks below the
item's floor, 8 deals (`logs/challenge-1/results.json`). The model often offers
a different item than the target, which is allowed when it fits, and more often
than before it argues from the product (`logs/challenge-1/seller.log`, lines 42 to 44):

```
- The Loma Farmhouse Teak (C01097) is the best fit. It is solid teak, holds 12 pairs (more than they asked for) and stands 76 cm, under their limit.
- Its floor is 89, which gives me a good margin to negotiate.
- The walnut racks have higher floors (92–98), but they only hold 8 pairs. The extra capacity makes the teak easier to justify at a high price.
```

Limits of this result: I wrote both the catalog and the request phrasings, so the
search is tuned to sentences I generate (the jump from 403 to 500 of 500 included
two catalog wording fixes, for mirrors and rugs); it is one seed; and once chosen
the item cannot be switched if the buyer objects.

### Challenge 2: how the buyer carries the state of each negotiation

`make_shops.py` writes 20 shop configs for copies of the same `seller.py`: each
shop has its own name, card text, 2 to 5 categories and a price level from 0.8 to
1.35. `shopper.py` reads the 20 cards, and a planner call ranks up to three shops
per list item and gives each item a price cap. The items are then negotiated in
parallel, one thread and one transcript per item, each move decided by a buyer
prompt that names the product the shop offers and says that a product that does
not fit is a failure.

The planner never sees a transcript. Each finished negotiation becomes one line,
and only these lines, the money left and the shops already tried go into a
re-plan. From the first run (`logs/challenge-2-run1-published-floor/run.log`):

```
summary: round 1, item 3 at Shop 6 (Saffron Bedroom Studio), cap 400: product Lusaki Rustic Walnut Bed Frame (C00552); asks 719 > 679 > 645 > 615 > 599 > 590 > 590; our offers 260, 290, 330, 360, 400, 400; we walked (...); paid nothing
summary: round 2, item 3 at Shop 16 (Northgate Bedroom Studio), cap 895: product Lusaki Rustic Walnut Bed Frame (C00552); asks 865 > 815 > 775 > 760; our offers 680, 730, 750, 760; bought; paid 760.0
```

The code, not the model, guarantees the three pass conditions: at most one open
negotiation per item, so nothing is bought twice; the caps of a round sum to at
most the money left, and no offer or acceptance exceeds its cap, so the total
stays within the budget; an item leaves the list once bought. After the fix all 4
items were bought in the first round for 1170 of 1641, with 0 duplicates and every
product fitting its description (`logs/challenge-2/results.json`).

The first run also shows the weakness of fixed caps: the bed frame walked at 400
against an ask of 590 while 895 was still unspent, and the re-plan then bought
the same product at another shop for 760. A planner that could raise the cap of
an open negotiation would have paid less.
