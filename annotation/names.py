"""Proposed names for regions, from what the sheet itself prints.

A callout gives the region its leader tip lands on the text verbatim, mapped to a class
through the ontology's aliases. A hatch of the same pattern and shape, anywhere in the file,
gets the class a callout gave one of them. A legend gives a hatch a meaning for the whole
file: its text stays verbatim and maps through the aliases too. `propagate.py` adds a fourth
source, what a person confirmed on a near-identical detail.

No match stays `unknown`: nothing here guesses, and nothing calls a model. Class names come
only from ontology data; the only English in this file is function words that end a noun
phrase.

Every proposal keeps the verbatim text, the reason and its state:
`proposed`, then `confirmed` or `corrected` by a person.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

from . import planar

UNKNOWN = "unknown"
PROPOSED, CONFIRMED, CORRECTED = "proposed", "confirmed", "corrected"
CALLOUT, HATCH, LEGEND, PROPAGATED = "callout", "hatch", "legend", "propagated"
SOLID = "SOLID"
MAX_LABEL_WORDS = 5  # text lying inside a region is its label only if it is this short
# A callout names its subject first and what it sits on after, so the words before one of
# these are read first, and the whole text only when those match nothing.
PHRASE_ENDS = {"on", "over", "under", "to", "with", "at", "from", "behind", "above", "below", "against",
               "onto", "into", "in", "by", "beneath", "around", "through", "for", "of"}


def tokens(text: str) -> list[str]:
    """Lower-case words: hyphens and slashes split, a plural s is dropped."""
    words = re.findall(r"[a-z0-9]+", text.casefold().replace("&", " and "))
    return [w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w for w in words]


@dataclass
class AliasIndex:
    """Ontology aliases as word tuples. Only data: a new trade is new YAML."""

    phrases: dict[tuple, set] = field(default_factory=dict)
    longest: int = 1

    @classmethod
    def of(cls, onto) -> AliasIndex:
        index = cls()
        for class_id in onto.component_ids():
            for alias in onto.component(class_id).aliases:
                words = tuple(tokens(alias))
                if words:
                    index.phrases.setdefault(words, set()).add(class_id)
        index.longest = max((len(p) for p in index.phrases), default=1)
        return index

    def spans(self, words) -> list:
        """(start, end, class ids, alias) of every alias in the words, longest kept: an
        alias inside a longer alias does not count, so two-word names beat their second word."""
        found = []
        for start in range(len(words)):
            for size in range(min(self.longest, len(words) - start), 0, -1):
                phrase = tuple(words[start:start + size])
                if phrase in self.phrases:
                    found.append((start, start + size, self.phrases[phrase], " ".join(phrase)))
        return [s for s in found if not any(o is not s and o[0] <= s[0] and s[1] <= o[1]
                                            and (o[1] - o[0]) > (s[1] - s[0]) for o in found)]


@dataclass
class Match:
    class_id: str  # a class id, or UNKNOWN
    alias: str | None = None
    candidates: list = field(default_factory=list)  # classes the words point to when they disagree


def match_text(text: str | None, index: AliasIndex) -> Match:
    """The class a text names, or UNKNOWN. Two classes in play is UNKNOWN too, with both
    listed: a person picks in one key, and nothing is guessed."""
    if not text or not text.strip():
        return Match(UNKNOWN)
    words = tokens(text)
    subject = []
    for word in words:
        if word in PHRASE_ENDS:
            break
        subject.append(word)
    for scope in (subject, words):
        spans = index.spans(scope)
        if not spans:
            continue
        classes = sorted({c for span in spans for c in span[2]})
        if len(classes) == 1:
            return Match(classes[0], alias=spans[0][3], candidates=classes)
        return Match(UNKNOWN, candidates=classes)
    return Match(UNKNOWN)


def hatch_key(hatch: dict | None) -> tuple | None:
    """Same pattern, scale and angle is the same hatch. A solid fill has no pattern."""
    if not hatch or not hatch["pattern"] or hatch["pattern"].upper() == SOLID:
        return None
    return (hatch["pattern"].upper(), round(hatch["scale"], 6), round(hatch["angle"], 3))


def group_key(region, thin_width) -> tuple | None:
    """Pattern plus shape. Two hatches with one pattern are one material only if they have
    the same kind of shape. A thin solid fill is a heavy line, which does repeat, so it gets
    a group of its own; a compact one says nothing about what it fills."""
    thin = region.width < thin_width
    key = hatch_key(region.hatch)
    if key is not None:
        return (*key, thin)
    if region.hatch and thin:
        return (SOLID, 1.0, 0.0, True)
    return None


def _landing(tip, toward, regions, reach, touch) -> tuple:
    """(region, how) a leader lands on, or (None, why).

    A tip clearly inside a region lands there, however thin the region. A tip on an
    outline (within `touch`) lands on what lies just ahead, because a leader stops at the
    edge of the part it points to. A tip in open space takes the nearest region within
    `reach`. Single lines come first: a leader drawn to a line means the line.
    """
    tip = np.asarray(tip, dtype=float)
    direction = tip - np.asarray(toward, dtype=float)
    length = float(np.hypot(*direction))
    direction = direction / length if length > 0 else np.zeros(2)
    live = [r for r in regions if not r.dropped]
    point = tip.reshape(1, 2)
    for region in live:
        if region.kind == "stroke" and planar.points_in_rings(region.rings, point)[0]:
            return region, "leader tip on the line"
    holding = [r for r in live if planar.points_in_rings(r.rings, point)[0]]
    inside = min(holding, key=lambda r: r.area) if holding else None
    if inside is not None and float(planar.distance_to_rings(inside.rings, point)[0]) > touch:
        return inside, "leader tip inside the region"
    ahead = (tip + direction * touch * 2.0).reshape(1, 2)
    forward = [r for r in live if planar.points_in_rings(r.rings, ahead)[0]]
    if forward:
        return min(forward, key=lambda r: r.area), "leader tip on an outline, read just ahead"
    if inside is not None:
        return inside, "leader tip inside the region"
    distances = [(float(planar.distance_to_rings(r.rings, point)[0]), k) for k, r in enumerate(live)]
    if distances and min(distances)[0] <= reach:
        return live[min(distances)[1]], "leader tip beside the region"
    return None, "leader tip lands in no region"


def blank(region_id: str) -> dict:
    return {"class_id": UNKNOWN, "source": None, "state": PROPOSED, "verbatim": [], "reason": "no callout or hatch names it",
            "candidates": [], "callouts": []}


def propose(regions, callouts, onto, tolerance, texts=()) -> tuple[dict, list]:
    """({region id: proposal}, callout records). `tolerance` is the sheet's: a tip may
    miss a region by `near`, and counts as on an outline within a few `eps`. Callouts win
    over hatches: they are written about that very part. Short text lying inside a region,
    with no leader, is read last, as a label on that region."""
    index = AliasIndex.of(onto)
    proposals = {r.id: blank(r.id) for r in regions}
    records = []
    for number, callout in enumerate(callouts):
        region, how = _landing(callout.tip, callout.toward, regions, tolerance.near, tolerance.eps * 4.0)
        match = match_text(callout.text, index)
        record = {"index": number, "text": callout.text, "tip": [float(v) for v in callout.tip], "kind": callout.kind,
                  "leader": callout.leader, "note": callout.note, "space": callout.space,
                  "region": region.id if region else None, "landing": how, "class_id": match.class_id,
                  "alias": match.alias, "candidates": match.candidates}
        records.append(record)
        if region is None or not callout.text:
            continue
        proposal = proposals[region.id]
        proposal["verbatim"].append(callout.text)
        proposal["callouts"].append(number)
    for region_id, proposal in proposals.items():
        if not proposal["callouts"]:
            continue
        rows = [records[k] for k in proposal["callouts"]]
        named = sorted({r["class_id"] for r in rows if r["class_id"] != UNKNOWN})
        if len(named) == 1:
            first = next(r for r in rows if r["class_id"] == named[0])
            proposal.update(class_id=named[0], source=CALLOUT, candidates=named,
                            reason=f"{first['landing']}; text matches alias '{first['alias']}'")
        elif len(named) > 1:
            proposal.update(candidates=named, reason="callouts on this region name different classes")
        else:
            candidates = sorted({c for r in rows for c in r["candidates"]})
            proposal.update(candidates=candidates, reason="the callout names no single class"
                            if candidates else "the callout matches no alias")
    _labels_inside(regions, proposals, texts, {c.note for c in callouts}, index)
    return proposals, records


def _labels_inside(regions, proposals, texts, linked, index) -> None:
    live = [r for r in regions if not r.dropped]
    for text in texts:
        if text.handle in linked or text.box is None or not 0 < len(tokens(text.text)) <= MAX_LABEL_WORDS:
            continue
        middle = ((text.box[0] + text.box[2]) / 2.0, (text.box[1] + text.box[3]) / 2.0)
        holding = [r for r in live if r.kind != "stroke" and planar.points_in_rings(r.rings, [middle])[0]]
        if not holding:
            continue
        region = min(holding, key=lambda r: r.area)
        proposal = proposals[region.id]
        if proposal["class_id"] != UNKNOWN:
            continue
        match = match_text(text.text, index)
        proposal["verbatim"].append(text.text)
        if match.class_id != UNKNOWN:
            proposal.update(class_id=match.class_id, source=CALLOUT, candidates=[match.class_id],
                            reason=f"text inside the region, no leader; matches alias '{match.alias}'")
        else:
            proposal.update(candidates=match.candidates, reason="text inside the region, which names no single class"
                            if match.candidates else "text inside the region, which matches no alias")


def spread(analyses, legend, onto, thin_width) -> None:
    """What a file says about itself, shared across its details: first the same hatch and
    shape as a region a callout named, then the legend. `analyses` have `.result.regions`
    and `.proposals`, and `.detail` (None for a whole sheet)."""
    rows = [(a, r) for a in analyses for r in a.result.regions if not r.dropped]
    _from_hatches(rows, thin_width)
    if legend:
        _from_legend(rows, legend, AliasIndex.of(onto))


def _label(analysis, region) -> str:
    return f"{region.id} in detail {analysis.detail.index}" if analysis.detail else region.id


def _from_hatches(rows, thin_width) -> None:
    groups: dict[tuple, list] = {}
    for analysis, region in rows:
        key = group_key(region, thin_width)
        if key:
            groups.setdefault(key, []).append((analysis, region))
    for key, members in groups.items():
        named = {}
        for analysis, region in members:
            proposal = analysis.proposals[region.id]
            if proposal["source"] == CALLOUT:
                named.setdefault(proposal["class_id"], (analysis, region))
        if len(named) != 1:
            continue  # nothing to copy, or callouts on this hatch disagree: guess nothing
        class_id, (donor_analysis, donor) = next(iter(named.items()))
        words = donor_analysis.proposals[donor.id]["verbatim"][0]
        shape = "thin" if key[3] else "compact"
        for analysis, region in members:
            proposal = analysis.proposals[region.id]
            if proposal["class_id"] == UNKNOWN:
                proposal.update(class_id=class_id, source=HATCH, candidates=[class_id],
                                reason=f"same hatch ({key[0]}, scale {key[1]:g}, angle {key[2]:g}, {shape}) as "
                                       f"{_label(donor_analysis, donor)}, named by callout '{words}'")


def _from_legend(rows, legend, index) -> None:
    """A swatch's meaning applies only to its exact hatch: a pattern can be used at another
    scale for something else, and a solid fill is no pattern at all. The text stays on the
    proposal even when no class matches, so a person sees what the sheet calls it."""
    table: dict[tuple, list] = {}
    for entry in legend:
        key = hatch_key({"pattern": entry.pattern, "scale": entry.scale, "angle": entry.angle}) if entry.pattern else None
        if key and entry.text not in table.setdefault(key, []):
            table[key].append(entry.text)
    for analysis, region in rows:
        words = table.get(hatch_key(region.hatch))
        proposal = analysis.proposals[region.id]
        if not words or proposal["class_id"] != UNKNOWN:
            continue
        matches = [match_text(w, index) for w in words]
        classes = sorted({m.class_id for m in matches})
        proposal["verbatim"] = list(proposal["verbatim"]) + [w for w in words if w not in proposal["verbatim"]]
        shown = " / ".join(f"'{w}'" for w in words)
        if len(classes) == 1 and classes[0] != UNKNOWN:
            proposal.update(class_id=classes[0], source=LEGEND, candidates=classes,
                            reason=f"the legend calls this hatch {shown}; text matches alias '{matches[0].alias}'")
        else:
            known = [c for c in classes if c != UNKNOWN]
            proposal.update(candidates=known, reason=f"the legend calls this hatch {shown}, which names no single class")
