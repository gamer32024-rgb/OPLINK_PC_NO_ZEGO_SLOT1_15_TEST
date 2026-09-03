from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image

from .ocr_probe import (
    GlyphSample,
    LabelRow,
    NumberPrediction,
    NumberSample,
    Roi,
    build_glyphs,
    digit_mask,
    extract_samples,
    load_labels,
    normalize_glyph,
    predict_number,
    segment_digits,
)


EXPECTED_SCREEN_SIZE = (960, 540)
MINIMAP_ROI = Roi(764, 6, 193, 176)
COORD_PANEL_ROI = Roi(780, 152, 170, 24)
DIALOG_SCAN_ROI = Roi(100, 70, 760, 420)


@dataclass(frozen=True)
class WorldCoordinates:
    east: int
    south: int
    east_segments: int
    south_segments: int

    def to_dict(self) -> dict[str, int]:
        return {
            "east": self.east,
            "south": self.south,
            "east_segments": self.east_segments,
            "south_segments": self.south_segments,
        }


@dataclass(frozen=True)
class WorldVisualEvidence:
    size_ok: bool
    minimap_dark_pixels: int
    minimap_orange_pixels: int
    minimap_yellow_pixels: int
    coord_green_pixels: int
    coord_digit_pixels: int
    dialog_overlay: bool
    transition_overlay: bool

    @property
    def world_anchor_ok(self) -> bool:
        return (
            self.size_ok
            and self.minimap_dark_pixels >= 5000
            and self.minimap_orange_pixels >= 1800
            and self.minimap_yellow_pixels >= 700
            and self.coord_green_pixels >= 90
            and self.coord_digit_pixels >= 30
        )

    def anchor_summary(self) -> str:
        return (
            f"size_ok={self.size_ok}, "
            f"minimap_dark={self.minimap_dark_pixels}, "
            f"minimap_orange={self.minimap_orange_pixels}, "
            f"minimap_yellow={self.minimap_yellow_pixels}, "
            f"coord_green={self.coord_green_pixels}, "
            f"coord_digit={self.coord_digit_pixels}"
        )


@dataclass(frozen=True)
class ScreenState:
    main_state: str
    overlay: str
    transition: str
    actionable: bool
    confidence: float
    coordinates: WorldCoordinates | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "main_state": self.main_state,
            "overlay": self.overlay,
            "transition": self.transition,
            "actionable": self.actionable,
            "confidence": self.confidence,
            "coordinates": None if self.coordinates is None else self.coordinates.to_dict(),
            "reason": self.reason,
        }


class WorldCoordinateReader:
    def __init__(
        self,
        glyphs: list[GlyphSample],
        east_roi: Roi = Roi(828, 158, 36, 20),
        south_roi: Roi = Roi(892, 158, 44, 20),
    ) -> None:
        self.glyphs = glyphs
        self.east_roi = east_roi
        self.south_roi = south_roi

    @classmethod
    def from_labels(
        cls,
        labels_path: Path,
        image_dir: Path | None = None,
        east_roi: Roi = Roi(828, 158, 36, 20),
        south_roi: Roi = Roi(892, 158, 44, 20),
    ) -> "WorldCoordinateReader":
        labels = load_labels(labels_path)
        source_dir = image_dir or labels_path.parent
        samples = extract_samples(labels, source_dir, east_roi, south_roi)
        return cls(build_glyphs(samples), east_roi=east_roi, south_roi=south_roi)

    def read_image(self, image_path: Path) -> WorldCoordinates | None:
        with Image.open(image_path) as image:
            return self.read(image.convert("RGB"), filename=image_path.name)

    def read(self, image: Image.Image, filename: str = "<image>") -> WorldCoordinates | None:
        east = self._read_number(image, filename, "east", self.east_roi)
        south = self._read_number(image, filename, "south", self.south_roi)
        if not _valid_coordinate_prediction(east) or not _valid_coordinate_prediction(south):
            return None
        return WorldCoordinates(
            east=int(east.predicted),
            south=int(south.predicted),
            east_segments=east.segment_count,
            south_segments=south.segment_count,
        )

    def _read_number(self, image: Image.Image, filename: str, field: str, roi: Roi) -> NumberPrediction:
        crop = roi.crop(image)
        mask = digit_mask(crop)
        boxes = segment_digits(mask)
        sample = NumberSample(
            label=LabelRow(
                filename=filename,
                map_name="",
                east="",
                south="",
                index=0,
                width=image.width,
                height=image.height,
            ),
            field=field,
            truth="",
            roi=roi,
            crop=crop,
            mask=mask,
            boxes=boxes,
            vectors=[normalize_glyph(mask, box) for box in boxes],
        )
        return predict_number(sample, self.glyphs)


class WorldStateDetector:
    def __init__(self, coordinate_reader: WorldCoordinateReader) -> None:
        self.coordinate_reader = coordinate_reader

    @classmethod
    def from_ocr_labels(
        cls,
        labels_path: Path,
        image_dir: Path | None = None,
        east_roi: Roi = Roi(828, 158, 36, 20),
        south_roi: Roi = Roi(892, 158, 44, 20),
    ) -> "WorldStateDetector":
        return cls(WorldCoordinateReader.from_labels(labels_path, image_dir, east_roi, south_roi))

    def detect_image(self, image_path: Path) -> ScreenState:
        with Image.open(image_path) as image:
            return self.detect(image.convert("RGB"), filename=image_path.name)

    def detect(self, image: Image.Image, filename: str = "<image>") -> ScreenState:
        screenshot = image.convert("RGB")
        evidence = _inspect_world_visual_evidence(screenshot)
        if evidence.transition_overlay:
            return _unknown_state(
                transition="unknown_transition",
                reason="transition-like screen detected",
            )

        coordinates = self.coordinate_reader.read(screenshot, filename=filename)
        if coordinates is None:
            overlay = "dialog" if evidence.dialog_overlay else "none"
            return _unknown_state(overlay=overlay, reason="world coordinate OCR failed")

        if not evidence.world_anchor_ok:
            overlay = "dialog" if evidence.dialog_overlay else "none"
            return _unknown_state(
                overlay=overlay,
                reason=f"world visual anchors failed: {evidence.anchor_summary()}",
            )

        if evidence.dialog_overlay:
            return ScreenState(
                main_state="world",
                overlay="dialog",
                transition="none",
                actionable=False,
                confidence=0.8,
                coordinates=coordinates,
                reason="dialog overlay detected",
            )

        return ScreenState(
            main_state="world",
            overlay="none",
            transition="none",
            actionable=True,
            confidence=0.95,
            coordinates=coordinates,
            reason="world coordinate OCR succeeded",
        )


def _unknown_state(
    *,
    overlay: str = "none",
    transition: str = "none",
    reason: str,
) -> ScreenState:
    return ScreenState(
        main_state="unknown",
        overlay=overlay,
        transition=transition,
        actionable=False,
        confidence=0.0,
        reason=reason,
    )


def _valid_coordinate_prediction(prediction: NumberPrediction) -> bool:
    if not 1 <= prediction.segment_count <= 3:
        return False
    if not prediction.predicted.isdecimal():
        return False
    value = int(prediction.predicted)
    return 1 <= value <= 999


def _inspect_world_visual_evidence(image: Image.Image) -> WorldVisualEvidence:
    size_ok = image.size == EXPECTED_SCREEN_SIZE
    rgb = np.asarray(image.convert("RGB"))
    transition_overlay = _looks_like_transition(rgb)

    if not size_ok:
        return WorldVisualEvidence(
            size_ok=False,
            minimap_dark_pixels=0,
            minimap_orange_pixels=0,
            minimap_yellow_pixels=0,
            coord_green_pixels=0,
            coord_digit_pixels=0,
            dialog_overlay=False,
            transition_overlay=transition_overlay,
        )

    minimap = _crop_array(rgb, MINIMAP_ROI).astype(np.int16)
    coord_panel = _crop_array(rgb, COORD_PANEL_ROI).astype(np.int16)
    minimap_red, minimap_green, minimap_blue = _channels(minimap)
    coord_red, coord_green, coord_blue = _channels(coord_panel)

    minimap_dark = (minimap_red < 75) & (minimap_green < 75) & (minimap_blue < 75)
    minimap_orange = (
        (minimap_red > 180)
        & (minimap_green > 70)
        & (minimap_green < 190)
        & (minimap_blue < 110)
        & ((minimap_red - minimap_green) > 20)
    )
    minimap_yellow = (
        (minimap_red > 145)
        & (minimap_green > 120)
        & (minimap_blue < 110)
        & (np.abs(minimap_red - minimap_green) < 90)
    )
    coord_green_label = (
        (coord_green > 130)
        & (coord_red < 140)
        & (coord_blue < 110)
        & ((coord_green - coord_red) > 20)
        & ((coord_green - coord_blue) > 30)
    )
    brightest = np.maximum.reduce([coord_red, coord_green, coord_blue])
    darkest = np.minimum.reduce([coord_red, coord_green, coord_blue])
    coord_digit = (
        (coord_red > 135)
        & (coord_green > 120)
        & (coord_blue > 100)
        & ((brightest - darkest) < 95)
    )

    return WorldVisualEvidence(
        size_ok=True,
        minimap_dark_pixels=int(minimap_dark.sum()),
        minimap_orange_pixels=int(minimap_orange.sum()),
        minimap_yellow_pixels=int(minimap_yellow.sum()),
        coord_green_pixels=int(coord_green_label.sum()),
        coord_digit_pixels=int(coord_digit.sum()),
        dialog_overlay=_looks_like_dialog_overlay(rgb),
        transition_overlay=transition_overlay,
    )


def _crop_array(rgb: np.ndarray, roi: Roi) -> np.ndarray:
    return rgb[roi.y : roi.y + roi.h, roi.x : roi.x + roi.w]


def _channels(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    return rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]


def _looks_like_transition(rgb: np.ndarray) -> bool:
    luminance = _luminance(rgb)
    mean = float(luminance.mean())
    std = float(luminance.std())
    dark_ratio = float((luminance < 20).mean())
    bright_ratio = float((luminance > 245).mean())
    return mean < 35 or mean > 235 or std < 15 or dark_ratio > 0.92 or bright_ratio > 0.92


def _looks_like_dialog_overlay(rgb: np.ndarray) -> bool:
    scan = _crop_array(rgb, DIALOG_SCAN_ROI).astype(np.int16)
    if _has_large_uniform_panel(scan):
        return True

    red, green, blue = _channels(scan)
    brightest = np.maximum.reduce([red, green, blue])
    darkest = np.minimum.reduce([red, green, blue])

    dark_panel = (brightest < 95) & ((brightest - darkest) < 80)
    light_neutral_panel = (brightest > 120) & ((brightest - darkest) < 70)
    beige_panel = (
        (red > 120)
        & (green > 90)
        & (blue > 55)
        & (red >= green)
        & (green >= blue)
        & ((red - blue) < 110)
    )
    panel_mask = (dark_panel | light_neutral_panel | beige_panel).astype(np.uint8) * 255
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
    closed = cv2.morphologyEx(panel_mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    component_count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(closed, 8)

    height, width = panel_mask.shape
    for index in range(1, component_count):
        x, y, w, h, area = (int(value) for value in stats[index])
        touches_scan_edge = x <= 2 or y <= 2 or x + w >= width - 2 or y + h >= height - 2
        if touches_scan_edge:
            continue
        if w < 260 or h < 100 or area < 25000:
            continue
        fill_ratio = area / (w * h)
        if fill_ratio >= 0.55:
            return True
    return False


def _has_large_uniform_panel(scan: np.ndarray) -> bool:
    quantized = (scan // 16).astype(np.int32)
    codes = (quantized[:, :, 0] << 8) | (quantized[:, :, 1] << 4) | quantized[:, :, 2]
    values, counts = np.unique(codes, return_counts=True)
    candidate_values = values[counts > 15000]
    if len(candidate_values) == 0:
        return False

    height, width = codes.shape
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    for value in candidate_values:
        mask = (codes == value).astype(np.uint8) * 255
        closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
        component_count, _labels, stats, _centroids = cv2.connectedComponentsWithStats(closed, 8)
        for index in range(1, component_count):
            x, y, w, h, area = (int(item) for item in stats[index])
            touches_scan_edge = x <= 2 or y <= 2 or x + w >= width - 2 or y + h >= height - 2
            if touches_scan_edge:
                continue
            if w < 260 or h < 100 or area < 20000:
                continue
            fill_ratio = area / (w * h)
            if fill_ratio >= 0.5:
                return True
    return False


def _luminance(rgb: np.ndarray) -> np.ndarray:
    values = rgb.astype(np.float32)
    return (0.299 * values[:, :, 0]) + (0.587 * values[:, :, 1]) + (0.114 * values[:, :, 2])
