"""What the pipeline made of a file: no truth needed, so it runs on real drawings.

Details found, regions per detail, the share named and by which source, what a person would
have to do per detail, and what carrying names between details saves. The report says what
it can and cannot know: with no truth it counts proposals, it does not score them.

Its output quotes the drawing's text, so it stays under the store with everything else.
"""
from __future__ import annotations

import statistics
from collections import Counter

from . import names, propagate, sheets


def _reason(text: str) -> str:
    return text.split(":")[0].split(" in detail")[0][:48]


def _keep(region) -> bool:
    return not region["dropped"]


def _same_word(record, region) -> str:
    """A stand-in reviewer for counting: every region gets the same word, so a carried name
    is never wrong and only the number of near-identical partners shows."""
    return "named"


def _hatch_word(record, region) -> str:
    """A stand-in reviewer who gives each hatch and shape one class: the best case for
    groups, for as long as the owner's classes do line up with them."""
    key = names.hatch_key(region["hatch"])
    kind = key or (names.SOLID,) if region["hatch"] else (region["id"], record["sheet_id"])
    return repr((kind, region["thin"]))


def _unmapped(records) -> list:
    words = {}
    for record in records[:1]:
        for entry in record.get("legend") or []:
            if entry["text"] not in words:
                words[entry["text"]] = entry["pattern"]
    return [{"text": text, "pattern": pattern} for text, pattern in words.items()]


def build(store, file_id=None) -> dict:
    ids = [s for s in sheets.sheet_ids(store) if file_id is None or s.rsplit("-d", 1)[0] == file_id]
    records = [sheets.read_sheet(store, s) for s in ids]
    live = [[r for r in record["regions"] if _keep(r)] for record in records]
    every = [r for regions in live for r in regions]
    counts = [len(k) for k in live]
    sources = Counter(r["proposal"]["source"] or "none" for r in every)
    total = len(every) or 1
    framed = [r["source"]["detail"] for r in records if r["source"].get("detail", {}).get("framed")]
    alone = propagate.simulate(records, _same_word, by_hatch=False)
    grouped = propagate.simulate(records, _hatch_word, by_hatch=True)
    details = len(records) or 1
    return {
        "details": len(records),
        "framed": len(framed),
        "frames_holding_several": len({d["frame"] for d in framed if d.get("groups_inside", 1) > 1}),
        "frames_split": len({d["frame"] for d in framed if d.get("split")}),
        "legend_rows": _unmapped(records),
        "regions": len(every),
        "regions_per_detail": {"mean": round(statistics.fmean(counts), 1) if counts else 0,
                               "median": statistics.median(counts) if counts else 0, "max": max(counts, default=0)},
        "thin": sum(1 for r in every if r["thin"]),
        "kinds": Counter(r["kind"] for r in every).most_common(),
        "hatches": Counter((r["hatch"] or {}).get("pattern") or "none" for r in every).most_common(),
        "named_by_source": {k: round(v / total, 4) for k, v in sources.most_common()},
        "named": sum(1 for r in every if r["proposal"]["class_id"] != names.UNKNOWN),
        "carrying_sheet_words": sum(1 for r in every if r["proposal"]["verbatim"]),
        "classes": Counter(r["proposal"]["class_id"] for r in every).most_common(),
        "set_aside": Counter(_reason(r["dropped"]) for record in records for r in record["regions"]
                             if r["dropped"]).most_common(),
        "seconds_per_detail": round(statistics.fmean(r.get("seconds_to_propose", 0.0) for r in records), 3) if records else 0,
        "reviewer": {
            "picks_per_detail_alone": round(alone["picks_without"] / details, 1),
            "picks_per_detail_near_identical": round(alone["picks_with"] / details, 1),
            "picks_saved_near_identical": alone["picks_without"] - alone["picks_with"],
            "share_saved_near_identical": round(1 - alone["picks_with"] / max(alone["picks_without"], 1), 3),
            "details_needing_no_pick": alone["details_no_pick"],
            "picks_per_detail_with_groups_best_case": round(grouped["picks_with"] / details, 1),
            "share_saved_with_groups_best_case": round(1 - grouped["picks_with"] / max(grouped["picks_without"], 1), 3),
        },
        "note": ("Counts of proposals on a drawing with no truth. Near-identical is measured by shape and place, and "
                 "whether a carried name is right is not known until a person checks. The groups figure assumes each "
                 "hatch and shape is one class, which is a best case."),
    }


def text(report: dict) -> str:
    r = report["reviewer"]
    per = report["regions_per_detail"]
    lines = [f"{report['details']} details ({report['framed']} framed, {report['frames_holding_several']} frames hold several, {report['frames_split']} split), "
             f"{report['regions']} regions: {per['mean']} per detail, median {per['median']}, most {per['max']}",
             f"thin {report['thin']}, with a class {report['named']}, carrying words from the sheet {report['carrying_sheet_words']}",
             f"named by source: {report['named_by_source']}",
             f"set aside: {report['set_aside']}",
             f"seconds per detail: {report['seconds_per_detail']}",
             f"picks per detail: {r['picks_per_detail_alone']} alone, {r['picks_per_detail_near_identical']} with near-identical "
             f"names carried ({r['share_saved_near_identical']:.0%} saved, {r['details_needing_no_pick']} details need none), "
             f"{r['picks_per_detail_with_groups_best_case']} with hatch groups at best",
             report["note"]]
    return "\n".join(lines)
