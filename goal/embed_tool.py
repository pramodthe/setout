"""Embeddings for the catalogue search, run with data/goal/embed-venv/bin/python, which holds fastembed. It reads a request
as JSON on stdin and writes vectors to the .npz it names, so the main environment needs no model.

    {"out": path, "texts": [...], "images": [...], "clip_texts": [...]}
"""
import json
import sys

import numpy as np
from fastembed import ImageEmbedding, TextEmbedding

TEXT = "BAAI/bge-small-en-v1.5"
CLIP_IMAGE, CLIP_TEXT = "Qdrant/clip-ViT-B-32-vision", "Qdrant/clip-ViT-B-32-text"


def unit(vectors) -> np.ndarray:
    v = np.array(list(vectors), np.float32)
    return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)


if __name__ == "__main__":
    ask = json.load(sys.stdin)
    out = {}
    if ask.get("texts"):
        out["texts"] = unit(TextEmbedding(TEXT).embed(ask["texts"]))
    if ask.get("images"):
        out["images"] = unit(ImageEmbedding(CLIP_IMAGE).embed(ask["images"]))
    if ask.get("clip_texts"):
        out["clip_texts"] = unit(TextEmbedding(CLIP_TEXT).embed(ask["clip_texts"]))
    np.savez(ask["out"], **out)
