"""Narrows a large catalog to a few candidates for the harness: BM25 in pure Python.

    uv run python catalog_search.py "something wooden to keep about 40 books"

The seller never pastes the whole catalog into a prompt. It searches the
buyer's description over each item's name, description and attributes, and
shows the harness only the top few items. Numbers in the query do not rank
items (BM25 matches words); the harness reads the numbers in the candidates.
"""

import json
import math
import re
import sys
from collections import Counter

K1, B = 1.5, 0.75
STOP = set(
    "a an the and or of for to in on at by with my me i we our is are be it that this "
    "something some about not no than more less at least most up need want looking".split()
)
SYNONYMS = {"wooden": "wood", "metallic": "metal", "wicker": "rattan", "couch": "sofa"}


def tokens(text: str) -> list[str]:
    """Lower-cased words without stop words; a crude plural strip and a few synonyms."""
    out = []
    for w in re.findall(r"[a-z]+", text.lower()):
        if w in STOP:
            continue
        w = SYNONYMS.get(w, w)
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return out


def item_text(item: dict) -> str:
    attrs = " ".join(str(v) for v in item.get("attrs", {}).values())
    return f"{item['name']} {item['description']} {attrs}"


class Index:
    """BM25 index over a list of catalog items."""

    def __init__(self, items: list[dict]):
        self.items = items
        self.docs = [Counter(tokens(item_text(it))) for it in items]
        self.lens = [sum(d.values()) for d in self.docs]
        self.avg = sum(self.lens) / len(self.lens)
        df = Counter(w for d in self.docs for w in d)
        n = len(items)
        self.idf = {w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()}

    def search(self, query: str, k: int = 15) -> list[dict]:
        """Returns the top-k items with a positive score, best first."""
        q = set(tokens(query))
        scored = []
        for i, (d, ln) in enumerate(zip(self.docs, self.lens)):
            s = 0.0
            for w in q:
                f = d.get(w)
                if f:
                    s += (
                        self.idf[w]
                        * f
                        * (K1 + 1)
                        / (f + K1 * (1 - B + B * ln / self.avg))
                    )
            if s > 0:
                scored.append((s, i))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [self.items[i] for _, i in scored[:k]]


_INDEXES: dict[int, Index] = {}


def search(items: list[dict], query: str, k: int = 15) -> list[dict]:
    """Top-k items for a free-text query; the index is built once per item list."""
    if id(items) not in _INDEXES:
        _INDEXES[id(items)] = Index(items)
    return _INDEXES[id(items)].search(query, k)


def load(path: str) -> list[dict]:
    with open(path) as f:
        return json.load(f)


if __name__ == "__main__":
    for it in search(load("catalog/catalog.json"), " ".join(sys.argv[1:])):
        print(f"{it['id']}  {it['reserve']:>5}  {it['name']}  |  {it['description']}")
