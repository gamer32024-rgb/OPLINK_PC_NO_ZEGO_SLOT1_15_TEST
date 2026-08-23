from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from native_capture_router_process import (
    NativeCaptureRouterProcess,
    RouterSwitchResult,
)


HOST = Path(__file__).resolve().parent
DEFAULT_EXE = HOST / "tools" / "oplink_capture_router" / "oplink_capture_router.exe"
DEFAULT_RUNTIME = HOST / "runtime" / "native_capture_router_test"
GUI_STATUS_URL = "http://127.0.0.1:5100/api/status"
LEGACY_HEALTH_URL = "http://127.0.0.1:5110/api/v1/health"


def fetch_json(url: str, timeout: float = 3.0) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"{url} returned HTTP {response.status}")
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise RuntimeError(f"{url} did not return a JSON object")
    return payload


def resolve_targets(status: dict[str, Any], slots: list[int]) -> dict[int, dict[str, Any]]:
    targets = status.get("targets")
    if not isinstance(targets, list):
        raise RuntimeError("GUI_TEST_PC status does not contain targets")

    resolved: dict[int, dict[str, Any]] = {}
    for target in targets:
        if not isinstance(target, dict):
            continue
        slot = target.get("slot")
        if slot not in slots:
            continue
        hwnd = target.get("hwnd")
        pid = target.get("pid")
        if (
            isinstance(hwnd, bool)
            or not isinstance(hwnd, int)
            or hwnd <= 0
            or isinstance(pid, bool)
            or not isinstance(pid, int)
            or pid <= 0
        ):
            raise RuntimeError(f"Slot {slot} has invalid PID/HWND identity")
        resolved[int(slot)] = {
            "slot": int(slot),
            "hwnd": hwnd,
            "pid": pid,
            "title": str(target.get("title") or ""),
            "process_path": str(target.get("process_path") or ""),
            "client_rect": target.get("client_rect"),
            "width": target.get("width"),
            "height": target.get("height"),
        }

    missing = [slot for slot in slots if slot not in resolved]
    if missing:
        raise RuntimeError(f"GUI_TEST_PC is missing requested Slots: {missing}")
    return resolved


def write_bgra_bmp(path: Path, width: int, height: int, bgra: bytes) -> None:
    expected = width * height * 4
    if len(bgra) != expected:
        raise ValueError(f"BGRA frame has {len(bgra)} bytes, expected {expected}")
    file_header_size = 14
    dib_header_size = 40
    pixel_offset = file_header_size + dib_header_size
    file_size = pixel_offset + len(bgra)
    file_header = struct.pack("<2sIHHI", b"BM", file_size, 0, 0, pixel_offset)
    dib_header = struct.pack(
        "<IiiHHIIiiII",
        dib_header_size,
        width,
        -height,
        1,
        32,
        0,
        len(bgra),
        2835,
        2835,
        0,
        0,
    )
    path.write_bytes(file_header + dib_header + bgra)


def read_bgra_frame(path: Path, frame_size: int, frame_index: int) -> bytes:
    if frame_index < 1:
        raise ValueError("stdout frame index must be positive")
    with path.open("rb") as stream:
        stream.seek((frame_index - 1) * frame_size)
        frame = stream.read(frame_size)
    if len(frame) != frame_size:
        raise RuntimeError(
            f"stdout frame {frame_index} is incomplete: "
            f"{len(frame)} of {frame_size} bytes"
        )
    return frame


def percentile(values: list[float], percentage: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(0, math.ceil(percentage * len(ordered)) - 1)
    return ordered[rank]


def result_payload(result: RouterSwitchResult) -> dict[str, Any]:
    return {
        "generation": result.generation,
        "slot": result.slot,
        "hwnd": result.hwnd,
        "switch_started": result.switch_started,
        "first_frame": result.first_frame,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run an isolated WGC router test without touching legacy services."
    )
    parser.add_argument("--exe", type=Path, default=DEFAULT_EXE)
    parser.add_argument("--slots", type=int, nargs="+", default=[1, 7, 15])
    parser.add_argument("--cycles", type=int, default=1)
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--switch-timeout", type=float, default=3.0)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--keep-raw", action="store_true")
    parser.add_argument(
        "--discard-output",
        action="store_true",
        help="drain stdout without storing frames; intended for switch stress tests",
    )
    args = parser.parse_args()

    slots = list(dict.fromkeys(args.slots))
    if not slots or any(slot < 1 or slot > 20 for slot in slots):
        parser.error("--slots must contain values from 1 through 20")
    if args.cycles <= 0:
        parser.error("--cycles must be positive")
    exe = args.exe.resolve()
    if not exe.is_file():
        raise FileNotFoundError(f"native router executable not found: {exe}")

    gui_status_before = fetch_json(GUI_STATUS_URL)
    legacy_health_before = fetch_json(LEGACY_HEALTH_URL)
    targets = resolve_targets(gui_status_before, slots)

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = args.output_dir.resolve() / run_id
    output_dir.mkdir(parents=True, exist_ok=False)
    raw_path = output_dir / "router_output.bgra"
    supervisor = NativeCaptureRouterProcess(
        [str(exe)],
        width=args.width,
        height=args.height,
        fps=args.fps,
        ready_timeout=5.0,
        switch_timeout=args.switch_timeout,
        stop_timeout=2.0,
        terminate_timeout=1.0,
        kill_timeout=1.0,
        stdout_path=None if args.discard_output else raw_path,
    )

    evidence: dict[str, Any] = {
        "run_id": run_id,
        "started_at_ms": int(time.time() * 1000),
        "mode": (
            "host_only_native_router_stress"
            if args.discard_output
            else "host_only_native_router"
        ),
        "legacy_services_modified": False,
        "profile": {
            "width": args.width,
            "height": args.height,
            "fps": args.fps,
            "format": "bgra",
        },
        "slots": slots,
        "cycles": args.cycles,
        "targets": targets,
        "legacy_health_before": {
            "ok": legacy_health_before.get("ok"),
            "profile": legacy_health_before.get("profile"),
            "encoder": legacy_health_before.get("encoder"),
        },
        "switches": [],
    }

    stop_stage = "not_started"
    try:
        ready = supervisor.start()
        evidence["router_ready"] = ready
        evidence["router_started"] = supervisor.started_event
        evidence["router_pid"] = supervisor.pid

        generation = 0
        for cycle in range(1, args.cycles + 1):
            for slot in slots:
                target = targets[slot]
                result = supervisor.switch(
                    slot=slot,
                    hwnd=int(target["hwnd"]),
                    timeout=args.switch_timeout,
                )
                generation += 1
                if result.generation != generation or supervisor.pid != evidence["router_pid"]:
                    raise RuntimeError("router PID/generation changed unexpectedly")

                switch_evidence = result_payload(result)
                switch_evidence.update(
                    {
                        "cycle": cycle,
                    }
                )
                evidence["switches"].append(switch_evidence)
    finally:
        stop_stage = supervisor.stop(reason="host_test_complete")
        evidence["stop_stage"] = stop_stage
        evidence["router_events"] = list(supervisor.event_history)
        evidence["finished_at_ms"] = int(time.time() * 1000)

        if args.discard_output:
            evidence["stdout_mode"] = "drained_and_discarded"
        else:
            frame_size = args.width * args.height * 4
            try:
                raw_bytes = raw_path.stat().st_size
                evidence["stdout_bytes"] = raw_bytes
                evidence["stdout_complete_frames"] = raw_bytes // frame_size
                for item in evidence["switches"]:
                    frame_index = int(item["first_frame"]["stdout_frame_index"])
                    frame = read_bgra_frame(raw_path, frame_size, frame_index)
                    frame_name = (
                        f"g{int(item['generation']):03d}_"
                        f"slot{int(item['slot']):02d}.bmp"
                    )
                    write_bgra_bmp(
                        output_dir / frame_name,
                        args.width,
                        args.height,
                        frame,
                    )
                    item.update(
                        {
                            "frame_file": frame_name,
                            "frame_sha256": hashlib.sha256(frame).hexdigest().upper(),
                            "frame_bytes": len(frame),
                            "stdout_frame_index": frame_index,
                        }
                    )
                if not args.keep_raw:
                    raw_path.unlink()
                    evidence["raw_output_retained"] = False
                else:
                    evidence["raw_output_retained"] = True
                    evidence["raw_output_file"] = raw_path.name
            except Exception as exc:
                evidence["frame_extract_error"] = str(exc)

        try:
            legacy_health_after = fetch_json(LEGACY_HEALTH_URL)
            evidence["legacy_health_after"] = {
                "ok": legacy_health_after.get("ok"),
                "profile": legacy_health_after.get("profile"),
                "encoder": legacy_health_after.get("encoder"),
            }
        except Exception as exc:
            evidence["legacy_health_after_error"] = str(exc)

        elapsed_values = [
            float(item["first_frame"]["elapsed_ms"])
            for item in evidence["switches"]
        ]
        evidence["latency_ms"] = {
            "count": len(elapsed_values),
            "p50": percentile(elapsed_values, 0.50),
            "p95": percentile(elapsed_values, 0.95),
            "worst": max(elapsed_values) if elapsed_values else None,
        }
        (output_dir / "evidence.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    if "frame_extract_error" in evidence:
        raise RuntimeError(str(evidence["frame_extract_error"]))

    print(
        json.dumps(
            {
                "ok": True,
                "router_pid": evidence.get("router_pid"),
                "switches": len(evidence["switches"]),
                "latency_ms": evidence["latency_ms"],
                "stop_stage": stop_stage,
                "evidence": str(output_dir / "evidence.json"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
