"""GPU smoke test: is a B200 available to this Modal account, and what does it report?

    modal run training/modal_gpu.py                         # asks for a B200
    SETOUT_SMOKE_GPU=H100 modal run training/modal_gpu.py   # the fallback, to prove the path

One function on one GPU prints nvidia-smi and the torch.cuda device
properties. Nothing else in the repo runs on a GPU yet, and nothing here reads
or writes drawings, datasets or weights (see training/README.md).
"""
import os
import subprocess

import modal

GPU = os.environ.get("SETOUT_SMOKE_GPU", "B200")

app = modal.App("setout-gpu-smoke")
# The plain PyPI torch wheel carries its own CUDA runtime; nothing else is needed.
image = modal.Image.debian_slim(python_version="3.12").pip_install("torch")


@app.function(image=image, gpu=GPU, timeout=600)
def probe(requested: str) -> str:
    lines = [f"gpu requested: {requested}"]
    smi = subprocess.run(["nvidia-smi"], capture_output=True, text=True)
    lines.append(smi.stdout or smi.stderr)
    try:
        import torch
    except ImportError as exc:
        lines.append(f"torch unavailable: {exc}")
        return "\n".join(lines)
    lines.append(f"torch {torch.__version__}, cuda {torch.version.cuda}, "
                 f"available={torch.cuda.is_available()}, devices={torch.cuda.device_count()}")
    for index in range(torch.cuda.device_count()):
        p = torch.cuda.get_device_properties(index)
        lines.append(f"device {index}: {p.name}, {p.total_memory / 2 ** 30:.1f} GiB, "
                     f"capability {p.major}.{p.minor}, {p.multi_processor_count} SMs")
    return "\n".join(lines)


@app.local_entrypoint()
def main():
    print(probe.remote(GPU))
