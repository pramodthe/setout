"""Of a model's parts, which does a section reach, and what could recover the rest? Training models only.

    python -m goal.missing
"""
from __future__ import annotations

from collections import Counter

from .baseline import cut, held_out
from .families import family_of
from .trial import DEV


def key(piece):
    return tuple(round(s * 1000 / 5) for s in piece["sizes"])  # within about 5 mm


def main():
    assert not set(DEV) & set(held_out())
    totals = Counter()
    for name in DEV:
        pieces, _, axis, _, cuts = cut(name)
        seen = {index for index, _ in cuts}
        shapes = {key(pieces[i]) for i in seen}
        tally, families = Counter(), Counter()
        for index, piece in enumerate(pieces):
            family = family_of(piece["path"][::-1], sorted(piece["materials"], key=piece["materials"].get, reverse=True))
            kind = "cut" if index in seen else "repeat of a cut shape" if key(piece) in shapes else "never shown"
            tally[kind] += 1
            if kind == "never shown":
                families[family] += 1
        totals.update(tally)
        print(f"{name[:20]:20} {len(pieces):5} pieces: " + ", ".join(f"{k} {v / len(pieces):.0%}" for k, v in tally.most_common()) + f"  | never shown: {families.most_common(6)}", flush=True)
    all_pieces = sum(totals.values())
    print("all:", {k: f"{v / all_pieces:.1%}" for k, v in totals.most_common()})


if __name__ == "__main__":
    main()
