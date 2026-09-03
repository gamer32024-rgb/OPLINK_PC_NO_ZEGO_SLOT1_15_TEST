from __future__ import annotations

import argparse
import csv
from pathlib import Path
import re

from PIL import Image


TRUTH_RE = re.compile(r"^world_(?P<map>.+)_東方(?P<east>\d{1,3})_南方(?P<south>\d{1,3})_(?P<idx>\d{3})\.png$")


def build_manifest(input_dir: Path, output_path: Path) -> int:
    rows: list[dict[str, str | int]] = []
    for path in sorted(input_dir.glob("*.png")):
        match = TRUTH_RE.match(path.name)
        if not match:
            continue
        with Image.open(path) as image:
            width, height = image.size
        rows.append(
            {
                "filename": path.name,
                "map_name": match.group("map"),
                "east": int(match.group("east")),
                "south": int(match.group("south")),
                "index": int(match.group("idx")),
                "width": width,
                "height": height,
            }
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["filename", "map_name", "east", "south", "index", "width", "height"],
        )
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build OCR label manifest from screenshot filenames.")
    parser.add_argument("--input-dir", default="datasets/ocr/world")
    parser.add_argument("--output", default="datasets/ocr/world/labels.csv")
    args = parser.parse_args(argv)

    count = build_manifest(Path(args.input_dir), Path(args.output))
    print(f"{args.output} ({count} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
