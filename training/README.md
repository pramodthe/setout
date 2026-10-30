# training

This directory holds what runs on the team's own GPUs through Modal; so far
that is only `modal_gpu.py`, a one-function smoke test that asks for a B200 and
prints what `nvidia-smi` and `torch.cuda` report (`modal run
training/modal_gpu.py`). The data for it is prepared by `app/dxf_ingest.py` and
`tools/dxf_census.py`, and what gets trained is specified in
`docs/DETAIL_WORLD_MODEL_SPEC.md`. Datasets, renders, masks and weights never
enter this repo: they live in private storage, and anything written locally
goes under `data/`, which is gitignored.
