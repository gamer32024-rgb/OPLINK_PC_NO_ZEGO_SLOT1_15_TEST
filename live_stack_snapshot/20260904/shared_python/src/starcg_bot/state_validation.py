from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .state_detector import ScreenState, WorldStateDetector


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}


@dataclass(frozen=True)
class ValidationFailure:
    folder: str
    image: str
    state: ScreenState

    def to_dict(self) -> dict[str, Any]:
        return {
            "folder": self.folder,
            "image": self.image,
            "state": self.state.to_dict(),
        }


def validate_state_dataset(root: Path, detector: WorldStateDetector) -> dict[str, Any]:
    root = root.resolve()
    folder_summaries: dict[str, dict[str, Any]] = {}
    unsafe_actionable_non_world: list[ValidationFailure] = []
    world_not_actionable: list[ValidationFailure] = []
    total_images = 0

    for folder in sorted(path for path in root.iterdir() if path.is_dir()):
        predictions: Counter[str] = Counter()
        folder_total = 0
        for image_path in _iter_images(folder):
            folder_total += 1
            total_images += 1
            state = detector.detect_image(image_path)
            predictions[_prediction_key(state)] += 1

            failure = ValidationFailure(folder.name, _relative_text(image_path, root), state)
            if folder.name == "world" and not state.actionable:
                world_not_actionable.append(failure)
            if folder.name != "world" and state.actionable:
                unsafe_actionable_non_world.append(failure)

        folder_summaries[folder.name] = {
            "total": folder_total,
            "predictions": dict(sorted(predictions.items())),
        }

    return {
        "root": str(root),
        "total_images": total_images,
        "passed": not unsafe_actionable_non_world and not world_not_actionable,
        "folders": folder_summaries,
        "unsafe_actionable_non_world_count": len(unsafe_actionable_non_world),
        "unsafe_actionable_non_world": [item.to_dict() for item in unsafe_actionable_non_world],
        "world_not_actionable_count": len(world_not_actionable),
        "world_not_actionable": [item.to_dict() for item in world_not_actionable],
    }


def _iter_images(folder: Path) -> list[Path]:
    return sorted(
        path
        for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    )


def _prediction_key(state: ScreenState) -> str:
    actionable = "actionable" if state.actionable else "blocked"
    return f"{state.main_state}|{state.overlay}|{state.transition}|{actionable}"


def _relative_text(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)
