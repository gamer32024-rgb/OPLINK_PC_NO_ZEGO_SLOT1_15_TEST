from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .json_io import write_json_file


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

    def crop(self, image: Image.Image) -> Image.Image:
        return image.crop((self.x, self.y, self.x + self.w, self.y + self.h))


@dataclass(frozen=True)
class LabelRow:
    filename: str
    map_name: str
    east: str
    south: str
    index: int
    width: int
    height: int


@dataclass(frozen=True)
class BBox:
    x: int
    y: int
    w: int
    h: int
    area: int


@dataclass(frozen=True)
class Hole:
    area: int
    center_x: float
    center_y: float


@dataclass(frozen=True)
class GlyphSample:
    digit: str
    vector: np.ndarray
    filename: str
    field: str


@dataclass(frozen=True)
class NumberSample:
    label: LabelRow
    field: str
    truth: str
    roi: Roi
    crop: Image.Image
    mask: np.ndarray
    boxes: list[BBox]
    vectors: list[np.ndarray]


@dataclass(frozen=True)
class NumberPrediction:
    truth: str
    predicted: str
    segment_count: int
    scores: list[float]

    @property
    def ok(self) -> bool:
        return self.truth == self.predicted


@dataclass(frozen=True)
class ImagePrediction:
    label: LabelRow
    east: NumberPrediction
    south: NumberPrediction

    @property
    def ok(self) -> bool:
        return self.east.ok and self.south.ok


def load_labels(path: Path) -> list[LabelRow]:
    rows: list[LabelRow] = []
    with path.open(newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        for row in reader:
            rows.append(
                LabelRow(
                    filename=row["filename"],
                    map_name=row["map_name"],
                    east=str(row["east"]),
                    south=str(row["south"]),
                    index=int(row["index"]),
                    width=int(row["width"]),
                    height=int(row["height"]),
                )
            )
    return rows


def digit_mask(crop: Image.Image) -> np.ndarray:
    image = np.asarray(crop.convert("RGB"))
    rgb = image.astype(np.int16)
    red = rgb[:, :, 0]
    green = rgb[:, :, 1]
    blue = rgb[:, :, 2]
    brightest = np.maximum.reduce([red, green, blue])
    darkest = np.minimum.reduce([red, green, blue])

    # The coordinate digits are off-white, while the fixed 東方/南方 labels are green.
    mask = (red > 135) & (green > 120) & (blue > 100) & ((brightest - darkest) < 95)
    return mask.astype(np.uint8) * 255


def segment_digits(mask: np.ndarray, min_area: int = 8, min_height: int = 6) -> list[BBox]:
    component_count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(mask, 8)
    boxes: list[BBox] = []
    for index in range(1, component_count):
        x, y, w, h, area = (int(value) for value in stats[index])
        if area >= min_area and h >= min_height:
            boxes.extend(_split_wide_box(mask, BBox(x=x, y=y, w=w, h=h, area=area)))
    return sorted(boxes, key=lambda box: (box.x, box.y))


def _split_wide_box(mask: np.ndarray, box: BBox, max_digit_width: int = 8) -> list[BBox]:
    if box.w <= max_digit_width:
        return [box]

    crop = mask[box.y : box.y + box.h, box.x : box.x + box.w]
    column_counts = (crop.sum(axis=0) // 255).astype(int)
    split = min(
        range(4, box.w - 3),
        key=lambda index: (
            int(column_counts[index - 1] + column_counts[index] + column_counts[min(index + 1, box.w - 1)]),
            abs(index - box.w / 2),
        ),
    )

    boxes: list[BBox] = []
    for region, offset_x in ((crop[:, :split], box.x), (crop[:, split:], box.x + split)):
        child = _bbox_from_region(region, offset_x, box.y)
        if child is not None:
            boxes.extend(_split_wide_box(mask, child, max_digit_width=max_digit_width))
    return boxes


def _bbox_from_region(region: np.ndarray, offset_x: int, offset_y: int) -> BBox | None:
    rows, cols = np.where(region > 0)
    if len(cols) == 0:
        return None
    return BBox(
        x=int(offset_x + cols.min()),
        y=int(offset_y + rows.min()),
        w=int(cols.max() - cols.min() + 1),
        h=int(rows.max() - rows.min() + 1),
        area=int(len(cols)),
    )


def normalize_glyph(mask: np.ndarray, box: BBox, target_width: int = 16, target_height: int = 20) -> np.ndarray:
    y1 = max(0, box.y - 1)
    y2 = min(mask.shape[0], box.y + box.h + 1)
    x1 = max(0, box.x - 1)
    x2 = min(mask.shape[1], box.x + box.w + 1)
    crop = mask[y1:y2, x1:x2]

    rows, cols = np.where(crop > 0)
    output = np.zeros((target_height, target_width), dtype=np.uint8)
    if len(cols) == 0:
        return output.astype(np.float32).ravel()

    crop = crop[rows.min() : rows.max() + 1, cols.min() : cols.max() + 1]
    crop_height, crop_width = crop.shape
    scale = min((target_width - 2) / crop_width, (target_height - 2) / crop_height)
    resized_width = max(1, int(round(crop_width * scale)))
    resized_height = max(1, int(round(crop_height * scale)))
    resized = cv2.resize(crop, (resized_width, resized_height), interpolation=cv2.INTER_NEAREST)

    paste_x = (target_width - resized_width) // 2
    paste_y = (target_height - resized_height) // 2
    output[paste_y : paste_y + resized_height, paste_x : paste_x + resized_width] = resized // 255
    return output.astype(np.float32).ravel()


def glyph_holes(mask: np.ndarray, box: BBox) -> list[Hole]:
    crop = mask[box.y : box.y + box.h, box.x : box.x + box.w]
    inverted = (np.pad(crop, 1) == 0).astype(np.uint8)
    component_count, labels, _stats, _centroids = cv2.connectedComponentsWithStats(inverted, 4)
    height, width = inverted.shape
    holes: list[Hole] = []
    for index in range(1, component_count):
        rows, cols = np.where(labels == index)
        if len(cols) == 0:
            continue
        touches_border = (
            cols.min() == 0
            or rows.min() == 0
            or cols.max() == width - 1
            or rows.max() == height - 1
        )
        if touches_border:
            continue
        holes.append(
            Hole(
                area=len(cols),
                center_x=float(cols.mean() - 1),
                center_y=float(rows.mean() - 1),
            )
        )
    return holes


def candidate_digits(mask: np.ndarray, box: BBox) -> set[str]:
    holes = glyph_holes(mask, box)
    if len(holes) >= 2:
        return {"8"}
    if len(holes) == 1:
        hole = holes[0]
        if hole.area >= 9:
            return {"0"}
        if hole.center_y >= 4.7:
            return {"6", "8"}
        if hole.center_y <= 3.6:
            return {"4", "9"}
        return {"4", "6", "8", "9"}

    crop = mask[box.y : box.y + box.h, box.x : box.x + box.w]
    row_counts = (crop.sum(axis=1) // 255).astype(int)
    column_counts = (crop.sum(axis=0) // 255).astype(int)
    if box.w >= 7 and row_counts.max() >= box.w - 1 and column_counts.max() >= box.h:
        return {"4"}
    return {"1", "2", "3", "5", "7"}


def extract_samples(labels: list[LabelRow], image_dir: Path, east_roi: Roi, south_roi: Roi) -> dict[tuple[str, str], NumberSample]:
    samples: dict[tuple[str, str], NumberSample] = {}
    for label in labels:
        image_path = image_dir / label.filename
        with Image.open(image_path) as image:
            screenshot = image.convert("RGB")
            for field, truth, roi in (
                ("east", label.east, east_roi),
                ("south", label.south, south_roi),
            ):
                crop = roi.crop(screenshot)
                mask = digit_mask(crop)
                boxes = segment_digits(mask)
                vectors = [normalize_glyph(mask, box) for box in boxes]
                samples[(label.filename, field)] = NumberSample(
                    label=label,
                    field=field,
                    truth=truth,
                    roi=roi,
                    crop=crop,
                    mask=mask,
                    boxes=boxes,
                    vectors=vectors,
                )
    return samples


def build_glyphs(samples: dict[tuple[str, str], NumberSample], exclude_filename: str | None = None) -> list[GlyphSample]:
    glyphs: list[GlyphSample] = []
    for sample in samples.values():
        if sample.label.filename == exclude_filename:
            continue
        if len(sample.vectors) != len(sample.truth):
            continue
        for digit, vector in zip(sample.truth, sample.vectors):
            glyphs.append(
                GlyphSample(
                    digit=digit,
                    vector=vector,
                    filename=sample.label.filename,
                    field=sample.field,
                )
            )
    return glyphs


def predict_number(sample: NumberSample, glyphs: list[GlyphSample]) -> NumberPrediction:
    if not glyphs:
        raise RuntimeError("No glyph templates available for OCR probe.")

    predicted: list[str] = []
    scores: list[float] = []
    for box, vector in zip(sample.boxes, sample.vectors):
        candidates = candidate_digits(sample.mask, box)
        candidate_glyphs = [glyph for glyph in glyphs if glyph.digit in candidates] or glyphs

        score, digit = min(
            (
                float(np.mean(np.abs(vector - glyph.vector))),
                glyph.digit,
            )
            for glyph in candidate_glyphs
        )
        predicted.append(digit)
        scores.append(score)

    return NumberPrediction(
        truth=sample.truth,
        predicted="".join(predicted),
        segment_count=len(sample.vectors),
        scores=scores,
    )


def run_probe(
    labels: list[LabelRow],
    image_dir: Path,
    east_roi: Roi,
    south_roi: Roi,
    mode: str,
) -> tuple[list[ImagePrediction], dict[tuple[str, str], NumberSample]]:
    samples = extract_samples(labels, image_dir, east_roi, south_roi)
    shared_glyphs = build_glyphs(samples) if mode == "self" else []
    predictions: list[ImagePrediction] = []

    for label in labels:
        glyphs = shared_glyphs if mode == "self" else build_glyphs(samples, exclude_filename=label.filename)
        east = predict_number(samples[(label.filename, "east")], glyphs)
        south = predict_number(samples[(label.filename, "south")], glyphs)
        predictions.append(ImagePrediction(label=label, east=east, south=south))

    return predictions, samples


def make_report(
    predictions: list[ImagePrediction],
    labels_path: Path,
    image_dir: Path,
    east_roi: Roi,
    south_roi: Roi,
    mode: str,
    target_complete: int,
) -> dict[str, object]:
    total = len(predictions)
    complete_correct = sum(1 for prediction in predictions if prediction.ok)
    east_correct = sum(1 for prediction in predictions if prediction.east.ok)
    south_correct = sum(1 for prediction in predictions if prediction.south.ok)
    segmentation_errors = [
        {
            "filename": prediction.label.filename,
            "east_segments": prediction.east.segment_count,
            "east_expected": len(prediction.east.truth),
            "south_segments": prediction.south.segment_count,
            "south_expected": len(prediction.south.truth),
        }
        for prediction in predictions
        if prediction.east.segment_count != len(prediction.east.truth)
        or prediction.south.segment_count != len(prediction.south.truth)
    ]
    results = [
        {
            "filename": prediction.label.filename,
            "index": prediction.label.index,
            "map_name": prediction.label.map_name,
            "east_truth": prediction.east.truth,
            "east_predicted": prediction.east.predicted,
            "east_ok": prediction.east.ok,
            "east_segments": prediction.east.segment_count,
            "south_truth": prediction.south.truth,
            "south_predicted": prediction.south.predicted,
            "south_ok": prediction.south.ok,
            "south_segments": prediction.south.segment_count,
            "complete_ok": prediction.ok,
        }
        for prediction in predictions
    ]
    failures = [result for result in results if not result["complete_ok"]]

    return {
        "mode": mode,
        "labels": str(labels_path),
        "image_dir": str(image_dir),
        "east_number_roi": f"{east_roi.x},{east_roi.y},{east_roi.w},{east_roi.h}",
        "south_number_roi": f"{south_roi.x},{south_roi.y},{south_roi.w},{south_roi.h}",
        "target_complete": target_complete,
        "passed": complete_correct >= target_complete,
        "total": total,
        "complete_correct": complete_correct,
        "east_correct": east_correct,
        "south_correct": south_correct,
        "segmentation_error_count": len(segmentation_errors),
        "segmentation_errors": segmentation_errors,
        "failures": failures,
        "results": results,
    }


def write_debug_crops(
    predictions: list[ImagePrediction],
    samples: dict[tuple[str, str], NumberSample],
    output_dir: Path,
    include_all: bool,
) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    for old_crop in output_dir.glob("*_true_*_pred_*.png"):
        old_crop.unlink()
    count = 0
    font = ImageFont.load_default()
    for prediction in predictions:
        for field, number in (("east", prediction.east), ("south", prediction.south)):
            if number.ok and not include_all:
                continue
            sample = samples[(prediction.label.filename, field)]
            debug = _debug_crop_image(sample, number, font)
            output = output_dir / (
                f"{prediction.label.index:03d}_{field}_true_{number.truth}_pred_{number.predicted or 'empty'}.png"
            )
            debug.save(output)
            count += 1
    return count


def _debug_crop_image(sample: NumberSample, prediction: NumberPrediction, font: ImageFont.ImageFont) -> Image.Image:
    scale = 6
    crop = sample.crop.resize((sample.crop.width * scale, sample.crop.height * scale), Image.Resampling.NEAREST)
    mask = Image.fromarray(sample.mask, mode="L").convert("RGB")
    mask = mask.resize((mask.width * scale, mask.height * scale), Image.Resampling.NEAREST)

    width = max(crop.width, mask.width)
    height = 20 + crop.height + 4 + mask.height
    debug = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(debug)
    status = "OK" if prediction.ok else "FAIL"
    draw.text((4, 4), f"{sample.field} true={prediction.truth} pred={prediction.predicted} {status}", fill="black", font=font)
    debug.paste(crop, (0, 20))
    debug.paste(mask, (0, 20 + crop.height + 4))
    for box in sample.boxes:
        draw.rectangle(
            (
                box.x * scale,
                20 + box.y * scale,
                (box.x + box.w) * scale - 1,
                20 + (box.y + box.h) * scale - 1,
            ),
            outline="red",
            width=2,
        )
    return debug


def print_summary(report: dict[str, object], report_path: Path, debug_dir: Path, debug_count: int) -> None:
    print(
        "OCR probe: "
        f"{report['complete_correct']}/{report['total']} complete, "
        f"east {report['east_correct']}/{report['total']}, "
        f"south {report['south_correct']}/{report['total']}, "
        f"segmentation_errors {report['segmentation_error_count']}"
    )
    print(f"Target: {report['target_complete']} complete ({'PASS' if report['passed'] else 'FAIL'})")
    print(f"Report: {report_path}")
    print(f"Debug crops: {debug_dir} ({debug_count} files)")
    failures = report["failures"]
    if isinstance(failures, list) and failures:
        for item in failures:
            if not isinstance(item, dict):
                continue
            print(
                "FAIL "
                f"{item['filename']}: "
                f"east {item['east_truth']}->{item['east_predicted']} "
                f"south {item['south_truth']}->{item['south_predicted']}"
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe fixed ROI OCR for world map east/south coordinates.")
    parser.add_argument("--labels", default="datasets/ocr/world/labels.csv")
    parser.add_argument("--image-dir", help="Defaults to the labels.csv parent directory.")
    parser.add_argument("--east-number-roi", default="828,158,36,20", help="x,y,w,h")
    parser.add_argument("--south-number-roi", default="892,158,44,20", help="x,y,w,h")
    parser.add_argument("--mode", choices=["leave-one-out", "self"], default="leave-one-out")
    parser.add_argument("--target-complete", type=int, default=28)
    parser.add_argument("--report", default="runs/ocr_probe_report.json")
    parser.add_argument("--debug-dir", default="runs/ocr_probe_debug")
    parser.add_argument("--debug-all", action="store_true")
    parser.add_argument("--no-debug-crops", action="store_true")
    args = parser.parse_args(argv)

    labels_path = Path(args.labels)
    image_dir = Path(args.image_dir) if args.image_dir else labels_path.parent
    east_roi = Roi.parse(args.east_number_roi)
    south_roi = Roi.parse(args.south_number_roi)
    labels = load_labels(labels_path)
    predictions, samples = run_probe(labels, image_dir, east_roi, south_roi, args.mode)
    report = make_report(predictions, labels_path, image_dir, east_roi, south_roi, args.mode, args.target_complete)

    report_path = Path(args.report)
    write_json_file(report_path, report)

    debug_dir = Path(args.debug_dir)
    debug_count = 0
    if not args.no_debug_crops:
        debug_count = write_debug_crops(predictions, samples, debug_dir, args.debug_all)

    print_summary(report, report_path, debug_dir, debug_count)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
