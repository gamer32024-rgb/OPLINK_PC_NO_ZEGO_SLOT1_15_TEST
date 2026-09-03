from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import time
from typing import Any

from .device import DeviceController
from .vision import Detector, best_detection


@dataclass(frozen=True)
class StepResult:
    step_type: str
    ok: bool
    message: str


@dataclass
class ScenarioRunner:
    device: DeviceController
    detector: Detector
    artifact_dir: Path = Path("runs")

    def run_file(self, path: str | Path) -> list[StepResult]:
        scenario_path = Path(path)
        with scenario_path.open("r", encoding="utf-8") as file:
            scenario = json.load(file)
        return self.run(scenario.get("steps", []))

    def run(self, steps: list[dict[str, Any]]) -> list[StepResult]:
        results: list[StepResult] = []
        for index, step in enumerate(steps):
            result = self._run_step(index, step)
            results.append(result)
            if not result.ok and not bool(step.get("optional", False)):
                break
        return results

    def _run_step(self, index: int, step: dict[str, Any]) -> StepResult:
        step_type = str(step.get("type", ""))
        try:
            if step_type == "sleep":
                time.sleep(float(step.get("seconds", 1)))
                return StepResult(step_type, True, "slept")
            if step_type == "tap_xy":
                self.device.tap(int(step["x"]), int(step["y"]))
                return StepResult(step_type, True, "tapped fixed coordinate")
            if step_type == "keyevent":
                self.device.keyevent(str(step["key"]))
                return StepResult(step_type, True, "sent keyevent")
            if step_type == "screenshot":
                path = Path(step.get("path", self.artifact_dir / f"step_{index}.png"))
                self.device.screenshot(path)
                return StepResult(step_type, True, f"saved {path}")
            if step_type == "wait_detect":
                return self._wait_detect(step)
            if step_type == "tap_detect":
                return self._tap_detect(step)
        except Exception as exc:
            return StepResult(step_type, False, str(exc))
        return StepResult(step_type, False, f"unknown step type: {step_type}")

    def _capture_for_detection(self) -> Path:
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        path = self.artifact_dir / f"{self.device.serial.replace(':', '_')}_latest.png"
        return self.device.screenshot(path)

    def _wait_detect(self, step: dict[str, Any]) -> StepResult:
        label = str(step["label"])
        confidence = float(step.get("confidence", 0.55))
        timeout_sec = float(step.get("timeout_sec", 10))
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() <= deadline:
            image_path = self._capture_for_detection()
            detection = best_detection(self.detector.detect(image_path, confidence), label, confidence)
            if detection is not None:
                return StepResult("wait_detect", True, f"found {label}")
            time.sleep(float(step.get("poll_sec", 0.5)))
        return StepResult("wait_detect", False, f"timed out waiting for {label}")

    def _tap_detect(self, step: dict[str, Any]) -> StepResult:
        label = str(step["label"])
        confidence = float(step.get("confidence", 0.55))
        image_path = self._capture_for_detection()
        detection = best_detection(self.detector.detect(image_path, confidence), label, confidence)
        if detection is None:
            return StepResult("tap_detect", False, f"{label} not found")
        x, y = detection.center
        self.device.tap(x, y)
        return StepResult("tap_detect", True, f"tapped {label} at {x},{y}")
