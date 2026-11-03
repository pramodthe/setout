"""Encode the Blender PNG sequence for the scroll player and a portable preview."""
import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "var/anatomy-cinema"


def main():
    parser = argparse.ArgumentParser()
    # Each look renders to its own frames and is served from its own folder.
    parser.add_argument("--look", choices=["studio", "fog"], default="studio")
    suffix = "" if parser.parse_args().look == "studio" else "-fog"
    output = ROOT / f"web/public/assets/anatomy/film{suffix}"
    sequence = json.loads((SOURCE / f"sequence{suffix}.json").read_text())
    paths = [SOURCE / f"frames{suffix}" / f"{i:03d}.png" for i in range(sequence["frames"])]
    missing = [path.name for path in paths if not path.is_file()]
    if missing:
        raise RuntimeError(f"Incomplete sequence: {', '.join(missing)}")
    output.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    for path in paths:
        with Image.open(path) as image:
            size = (sequence["width"], sequence["height"])
            if image.size != size:
                if image.width < size[0] or image.height < size[1]:
                    raise RuntimeError(f"Undersized frame: {path.name}: {image.size}")
                image = image.resize(size, Image.Resampling.LANCZOS)
            destination = output / f"{path.stem}.webp"
            image.convert("RGB").save(destination, "WEBP", quality=88, method=6)
            digest.update(destination.read_bytes())
    manifest = {**sequence, "components": 6405, "revision": digest.hexdigest()[:12]}
    (output / "manifest.json").write_text(json.dumps(manifest))
    # A self-contained, full-colour preview; the site uses individual frames
    # so scrolling can pause and reverse at any point.
    preview = []
    for path in paths:
        with Image.open(path) as source:
            image = source.convert("RGB")
            image.thumbnail((960, 600), Image.Resampling.LANCZOS)
            preview.append(image)
    if len(preview) > 115:
        preview.extend(reversed(preview[83:115]))
        last, first = preview[-1], preview[0]
        preview.extend(Image.blend(last, first, step/8) for step in range(1, 9))
    preview[0].save(SOURCE / f"anatomy-film{suffix}.webp", save_all=True,
                    append_images=preview[1:], duration=100, loop=0,
                    quality=82, method=4)
    print(json.dumps({"frames": len(paths), "revision": manifest["revision"],
                      "web_bytes": sum(p.stat().st_size for p in output.glob("*.webp"))}))


if __name__ == "__main__":
    main()
