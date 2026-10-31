"""Retrieve by the drawn shape itself, with no model call: how well two sections' lines overlap at their best
placement. A language model names unlabelled lines unreliably, and two near-twins overlap whatever they are called.

    python -m goal.retrieve_shape
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image

from . import find_assembly as fa
from .retrieve_test import FIND, HIDE, NOTHING, OUT, code, picture_of

GRID = 256  # the frame two sections are compared in
FIT = 150  # pixels: a section's longest side, inside the frame
SIZES = (0.7, 0.84, 1.0, 1.19, 1.42)  # sections are cropped differently, so sizes are tried
SOFT = 3  # blur passes: lines a few pixels apart still count as the same line


def signature(path, size=1.0, flip=False) -> np.ndarray:
    image = fa.trimmed(path).convert("L")
    scale = FIT * size / max(image.size)
    image = image.resize((max(round(image.width * scale), 1), max(round(image.height * scale), 1)), Image.LANCZOS)
    ink = 1 - np.asarray(image, float) / 255
    ink = ink[:, ::-1] if flip else ink
    frame = np.zeros((GRID, GRID))
    frame[:ink.shape[0], :ink.shape[1]] = ink
    for _ in range(SOFT):
        frame = (frame * 2 + np.roll(frame, 1, 0) + np.roll(frame, -1, 0) + np.roll(frame, 1, 1) + np.roll(frame, -1, 1)) / 6
    return frame / max(np.linalg.norm(frame), 1e-9)


def spectra(path) -> list:
    return [np.conj(np.fft.rfft2(signature(path, size, flip))) for size in SIZES for flip in (False, True)]


def score(query, candidate_spectra) -> float:
    """The best overlap, 0 to 1, over placements, sizes and a mirror."""
    return max(float(np.fft.irfft2(query * spectrum, (GRID, GRID)).max()) for spectrum in candidate_spectra)


def ranked(query_name, names, library) -> list:
    query = np.fft.rfft2(signature(picture_of(query_name)))
    return sorted(((score(query, library[n]), n) for n in names if n != query_name), reverse=True)


def main():
    names = fa.catalogue()
    queries = FIND + NOTHING + HIDE
    with ThreadPoolExecutor(6) as pool:
        library = dict(zip(names, pool.map(lambda n: spectra(picture_of(n)), names)))
    # the cut-off comes from the catalogue alone, before any test query is scored
    same, other = [], []
    for name in names:
        hits = ranked(name, names, library)
        family = [s for s, n in hits if code(n) == code(name)]
        if family:
            same.append(family[0])
        other.append(next(s for s, n in hits if code(n) != code(name)))
    gaps = sorted(set(same + other))
    cut = max(gaps, key=lambda c: sum(s >= c for s in same) + sum(s < c for s in other))
    print(f"catalogue: {len(same)} with a relative, best relative median {np.median(same):.3f}, best stranger median {np.median(other):.3f}, cut-off {cut:.3f}", flush=True)
    results = {}
    for q in queries:
        pool_names = [n for n in names if not (q in HIDE and code(n) == code(q))]
        hits = ranked(q, pool_names, library)
        results[q] = {"names": [n for _, n in hits[:3]], "scores": [round(s, 3) for s, _ in hits[:3]], "verdict": "same" if hits[0][0] >= cut else "nothing close", "what": "", "tokens": 0}
    first = sum(results[q]["verdict"] == "same" and code(results[q]["names"][0]) == code(q) for q in FIND)
    top = sum(any(code(n) == code(q) for n in results[q]["names"]) for q in FIND)
    print(json.dumps({"first": f"{first}/5", "top3": f"{top}/5", "refused": f"{sum(results[q]['verdict'] == 'nothing close' for q in NOTHING + HIDE)}/7", "wrongly_refused": f"{sum(results[q]['verdict'] == 'nothing close' for q in FIND)}/5"}))
    for q, a in results.items():
        print(f"{q[:28]:28} {a['verdict']:14} {list(zip([n[:16] for n in a['names']], a['scores']))}")
    (OUT / "shape.json").write_text(json.dumps({"cut": cut, "results": results}, indent=1))
    from .retrieve_test import contact
    contact(results, OUT / "shape.jpg")


if __name__ == "__main__":
    main()
