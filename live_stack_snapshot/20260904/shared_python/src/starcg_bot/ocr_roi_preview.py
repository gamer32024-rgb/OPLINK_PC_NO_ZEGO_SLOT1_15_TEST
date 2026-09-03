from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re

from PIL import Image, ImageDraw, ImageFont


@dataclass(frozen=True)
class Roi:
    x: int
    y: int
    w: int
    h: int

    @classmethod
    def parse(cls, value: str) -> "Roi":
        parts = [int(part.strip()) for part in value.split(",")]
        if len(parts) != 4:
            raise ValueError("ROI must be x,y,w,h")
        return cls(*parts)


TRUTH_RE = re.compile(r"^world_(?P<map>.+)_東方(?P<east>\d{1,3})_南方(?P<south>\d{1,3})_(?P<idx>\d{3})\.png$")


def filename_truth(path: Path) -> str:
    match = TRUTH_RE.match(path.name)
    if not match:
        return path.stem
    return f"{match.group('map')} E{match.group('east')} S{match.group('south')} #{match.group('idx')}"


def make_preview(input_dir: Path, output_path: Path, map_roi: Roi, coord_roi: Roi, limit: int) -> None:
    paths = sorted(input_dir.glob("*.png"))[:limit]
    if not paths:
        raise RuntimeError(f"No PNG files found in {input_dir}")

    font = ImageFont.load_default()
    map_scale = 3
    coord_scale = 3
    scaled_map_h = map_roi.h * map_scale
    scaled_coord_h = coord_roi.h * coord_scale
    cell_w = 640
    cell_h = 38 + scaled_map_h + 12 + scaled_coord_h + 12
    cols = 2
    rows = (len(paths) + cols - 1) // cols
    sheet = Image.new("RGB", (cell_w * cols, cell_h * rows), "white")
    draw = ImageDraw.Draw(sheet)

    for i, path in enumerate(paths):
        with Image.open(path) as image:
            image = image.convert("RGB")
            map_crop = image.crop((map_roi.x, map_roi.y, map_roi.x + map_roi.w, map_roi.y + map_roi.h))
            coord_crop = image.crop((coord_roi.x, coord_roi.y, coord_roi.x + coord_roi.w, coord_roi.y + coord_roi.h))
            map_crop = map_crop.resize((map_crop.width * map_scale, map_crop.height * map_scale))
            coord_crop = coord_crop.resize((coord_crop.width * coord_scale, coord_crop.height * coord_scale))

        col = i % cols
        row = i // cols
        x = col * cell_w
        y = row * cell_h
        draw.text((x + 8, y + 6), filename_truth(path), fill="black", font=font)
        sheet.paste(map_crop, (x + 8, y + 26))
        sheet.paste(coord_crop, (x + 8, y + 26 + scaled_map_h + 10))
        draw.rectangle((x, y, x + cell_w - 1, y + cell_h - 1), outline="#cccccc")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create OCR ROI preview contact sheet.")
    parser.add_argument("--input-dir", default="datasets/ocr/world")
    parser.add_argument("--output", default="runs/ocr_roi_preview.png")
    parser.add_argument("--map-roi", default="760,6,190,30", help="x,y,w,h")
    parser.add_argument("--coord-roi", default="780,152,170,24", help="x,y,w,h")
    parser.add_argument("--limit", type=int, default=32)
    args = parser.parse_args(argv)

    make_preview(
        Path(args.input_dir),
        Path(args.output),
        Roi.parse(args.map_roi),
        Roi.parse(args.coord_roi),
        args.limit,
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
