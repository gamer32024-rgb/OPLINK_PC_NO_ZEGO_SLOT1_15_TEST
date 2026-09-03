from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class Detection:
    label: str
    confidence: float
    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def center(self) -> tuple[int, int]:
        return ((self.x1 + self.x2) // 2, (self.y1 + self.y2) // 2)


class Detector(Protocol):
    def detect(self, image_path: str | Path, confidence: float = 0.55) -> list[Detection]:
        ...


class YoloDetector:
    def __init__(self, model_path: str | Path) -> None:
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise RuntimeError("ultralytics is required for YOLO detection. Install with `python -m pip install -e .`.") from exc
        self.model_path = Path(model_path)
        self.model = YOLO(str(self.model_path))

    def detect(self, image_path: str | Path, confidence: float = 0.55) -> list[Detection]:
        results = self.model.predict(str(image_path), conf=confidence, verbose=False)
        detections: list[Detection] = []
        for result in results:
            names = result.names
            for box in result.boxes:
                cls_id = int(box.cls[0].item())
                score = float(box.conf[0].item())
                x1, y1, x2, y2 = (int(value) for value in box.xyxy[0].tolist())
                detections.append(
                    Detection(
                        label=str(names.get(cls_id, cls_id)),
                        confidence=score,
                        x1=x1,
                        y1=y1,
                        x2=x2,
                        y2=y2,
                    )
                )
        return detections


class NullDetector:
    def detect(self, image_path: str | Path, confidence: float = 0.55) -> list[Detection]:
        return []


def best_detection(detections: list[Detection], label: str, confidence: float) -> Detection | None:
    matches = [item for item in detections if item.label == label and item.confidence >= confidence]
    if not matches:
        return None
    return max(matches, key=lambda item: item.confidence)
