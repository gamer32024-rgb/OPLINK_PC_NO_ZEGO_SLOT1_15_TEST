from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

from .config import EmulatorConfig, TargetConfig
from .device import AdbDeviceController, DeviceController
from .scenario import ScenarioRunner, StepResult
from .vision import Detector, YoloDetector

WorkerTarget: TypeAlias = EmulatorConfig | TargetConfig


@dataclass(frozen=True)
class WorkerResult:
    name: str
    serial: str
    ok: bool
    steps: list[StepResult]


def run_worker(
    target: WorkerTarget,
    scenario_path: str | Path,
    model_path: str | Path | None = None,
    device: DeviceController | None = None,
    detector: Detector | None = None,
) -> WorkerResult:
    target_config = _coerce_target(target)
    active_device = device or create_device_controller(target_config)
    active_detector = detector or YoloDetector(model_path)  # type: ignore[arg-type]
    runner = ScenarioRunner(
        device=active_device,
        detector=active_detector,
        artifact_dir=Path("runs") / target_config.name,
    )
    steps = runner.run_file(scenario_path)
    return WorkerResult(
        name=target_config.name,
        serial=active_device.serial,
        ok=all(step.ok for step in steps),
        steps=steps,
    )


def run_worker_pool(
    targets: list[WorkerTarget],
    scenario_path: str | Path,
    model_path: str | Path,
    max_workers: int,
) -> list[WorkerResult]:
    results: list[WorkerResult] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(run_worker, target, scenario_path, model_path)
            for target in targets
        ]
        for future in as_completed(futures):
            results.append(future.result())
    return results


def create_device_controller(target: TargetConfig) -> DeviceController:
    if target.target_type == "adb":
        if not target.serial:
            raise RuntimeError(f"ADB target {target.name!r} requires serial")
        return AdbDeviceController(target.serial)
    if target.target_type == "windows":
        from .windows_device import WindowsWindowController

        return WindowsWindowController.from_locator(
            hwnd=target.hwnd,
            title_contains=target.window_title,
            process_name=target.process_name,
            process_path=target.game_path,
            match_index=target.match_index,
            serial=target.name,
        )
    raise RuntimeError(f"unsupported target type for {target.name!r}: {target.target_type!r}")


def _coerce_target(target: WorkerTarget) -> TargetConfig:
    if isinstance(target, TargetConfig):
        return target
    return TargetConfig(
        name=target.name,
        target_type="adb",
        enabled=target.enabled,
        serial=target.serial,
    )
