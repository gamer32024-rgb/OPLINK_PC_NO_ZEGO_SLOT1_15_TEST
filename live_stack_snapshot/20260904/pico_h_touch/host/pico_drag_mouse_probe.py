from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime
import json
from pathlib import Path
import queue
import sys
import threading
import time
import traceback
from typing import Any


WORKSPACE = Path(__file__).resolve().parents[2]
SRC_DIR = WORKSPACE / "src"
GUI_DIR = WORKSPACE / "GUI_TEST_PC_DEV_20260703"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from starcg_bot.pico_touch import pico_touch_player  # noqa: E402
from starcg_bot.windows_device import client_size, list_windows  # noqa: E402


WH_MOUSE_LL = 14
HC_ACTION = 0
PM_REMOVE = 0x0001
GA_ROOT = 2

WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202

LLMHF_INJECTED = 0x00000001
LLMHF_LOWER_IL_INJECTED = 0x00000002

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong
LRESULT = ctypes.c_ssize_t


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", POINT),
    ]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


if not sys.platform.startswith("win"):
    raise SystemExit("pico_drag_mouse_probe.py only supports Windows.")


user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.PeekMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
user32.PeekMessageW.restype = wintypes.BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
user32.TranslateMessage.restype = wintypes.BOOL
user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.restype = LRESULT
user32.WindowFromPoint.argtypes = [POINT]
user32.WindowFromPoint.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Send one controlled Pico HID drag while logging Windows low-level mouse promotion."
    )
    parser.add_argument("--hwnd", type=lambda text: int(str(text), 0), default=None)
    parser.add_argument("--slot", type=int, default=1)
    parser.add_argument("--duration-ms", type=int, default=1200)
    parser.add_argument("--hold-before-ms", type=int, default=250)
    parser.add_argument("--hold-after-ms", type=int, default=150)
    parser.add_argument("--tail-ms", type=int, default=1000)
    parser.add_argument("--steps", type=int, default=30)
    parser.add_argument("--script", type=Path, default=GUI_DIR / "scripts_pc" / "TEST2.pcscript.json")
    parser.add_argument("--pico-config", type=Path, default=GUI_DIR / "config_pc" / "pico_touch.json")
    parser.add_argument("--jsonl", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target = _resolve_target(args.hwnd)
    width, height = client_size(target["hwnd"])
    start, end = _first_drag_points(args.script, current_w=width, current_h=height)
    jsonl_path = args.jsonl or _default_log_path()
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)

    events: queue.SimpleQueue[dict[str, Any]] = queue.SimpleQueue()
    stop = threading.Event()
    sender_error: list[str] = []
    hook, callback = _install_hook(events)
    _ = callback

    sender = threading.Thread(
        target=_send_drag_worker,
        kwargs={
            "stop": stop,
            "error_out": sender_error,
            "slot": args.slot,
            "hwnd": target["hwnd"],
            "config_path": args.pico_config,
            "start": start,
            "end": end,
            "duration_ms": args.duration_ms,
            "hold_before_ms": args.hold_before_ms,
            "hold_after_ms": args.hold_after_ms,
            "steps": args.steps,
        },
        daemon=True,
    )

    captured: list[dict[str, Any]] = []
    started = datetime.now().astimezone().isoformat(timespec="milliseconds")
    try:
        sender.start()
        deadline = time.monotonic() + max(3.0, args.duration_ms / 1000.0 + 4.0)
        tail_deadline: float | None = None
        while time.monotonic() < deadline:
            _pump_messages()
            _drain(events, captured)
            if stop.is_set():
                if tail_deadline is None:
                    tail_deadline = time.monotonic() + max(0, args.tail_ms) / 1000.0
                elif time.monotonic() >= tail_deadline and events.empty():
                    break
            time.sleep(0.005)
        sender.join(timeout=2.0)
        _pump_messages()
        _drain(events, captured)
    finally:
        if hook:
            user32.UnhookWindowsHookEx(wintypes.HHOOK(hook))

    with jsonl_path.open("w", encoding="utf-8") as file:
        for event in captured:
            file.write(json.dumps(event, ensure_ascii=False) + "\n")

    target_events = [event for event in captured if int(event["target"]["root_hwnd"]) == int(target["hwnd"])]
    summary = {
        "started_at": started,
        "target": target,
        "client_size": {"w": width, "h": height},
        "drag": {
            "start": {"x": start[0], "y": start[1]},
            "end": {"x": end[0], "y": end[1]},
            "duration_ms": args.duration_ms,
            "hold_before_ms": args.hold_before_ms,
            "hold_after_ms": args.hold_after_ms,
            "tail_ms": args.tail_ms,
            "steps": args.steps,
        },
        "captured_total": len(captured),
        "captured_on_target": len(target_events),
        "all_event_counts": _counts(captured),
        "target_event_counts": _counts(target_events),
        "source_counts": _source_counts(captured),
        "target_source_counts": _source_counts(target_events),
        "first_target_events": target_events[:8],
        "last_target_events": target_events[-8:],
        "jsonl": str(jsonl_path),
        "sender_error": sender_error[0] if sender_error else None,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if sender_error else 0


def _resolve_target(hwnd: int | None) -> dict[str, Any]:
    windows = list_windows(process_name="StarCG.exe")
    if hwnd is not None:
        matches = [window for window in windows if int(window.hwnd) == int(hwnd)]
    else:
        matches = windows
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one StarCG target, found {len(matches)}")
    window = matches[0]
    return {
        "hwnd": int(window.hwnd),
        "hwnd_hex": f"0x{int(window.hwnd):X}",
        "pid": int(window.pid),
        "title": window.title,
        "process_name": window.process_name,
        "process_path": window.process_path,
    }


def _first_drag_points(script_path: Path, *, current_w: int, current_h: int) -> tuple[tuple[int, int], tuple[int, int]]:
    script = json.loads(script_path.read_text(encoding="utf-8-sig"))
    recorded = script.get("client_size") or {}
    recorded_w = int(recorded.get("w") or current_w)
    recorded_h = int(recorded.get("h") or current_h)
    events = script.get("events") or []
    drag_start = next((event for event in events if event.get("type") == "drag_start"), None)
    if not drag_start:
        raise RuntimeError(f"script has no drag_start: {script_path}")
    drag_id = drag_start.get("drag_id")
    drag_end = next(
        (
            event
            for event in events
            if event.get("type") == "drag_end" and (drag_id is None or event.get("drag_id") == drag_id)
        ),
        None,
    )
    if not drag_end:
        raise RuntimeError(f"script has no matching drag_end for {drag_id!r}")
    start = (
        _scale_coordinate(int(drag_start["x"]), recorded_w, current_w),
        _scale_coordinate(int(drag_start["y"]), recorded_h, current_h),
    )
    end = (
        _scale_coordinate(int(drag_end["x"]), recorded_w, current_w),
        _scale_coordinate(int(drag_end["y"]), recorded_h, current_h),
    )
    return start, end


def _scale_coordinate(value: int, recorded_size: int, current_size: int) -> int:
    if recorded_size < 2 or current_size < 2:
        return int(value)
    scaled = round(value * (current_size - 1) / (recorded_size - 1))
    return max(0, min(current_size - 1, scaled))


def _send_drag_worker(
    *,
    stop: threading.Event,
    error_out: list[str],
    slot: int,
    hwnd: int,
    config_path: Path,
    start: tuple[int, int],
    end: tuple[int, int],
    duration_ms: int,
    hold_before_ms: int,
    hold_after_ms: int,
    steps: int,
) -> None:
    player = pico_touch_player(config_path=config_path, slot=slot, hwnd=hwnd)
    try:
        time.sleep(0.5)
        player.prepare()
        player.left_down(*start)
        time.sleep(max(0, hold_before_ms) / 1000.0)
        step_count = max(2, int(steps))
        sleep_sec = max(0, duration_ms) / 1000.0 / step_count
        for index in range(1, step_count + 1):
            ratio = index / step_count
            x = round(start[0] + (end[0] - start[0]) * ratio)
            y = round(start[1] + (end[1] - start[1]) * ratio)
            player.move(x, y)
            if sleep_sec:
                time.sleep(sleep_sec)
        time.sleep(max(0, hold_after_ms) / 1000.0)
        player.left_up(*end)
    except Exception:
        try:
            player.cancel()
        except Exception:
            pass
        error_out.append(traceback.format_exc())
    finally:
        stop.set()


def _install_hook(events: "queue.SimpleQueue[dict[str, Any]]") -> tuple[int, HOOKPROC]:
    def proc(n_code: int, w_param: int, l_param: int) -> int:
        if n_code == HC_ACTION and int(w_param) in {WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP}:
            raw = ctypes.cast(l_param, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
            events.put(_build_event(int(w_param), raw))
        return user32.CallNextHookEx(0, n_code, w_param, l_param)

    callback = HOOKPROC(proc)
    hook = user32.SetWindowsHookExW(WH_MOUSE_LL, callback, kernel32.GetModuleHandleW(None), 0)
    if not hook:
        raise RuntimeError(f"SetWindowsHookExW failed: WinError {ctypes.get_last_error()}")
    return int(hook), callback


def _build_event(message: int, raw: MSLLHOOKSTRUCT) -> dict[str, Any]:
    x = int(raw.pt.x)
    y = int(raw.pt.y)
    flags = int(raw.flags)
    child = int(user32.WindowFromPoint(POINT(x, y)) or 0)
    root = int(user32.GetAncestor(wintypes.HWND(child), GA_ROOT) or 0) if child else 0
    return {
        "ts": datetime.now().astimezone().isoformat(timespec="milliseconds"),
        "event": {WM_MOUSEMOVE: "move", WM_LBUTTONDOWN: "left_down", WM_LBUTTONUP: "left_up"}.get(
            message, f"message_0x{message:X}"
        ),
        "message": message,
        "screen": {"x": x, "y": y},
        "flags": flags,
        "source": _source_from_flags(flags),
        "extra_info": int(raw.dwExtraInfo),
        "target": {
            "child_hwnd": child,
            "child_hwnd_hex": f"0x{child:X}" if child else "0x0",
            "root_hwnd": root,
            "root_hwnd_hex": f"0x{root:X}" if root else "0x0",
            "title": _window_text(root) if root else "",
            "pid": _pid(root) if root else 0,
        },
    }


def _source_from_flags(flags: int) -> str:
    if flags & LLMHF_LOWER_IL_INJECTED:
        return "lower_integrity_injected"
    if flags & LLMHF_INJECTED:
        return "injected"
    return "not_injected_flagged"


def _window_text(hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(wintypes.HWND(hwnd), buffer, len(buffer))
    return buffer.value


def _pid(hwnd: int) -> int:
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
    return int(pid.value)


def _pump_messages() -> None:
    msg = MSG()
    while user32.PeekMessageW(ctypes.byref(msg), 0, 0, 0, PM_REMOVE):
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


def _drain(events: "queue.SimpleQueue[dict[str, Any]]", captured: list[dict[str, Any]]) -> None:
    while True:
        try:
            captured.append(events.get_nowait())
        except queue.Empty:
            return


def _counts(events: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        name = str(event["event"])
        counts[name] = counts.get(name, 0) + 1
    return counts


def _source_counts(events: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in events:
        name = str(event["source"])
        counts[name] = counts.get(name, 0) + 1
    return counts


def _default_log_path() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return GUI_DIR / "logs_pc" / f"pico_drag_mouse_probe_{stamp}.jsonl"


if __name__ == "__main__":
    raise SystemExit(main())
