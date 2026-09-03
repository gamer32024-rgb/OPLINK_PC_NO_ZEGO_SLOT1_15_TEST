from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import shutil
import sys

from .collector import ScreenshotCollector
from .config import load_config
from .device import AdbDeviceController, list_adb_devices
from .json_io import json_text
from .scenario import ScenarioRunner
from .vision import NullDetector, YoloDetector
from .worker import run_worker_pool


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="starcg-bot")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor")
    subparsers.add_parser("devices")
    subparsers.add_parser("coordinate-capture")

    pc_input_audit = subparsers.add_parser("pc-input-audit")
    pc_input_audit.add_argument("--probe-log")

    pc_record_script = subparsers.add_parser("pc-record-script")
    pc_record_script.add_argument("--hwnd")
    pc_record_script.add_argument("--title")
    pc_record_script.add_argument("--process-name", default="StarCG")
    pc_record_script.add_argument("--path-prefix", default=r"D:\TWFULLPC1.2.76_multi")
    pc_record_script.add_argument("--process-path")
    pc_record_script.add_argument("--match-index", type=int)
    pc_record_script.add_argument("--seconds", type=float, default=30.0)
    pc_record_script.add_argument("--output")
    pc_record_script.add_argument("--click-max-duration-ms", type=int, default=180)
    pc_record_script.add_argument("--click-max-move-px", type=int, default=3)

    pc_play_script = subparsers.add_parser("pc-play-script")
    pc_play_script.add_argument("--hwnd")
    pc_play_script.add_argument("--title")
    pc_play_script.add_argument("--process-name", default="StarCG")
    pc_play_script.add_argument("--path-prefix", default=r"D:\TWFULLPC1.2.76_multi")
    pc_play_script.add_argument("--process-path")
    pc_play_script.add_argument("--match-index", type=int)
    pc_play_script.add_argument("--script", required=True)
    pc_play_script.add_argument("--speed", type=float, default=1.0)
    pc_play_script.add_argument("--dry-run", action="store_true")
    pc_play_script.add_argument("--allow-size-mismatch", action="store_true")
    pc_play_script.add_argument(
        "--allow-window-input",
        action="store_true",
        help="Explicitly allow Windows input injection for local QA playback.",
    )

    capture = subparsers.add_parser("capture")
    capture.add_argument("--serial", required=True)
    capture.add_argument("--tag", default="sample")
    capture.add_argument("--output-dir", default="datasets/raw")

    windows_list = subparsers.add_parser("windows-list")
    windows_list.add_argument("--title")
    windows_list.add_argument("--process-name")
    windows_list.add_argument("--path-prefix")
    windows_list.add_argument("--process-path")
    windows_list.add_argument("--all", action="store_true")

    capture_window = subparsers.add_parser("capture-window")
    capture_window.add_argument("--hwnd")
    capture_window.add_argument("--title")
    capture_window.add_argument("--process-name")
    capture_window.add_argument("--path-prefix")
    capture_window.add_argument("--process-path")
    capture_window.add_argument("--match-index", type=int)
    capture_window.add_argument("--tag", default="window")
    capture_window.add_argument("--output-dir", default="datasets/raw")

    window_tap = subparsers.add_parser("window-tap")
    window_tap.add_argument("--hwnd")
    window_tap.add_argument("--title")
    window_tap.add_argument("--process-name", default="StarCG")
    window_tap.add_argument("--path-prefix", default=r"D:\TWFULLPC1.2.76_multi")
    window_tap.add_argument("--process-path")
    window_tap.add_argument("--match-index", type=int)
    window_tap.add_argument("--x", type=int, required=True)
    window_tap.add_argument("--y", type=int, required=True)

    window_text = subparsers.add_parser("window-text")
    window_text.add_argument("--hwnd")
    window_text.add_argument("--title")
    window_text.add_argument("--process-name", default="StarCG")
    window_text.add_argument("--path-prefix", default=r"D:\TWFULLPC1.2.76_multi")
    window_text.add_argument("--process-path")
    window_text.add_argument("--match-index", type=int)
    window_text.add_argument("--text", required=True)

    window_key = subparsers.add_parser("window-key")
    window_key.add_argument("--hwnd")
    window_key.add_argument("--title")
    window_key.add_argument("--process-name", default="StarCG")
    window_key.add_argument("--path-prefix", default=r"D:\TWFULLPC1.2.76_multi")
    window_key.add_argument("--process-path")
    window_key.add_argument("--match-index", type=int)
    window_key.add_argument("--key", required=True)

    detect = subparsers.add_parser("detect")
    detect.add_argument("--model", required=True)
    detect.add_argument("--image", required=True)
    detect.add_argument("--confidence", type=float, default=0.55)

    run_scenario = subparsers.add_parser("run-scenario")
    run_scenario.add_argument("--serial", required=True)
    run_scenario.add_argument("--scenario", required=True)
    run_scenario.add_argument("--model")
    run_scenario.add_argument("--dry-run", action="store_true")

    run_scenario_window = subparsers.add_parser("run-scenario-window")
    run_scenario_window.add_argument("--hwnd")
    run_scenario_window.add_argument("--title")
    run_scenario_window.add_argument("--process-name")
    run_scenario_window.add_argument("--path-prefix")
    run_scenario_window.add_argument("--process-path")
    run_scenario_window.add_argument("--match-index", type=int)
    run_scenario_window.add_argument("--scenario", required=True)
    run_scenario_window.add_argument("--model")
    run_scenario_window.add_argument("--dry-run", action="store_true")
    run_scenario_window.add_argument(
        "--allow-window-input",
        action="store_true",
        help="Explicitly allow Windows input injection for local QA playback.",
    )

    run_pool = subparsers.add_parser("run-pool")
    run_pool.add_argument("--config", required=True)
    run_pool.add_argument("--scenario", required=True)
    run_pool.add_argument("--model", required=True)

    roi_preview = subparsers.add_parser("ocr-roi-preview")
    roi_preview.add_argument("--input-dir", default="datasets/ocr/world")
    roi_preview.add_argument("--output", default="runs/ocr_roi_preview.png")
    roi_preview.add_argument("--map-roi", default="760,6,190,30")
    roi_preview.add_argument("--coord-roi", default="780,152,170,24")
    roi_preview.add_argument("--limit", type=int, default=32)

    ocr_manifest = subparsers.add_parser("ocr-manifest")
    ocr_manifest.add_argument("--input-dir", default="datasets/ocr/world")
    ocr_manifest.add_argument("--output", default="datasets/ocr/world/labels.csv")

    ocr_probe = subparsers.add_parser("ocr-probe")
    ocr_probe.add_argument("--labels", default="datasets/ocr/world/labels.csv")
    ocr_probe.add_argument("--image-dir")
    ocr_probe.add_argument("--east-number-roi", default="828,158,36,20")
    ocr_probe.add_argument("--south-number-roi", default="892,158,44,20")
    ocr_probe.add_argument("--mode", choices=["leave-one-out", "self"], default="leave-one-out")
    ocr_probe.add_argument("--target-complete", type=int, default=28)
    ocr_probe.add_argument("--report", default="runs/ocr_probe_report.json")
    ocr_probe.add_argument("--debug-dir", default="runs/ocr_probe_debug")
    ocr_probe.add_argument("--debug-all", action="store_true")
    ocr_probe.add_argument("--no-debug-crops", action="store_true")

    state_detect = subparsers.add_parser("state-detect")
    state_detect.add_argument("--image", required=True)
    state_detect.add_argument("--labels", default="datasets/ocr/world/labels.csv")
    state_detect.add_argument("--image-dir")
    state_detect.add_argument("--east-number-roi", default="828,158,36,20")
    state_detect.add_argument("--south-number-roi", default="892,158,44,20")

    state_validate = subparsers.add_parser("state-validate")
    state_validate.add_argument("--input-dir", default="datasets/state")
    state_validate.add_argument("--labels", default="datasets/ocr/world/labels.csv")
    state_validate.add_argument("--image-dir")
    state_validate.add_argument("--east-number-roi", default="828,158,36,20")
    state_validate.add_argument("--south-number-roi", default="892,158,44,20")
    state_validate.add_argument("--report", default="runs/state_validation_report.json")

    args = parser.parse_args(argv)

    if args.command == "doctor":
        return _doctor()
    if args.command == "devices":
        return _devices()
    if args.command == "coordinate-capture":
        from .coordinate_capture_app import main as capture_app_main

        return capture_app_main()
    if args.command == "pc-input-audit":
        return _pc_input_audit(args.probe_log)
    if args.command == "pc-record-script":
        return _pc_record_script(args)
    if args.command == "pc-play-script":
        return _pc_play_script(args)
    if args.command == "capture":
        return _capture(args.serial, args.tag, Path(args.output_dir))
    if args.command == "windows-list":
        return _windows_list(args.title, args.process_name, args.process_path, args.path_prefix, not args.all)
    if args.command == "capture-window":
        return _capture_window(
            args.hwnd,
            args.title,
            args.process_name,
            args.process_path,
            args.path_prefix,
            args.match_index,
            args.tag,
            Path(args.output_dir),
        )
    if args.command == "window-tap":
        return _window_tap(args)
    if args.command == "window-text":
        return _window_text(args)
    if args.command == "window-key":
        return _window_key(args)
    if args.command == "detect":
        return _detect(Path(args.model), Path(args.image), args.confidence)
    if args.command == "run-scenario":
        return _run_scenario(args.serial, Path(args.scenario), args.model, args.dry_run)
    if args.command == "run-scenario-window":
        return _run_scenario_window(
            args.hwnd,
            args.title,
            args.process_name,
            args.process_path,
            args.path_prefix,
            args.match_index,
            Path(args.scenario),
            args.model,
            args.dry_run,
            args.allow_window_input,
        )
    if args.command == "run-pool":
        return _run_pool(Path(args.config), Path(args.scenario), Path(args.model))
    if args.command == "ocr-roi-preview":
        from .ocr_roi_preview import main as roi_preview_main

        return roi_preview_main(
            [
                "--input-dir",
                args.input_dir,
                "--output",
                args.output,
                "--map-roi",
                args.map_roi,
                "--coord-roi",
                args.coord_roi,
                "--limit",
                str(args.limit),
            ]
        )
    if args.command == "ocr-manifest":
        from .ocr_manifest import main as ocr_manifest_main

        return ocr_manifest_main(["--input-dir", args.input_dir, "--output", args.output])
    if args.command == "ocr-probe":
        from .ocr_probe import main as ocr_probe_main

        probe_args = [
            "--labels",
            args.labels,
            "--east-number-roi",
            args.east_number_roi,
            "--south-number-roi",
            args.south_number_roi,
            "--mode",
            args.mode,
            "--target-complete",
            str(args.target_complete),
            "--report",
            args.report,
            "--debug-dir",
            args.debug_dir,
        ]
        if args.image_dir:
            probe_args.extend(["--image-dir", args.image_dir])
        if args.debug_all:
            probe_args.append("--debug-all")
        if args.no_debug_crops:
            probe_args.append("--no-debug-crops")
        return ocr_probe_main(probe_args)
    if args.command == "state-detect":
        from .ocr_probe import Roi
        from .state_detector import WorldStateDetector

        labels_path = Path(args.labels)
        image_dir = Path(args.image_dir) if args.image_dir else labels_path.parent
        detector = WorldStateDetector.from_ocr_labels(
            labels_path,
            image_dir=image_dir,
            east_roi=Roi.parse(args.east_number_roi),
            south_roi=Roi.parse(args.south_number_roi),
        )
        print(json_text(detector.detect_image(Path(args.image)).to_dict()))
        return 0
    if args.command == "state-validate":
        from .json_io import write_json_file
        from .ocr_probe import Roi
        from .state_detector import WorldStateDetector
        from .state_validation import validate_state_dataset

        labels_path = Path(args.labels)
        image_dir = Path(args.image_dir) if args.image_dir else labels_path.parent
        detector = WorldStateDetector.from_ocr_labels(
            labels_path,
            image_dir=image_dir,
            east_roi=Roi.parse(args.east_number_roi),
            south_roi=Roi.parse(args.south_number_roi),
        )
        report = validate_state_dataset(Path(args.input_dir), detector)
        if args.report:
            write_json_file(Path(args.report), report)
        print(json_text(report))
        return 0 if report["passed"] else 1
    return 2


def _doctor() -> int:
    checks = {
        "python": sys.version.split()[0],
        "adb.exe": shutil.which("adb") or "not found",
        "adbutils": _module_status("adbutils"),
        "ultralytics": _module_status("ultralytics"),
        "cv2": _module_status("cv2"),
        "PIL": _module_status("PIL"),
    }
    print(json_text(checks))
    return 0


def _module_status(name: str) -> str:
    return "ok" if importlib.util.find_spec(name) else "not installed"


def _devices() -> int:
    for serial in list_adb_devices():
        print(serial)
    return 0


def _pc_input_audit(probe_log: str | None) -> int:
    from .pc_input_audit import audit_report

    print(json_text(audit_report(probe_log)))
    return 0


def _pc_record_script(args: argparse.Namespace) -> int:
    from .pc_script import record_pc_script

    print("Recording left-click taps/drags. Press F8 to force a drag; press Ctrl+C to stop early.")
    result = record_pc_script(
        output=args.output,
        seconds=args.seconds,
        hwnd=args.hwnd,
        title_contains=args.title,
        process_name=args.process_name,
        process_path=args.process_path,
        process_path_prefix=args.path_prefix,
        match_index=args.match_index,
        click_max_duration_ms=args.click_max_duration_ms,
        click_max_move_px=args.click_max_move_px,
    )
    print(json_text(result))
    return 0


def _pc_play_script(args: argparse.Namespace) -> int:
    if not args.dry_run and not args.allow_window_input:
        print(
            "PC script playback is disabled by default because the current Windows backend uses "
            "automation APIs. Use --dry-run to inspect timing/actions, or --allow-window-input "
            "only for local QA playback."
        )
        return 2

    from .pc_script import play_pc_script

    result = play_pc_script(
        script_path=args.script,
        hwnd=args.hwnd,
        title_contains=args.title,
        process_name=args.process_name,
        process_path=args.process_path,
        process_path_prefix=args.path_prefix,
        match_index=args.match_index,
        speed=args.speed,
        allow_size_mismatch=args.allow_size_mismatch,
        dry_run=args.dry_run,
    )
    print(json_text(result))
    return 0


def _capture(serial: str, tag: str, output_dir: Path) -> int:
    collector = ScreenshotCollector(output_dir)
    path = collector.capture(AdbDeviceController(serial), tag)
    print(path)
    return 0


def _windows_list(
    title: str | None,
    process_name: str | None,
    process_path: str | None,
    path_prefix: str | None,
    visible_only: bool,
) -> int:
    from .windows_device import list_windows

    print(
        json_text(
            [
                item.to_dict()
                for item in list_windows(
                    title_contains=title,
                    process_name=process_name,
                    process_path=process_path,
                    process_path_prefix=path_prefix,
                    visible_only=visible_only,
                )
            ]
        )
    )
    return 0


def _capture_window(
    hwnd: str | None,
    title: str | None,
    process_name: str | None,
    process_path: str | None,
    path_prefix: str | None,
    match_index: int | None,
    tag: str,
    output_dir: Path,
) -> int:
    collector = ScreenshotCollector(output_dir)
    device = _windows_device(hwnd, title, process_name, process_path, path_prefix, match_index)
    path = collector.capture(device, tag)
    print(path)
    return 0


def _window_tap(args: argparse.Namespace) -> int:
    device = _windows_device(
        args.hwnd,
        args.title,
        args.process_name,
        args.process_path,
        args.path_prefix,
        args.match_index,
    )
    device.tap(args.x, args.y)
    print(f"tap {args.x},{args.y} -> {device.serial}")
    return 0


def _window_text(args: argparse.Namespace) -> int:
    device = _windows_device(
        args.hwnd,
        args.title,
        args.process_name,
        args.process_path,
        args.path_prefix,
        args.match_index,
    )
    device.text(args.text)
    print(f"text {len(args.text)} chars -> {device.serial}")
    return 0


def _window_key(args: argparse.Namespace) -> int:
    device = _windows_device(
        args.hwnd,
        args.title,
        args.process_name,
        args.process_path,
        args.path_prefix,
        args.match_index,
    )
    device.keyevent(args.key)
    print(f"key {args.key} -> {device.serial}")
    return 0


def _detect(model: Path, image: Path, confidence: float) -> int:
    detector = YoloDetector(model)
    detections = [item.__dict__ for item in detector.detect(image, confidence)]
    print(json_text(detections))
    return 0


def _run_scenario(serial: str, scenario: Path, model: str | None, dry_run: bool) -> int:
    device = _DryRunDevice(serial) if dry_run else AdbDeviceController(serial)
    detector = NullDetector() if dry_run or model is None else YoloDetector(model)
    results = ScenarioRunner(device=device, detector=detector).run_file(scenario)
    for result in results:
        status = "OK" if result.ok else "FAIL"
        print(f"{status} {result.step_type}: {result.message}")
    return 0 if all(result.ok for result in results) else 1


def _run_scenario_window(
    hwnd: str | None,
    title: str | None,
    process_name: str | None,
    process_path: str | None,
    path_prefix: str | None,
    match_index: int | None,
    scenario: Path,
    model: str | None,
    dry_run: bool,
    allow_window_input: bool,
) -> int:
    if not dry_run and not allow_window_input:
        print(
            "Windows scenario playback is disabled by default because the current PC input path uses "
            "automation APIs that can be distinguished from physical mouse input. Use --dry-run for "
            "validation, or --allow-window-input only for local QA playback."
        )
        return 2
    device = (
        _DryRunDevice("windows-dry-run")
        if dry_run
        else _windows_device(hwnd, title, process_name, process_path, path_prefix, match_index)
    )
    detector = NullDetector() if dry_run or model is None else YoloDetector(model)
    results = ScenarioRunner(device=device, detector=detector).run_file(scenario)
    for result in results:
        status = "OK" if result.ok else "FAIL"
        print(f"{status} {result.step_type}: {result.message}")
    return 0 if all(result.ok for result in results) else 1


def _run_pool(config_path: Path, scenario: Path, model: Path) -> int:
    config = load_config(config_path)
    results = run_worker_pool(
        list(config.enabled_targets),
        scenario,
        model,
        max_workers=config.runtime.max_workers,
    )
    for worker in results:
        status = "OK" if worker.ok else "FAIL"
        print(f"{status} {worker.name} {worker.serial}")
    return 0 if all(result.ok for result in results) else 1


def _windows_device(
    hwnd: str | None,
    title: str | None,
    process_name: str | None,
    process_path: str | None,
    path_prefix: str | None,
    match_index: int | None,
):
    from .windows_device import WindowsWindowController

    return WindowsWindowController.from_locator(
        hwnd=hwnd,
        title_contains=title,
        process_name=process_name,
        process_path=process_path,
        process_path_prefix=path_prefix,
        match_index=match_index,
    )


class _DryRunDevice:
    def __init__(self, serial: str) -> None:
        self.serial = serial

    def screenshot(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"dry-run")
        return target

    def tap(self, x: int, y: int) -> None:
        print(f"DRY tap {x},{y}")

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_sec: float = 0.3) -> None:
        print(f"DRY swipe {x1},{y1}->{x2},{y2} {duration_sec}s")

    def keyevent(self, key: str) -> None:
        print(f"DRY keyevent {key}")


if __name__ == "__main__":
    raise SystemExit(main())
