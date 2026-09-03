from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import queue
import time
from typing import Any

from pico_drag_mouse_probe import (
    _counts,
    _drain,
    _install_hook,
    _pump_messages,
    _source_counts,
    user32,
    wintypes,
)


WORKSPACE = Path(__file__).resolve().parents[2]
GUI_DIR = WORKSPACE / "GUI_TEST_PC_DEV_20260703"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Observe OSLink mouse drag events without sending any local input."
    )
    parser.add_argument("--duration-sec", type=float, default=20.0)
    parser.add_argument("--jsonl", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    duration_sec = max(1.0, float(args.duration_sec))
    jsonl_path = args.jsonl or _default_log_path()
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)

    events: queue.SimpleQueue[dict[str, Any]] = queue.SimpleQueue()
    captured: list[dict[str, Any]] = []
    hook, callback = _install_hook(events)
    _ = callback
    started = datetime.now().astimezone().isoformat(timespec="milliseconds")
    try:
        deadline = time.monotonic() + duration_sec
        while time.monotonic() < deadline:
            _pump_messages()
            _drain(events, captured)
            time.sleep(0.005)
        _pump_messages()
        _drain(events, captured)
    finally:
        if hook:
            user32.UnhookWindowsHookEx(wintypes.HHOOK(hook))

    with jsonl_path.open("w", encoding="utf-8") as file:
        for event in captured:
            file.write(json.dumps(event, ensure_ascii=False) + "\n")

    summary = {
        "started_at": started,
        "duration_sec": duration_sec,
        "captured_total": len(captured),
        "event_counts": _counts(captured),
        "source_counts": _source_counts(captured),
        "gestures": _summarize_gestures(captured),
        "jsonl": str(jsonl_path),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _summarize_gestures(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    gestures: list[dict[str, Any]] = []
    active: dict[str, Any] | None = None
    for event in events:
        event_type = event.get("event")
        if event_type == "left_down":
            if active is not None:
                active["missing_up"] = True
                gestures.append(active)
            active = {
                "down": event,
                "move_count": 0,
                "path_px": 0.0,
                "last_point": _point(event),
            }
            continue
        if active is None:
            continue
        if event_type == "move":
            point = _point(event)
            last_x, last_y = active["last_point"]
            active["path_px"] += ((point[0] - last_x) ** 2 + (point[1] - last_y) ** 2) ** 0.5
            active["last_point"] = point
            active["move_count"] += 1
            continue
        if event_type == "left_up":
            active["up"] = event
            active["duration_ms"] = _elapsed_ms(active["down"]["ts"], event["ts"])
            active["path_px"] = round(active["path_px"], 1)
            active.pop("last_point", None)
            gestures.append(active)
            active = None
    if active is not None:
        active["missing_up"] = True
        active["path_px"] = round(active["path_px"], 1)
        active.pop("last_point", None)
        gestures.append(active)
    return gestures


def _point(event: dict[str, Any]) -> tuple[int, int]:
    screen = event.get("screen") or {}
    return int(screen.get("x", 0)), int(screen.get("y", 0))


def _elapsed_ms(start: str, end: str) -> int:
    start_at = datetime.fromisoformat(start)
    end_at = datetime.fromisoformat(end)
    return round((end_at - start_at).total_seconds() * 1000)


def _default_log_path() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return GUI_DIR / "logs_pc" / f"oslink_drag_mouse_probe_{stamp}.jsonl"


if __name__ == "__main__":
    raise SystemExit(main())
