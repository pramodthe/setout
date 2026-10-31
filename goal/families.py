"""What kind of component a piece is, from the words the model carries.

Node names are tried first and material names second, because a material is often shared by unrelated
parts. Most pieces have neither, and those stay `unknown`: the judge never guesses a family.
"""
from __future__ import annotations

import re

# Order matters: the first family with a matching word wins.
WORDS = (
    ("fixing", r"\bnut\b|hex.?nut|hvy.?hx|threaded.?rod|screw|\bbolt|\bnail|anchor|\bwasher|term.?bar|\bshim"),
    ("reinforcement", r"joint.?wire|wire|\brebar|\breinforc"),
    ("weep", r"weep"),
    ("sealant", r"caulk|sealant|backer.?rod|gasket"),
    ("flashing", r"flashing|drip|outside.?corner|inside.?corner|dampproof|copper|\bangle\b|stainless|stailnless"),
    ("drainage", r"mortar.?net|cavclear|drain|gravel"),
    ("mortar", r"bed.?joint|head.?joint|mortar|\bgrout"),
    ("brick", r"brick|bryce"),
    ("block", r"\bcmu\b|block|\bsash"),
    ("insulation", r"insulat|rock.?wool|\bxps\b|\bspf\b|foam"),
    ("glass", r"glass|window"),
    ("tile", r"tile|porcelain|terrazzo|mosaic"),
    ("board", r"cement.?board|\bcbu\b|plywood|gypsum|drywall"),
    ("thinset", r"thinset"),
    ("timber", r"wood|timber|\bstud\b|endgrain"),
    ("metal", r"metal|aluminum|steel"),
    ("concrete", r"concrete|\bconc\b|foundation|\bslab"),
    ("stone", r"limestone|stone|marble|botticino"),
    ("plaster", r"plaster|stucco"),
)
FAMILIES = tuple(name for name, _ in WORDS) + ("unknown",)
_compiled = [(name, re.compile(words, re.I)) for name, words in WORDS]


def family_of(names, materials=()) -> str:
    for texts in (names, materials):
        for text in texts:
            spaced = re.sub(r"[_\-]+", " ", text or "")
            for family, words in _compiled:
                if words.search(spaced):
                    return family
    return "unknown"


def spread(pieces, purity=0.7) -> list[str]:
    """Each piece's family, reading the model's own facts further. A piece with no words takes the
    family of named pieces with its exact shape, then of named pieces sharing its main material, when
    at least `purity` of those agree. No opinion enters: a model that names nothing stays unknown."""
    named = [family_of(p["path"][::-1], sorted(p["materials"], key=p["materials"].get, reverse=True)) for p in pieces]

    def votes(key_of):
        tally: dict = {}
        for piece, family in zip(pieces, named):
            if family != "unknown":
                tally.setdefault(key_of(piece), {}).setdefault(family, 0)
                tally[key_of(piece)][family] += 1
        chosen = {}
        for key, counts in tally.items():
            best = max(counts, key=counts.get)
            if counts[best] / sum(counts.values()) >= purity:
                chosen[key] = best
        return chosen

    main_material = lambda piece: max(piece["materials"], key=piece["materials"].get, default="")  # noqa: E731
    by_shape, by_material = votes(lambda piece: piece["shape"]), votes(main_material)
    return [family if family != "unknown" else by_shape.get(piece["shape"]) or by_material.get(main_material(piece)) or "unknown"
            for piece, family in zip(pieces, named)]
