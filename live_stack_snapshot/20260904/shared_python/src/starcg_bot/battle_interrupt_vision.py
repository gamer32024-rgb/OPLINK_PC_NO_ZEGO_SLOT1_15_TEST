from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image

from .battle_interrupt import BattleEvidence


SIGNAL_NAMES = (
    "world_anchor",
    "result_anchor",
    "battle_anchor",
    "timer_visible",
    "more_visible",
    "auto_visible",
    "cancel_visible",
    "ambush_visible",
    "success_visible",
)


@dataclass(frozen=True)
class TemplateVariant:
    path: Path
    threshold: float


@dataclass(frozen=True)
class SignalDefinition:
    name: str
    roi: tuple[int, int, int, int]
    templates: tuple[TemplateVariant, ...]
    click_point: tuple[int, int] | None = None


@dataclass(frozen=True)
class VisionResult:
    evidence: BattleEvidence
    black_ratio: float
    motion_score: float | None
    hashes: dict[str, str]


class BattleVisionAnalyzer:
    def __init__(self, config_path: str | Path) -> None:
        self.config_path = Path(config_path).resolve()
        payload = json.loads(self.config_path.read_text(encoding="utf-8-sig"))
        expected = payload.get("expected_client") or {}
        self.expected_size = (int(expected.get("w", 0)), int(expected.get("h", 0)))
        transition = payload.get("transition") or {}
        self.black_pixel_max = int(transition.get("black_pixel_max", 20))
        self.black_ratio_min = float(transition.get("black_ratio_min", 0.80))
        self.motion_score_min = float(transition.get("motion_score_min", 0.18))
        self.signals = self._load_signals(payload.get("signals") or {})
        self._templates = self._load_template_images()
        self._previous_gray: np.ndarray | None = None

    def analyze(self, image: Image.Image) -> VisionResult:
        rgb = np.asarray(image.convert("RGB"))
        height, width = rgb.shape[:2]
        correct_size = (width, height) == self.expected_size
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        black_ratio = float(np.mean(np.max(rgb, axis=2) < self.black_pixel_max))
        motion_score = None
        if self._previous_gray is not None and self._previous_gray.shape == gray.shape:
            motion_score = float(np.mean(cv2.absdiff(gray, self._previous_gray)) / 255.0)
        self._previous_gray = gray

        scores: dict[str, float] = {}
        matched: dict[str, bool | None] = {name: None for name in SIGNAL_NAMES}
        hashes: dict[str, str] = {}
        if correct_size:
            for name, definition in self.signals.items():
                roi_image = image.crop(definition.roi).convert("RGB")
                hashes[name] = perceptual_hash(roi_image)
                score, threshold = self._match_signal(name, roi_image)
                scores[name] = score
                matched[name] = score >= threshold

        structural_visible = any(
            matched.get(name) is True
            for name in ("world_anchor", "result_anchor", "battle_anchor")
        )
        transition = black_ratio >= self.black_ratio_min or (
            motion_score is not None
            and motion_score >= self.motion_score_min
            and not structural_visible
        )
        evidence = BattleEvidence(
            captured=True,
            correct_size=correct_size,
            transition=transition,
            world_anchor=matched["world_anchor"],
            result_anchor=matched["result_anchor"],
            battle_anchor=matched["battle_anchor"],
            timer_visible=matched["timer_visible"],
            more_visible=matched["more_visible"],
            auto_visible=matched["auto_visible"],
            cancel_visible=matched["cancel_visible"],
            ambush_visible=matched["ambush_visible"],
            success_visible=matched["success_visible"],
            scores=scores,
        )
        return VisionResult(
            evidence=evidence,
            black_ratio=black_ratio,
            motion_score=motion_score,
            hashes=hashes,
        )

    def click_point(self, signal_name: str) -> tuple[int, int]:
        definition = self.signals.get(signal_name)
        if definition is None or definition.click_point is None:
            raise RuntimeError(f"signal {signal_name!r} has no configured click point")
        return definition.click_point

    def _load_signals(self, values: dict[str, Any]) -> dict[str, SignalDefinition]:
        result: dict[str, SignalDefinition] = {}
        for name in SIGNAL_NAMES:
            value = values.get(name)
            if not isinstance(value, dict):
                continue
            roi_values = value.get("roi") or []
            if len(roi_values) != 4:
                raise ValueError(f"signal {name!r} requires roi=[left, top, right, bottom]")
            roi = tuple(int(item) for item in roi_values)
            left, top, right, bottom = roi
            if min(left, top) < 0 or right <= left or bottom <= top:
                raise ValueError(f"signal {name!r} has invalid ROI: {roi}")
            variants: list[TemplateVariant] = []
            for item in value.get("templates") or []:
                path = Path(str(item.get("path") or ""))
                if not path.is_absolute():
                    path = (self.config_path.parent / path).resolve()
                variants.append(
                    TemplateVariant(
                        path=path,
                        threshold=float(item.get("threshold", 0.88)),
                    )
                )
            if not variants:
                raise ValueError(f"signal {name!r} requires at least one template")
            click_values = value.get("click_point")
            click_point = None
            if click_values is not None:
                if len(click_values) != 2:
                    raise ValueError(f"signal {name!r} click_point must contain x,y")
                click_point = (int(click_values[0]), int(click_values[1]))
            result[name] = SignalDefinition(
                name=name,
                roi=roi,
                templates=tuple(variants),
                click_point=click_point,
            )
        return result

    def _load_template_images(self) -> dict[str, list[tuple[np.ndarray, float]]]:
        loaded: dict[str, list[tuple[np.ndarray, float]]] = {}
        for name, definition in self.signals.items():
            items: list[tuple[np.ndarray, float]] = []
            for variant in definition.templates:
                if not variant.path.exists():
                    raise FileNotFoundError(f"missing template for {name}: {variant.path}")
                image = cv2.imread(str(variant.path), cv2.IMREAD_GRAYSCALE)
                if image is None:
                    raise RuntimeError(f"could not read template for {name}: {variant.path}")
                items.append((image, variant.threshold))
            loaded[name] = items
        return loaded

    def _match_signal(self, name: str, roi_image: Image.Image) -> tuple[float, float]:
        roi_gray = cv2.cvtColor(np.asarray(roi_image), cv2.COLOR_RGB2GRAY)
        best_score = -1.0
        best_threshold = 1.0
        for template, threshold in self._templates[name]:
            if template.shape[0] > roi_gray.shape[0] or template.shape[1] > roi_gray.shape[1]:
                continue
            result = cv2.matchTemplate(roi_gray, template, cv2.TM_CCOEFF_NORMED)
            score = float(np.nanmax(result)) if result.size else -1.0
            if score > best_score:
                best_score = score
                best_threshold = threshold
        return best_score, best_threshold


class DeduplicatingCaptureCollector:
    def __init__(
        self,
        root: str | Path,
        signals: dict[str, SignalDefinition],
        *,
        hash_distance_min: int = 8,
    ) -> None:
        self.root = Path(root)
        self.signals = signals
        self.hash_distance_min = max(1, int(hash_distance_min))
        self._last_hash_by_label: dict[str, str] = {}

    def save_candidate(
        self,
        image: Image.Image,
        *,
        label: str,
        force: bool = False,
    ) -> dict[str, str]:
        normalized = _safe_label(label)
        current_hash = perceptual_hash(image)
        previous_hash = self._last_hash_by_label.get(normalized)
        if not force and previous_hash and hamming_distance(previous_hash, current_hash) < self.hash_distance_min:
            return {}
        self._last_hash_by_label[normalized] = current_hash
        stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
        saved: dict[str, str] = {}
        frame_path = self.root / "frames" / normalized / f"{stamp}.png"
        frame_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(frame_path)
        saved["frame"] = str(frame_path)
        for name, definition in self.signals.items():
            roi_path = self.root / "rois" / name / normalized / f"{stamp}.png"
            roi_path.parent.mkdir(parents=True, exist_ok=True)
            image.crop(definition.roi).save(roi_path)
            saved[name] = str(roi_path)
        return saved


def perceptual_hash(image: Image.Image, size: int = 16) -> str:
    gray = np.asarray(image.convert("L").resize((size, size), Image.Resampling.BILINEAR))
    bits = gray >= float(np.mean(gray))
    value = 0
    for bit in bits.flat:
        value = (value << 1) | int(bool(bit))
    return f"{value:0{size * size // 4}x}"


def hamming_distance(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


def _safe_label(value: str) -> str:
    text = "".join(char if char.isalnum() or char in "-_" else "_" for char in str(value))
    return text.strip("_") or "unknown"
