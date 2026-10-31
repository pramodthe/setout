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
