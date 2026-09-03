from __future__ import annotations

import argparse
import ctypes
import json
import shutil
import subprocess
import sys
import time
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from gui_test_pc_server import (  # noqa: E402
    LOG_DIR,
    MAX_SLOT,
    enum_windows,
    game_windows,
    normalize_slots,
    target_slots,
)

PT_TOUCH = 0x00000002

POINTER_FLAG_INRANGE = 0x00000002
POINTER_FLAG_INCONTACT = 0x00000004
POINTER_FLAG_PRIMARY = 0x00002000
POINTER_FLAG_DOWN = 0x00010000
POINTER_FLAG_UPDATE = 0x00020000
POINTER_FLAG_UP = 0x00040000

TOUCH_MASK_CONTACTAREA = 0x00000001
TOUCH_MASK_ORIENTATION = 0x00000002
TOUCH_MASK_PRESSURE = 0x00000004
TOUCH_FEEDBACK_DEFAULT = 0x00000001

POINTER_CHANGE_NONE = 0


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class POINTER_INFO(ctypes.Structure):
    _fields_ = [
        ("pointerType", wintypes.UINT),
        ("pointerId", wintypes.UINT),
        ("frameId", wintypes.UINT),
        ("pointerFlags", wintypes.UINT),
        ("sourceDevice", wintypes.HANDLE),
        ("hwndTarget", wintypes.HWND),
        ("ptPixelLocation", POINT),
        ("ptHimetricLocation", POINT),
        ("ptPixelLocationRaw", POINT),
        ("ptHimetricLocationRaw", POINT),
        ("dwTime", wintypes.DWORD),
        ("historyCount", wintypes.UINT),
        ("InputData", wintypes.INT),
        ("dwKeyStates", wintypes.DWORD),
        ("PerformanceCount", ctypes.c_ulonglong),
        ("ButtonChangeType", wintypes.INT),
    ]


class POINTER_TOUCH_INFO(ctypes.Structure):
    _fields_ = [
        ("pointerInfo", POINTER_INFO),
        ("touchFlags", wintypes.UINT),
        ("touchMask", wintypes.UINT),
        ("rcContact", RECT),
        ("rcContactRaw", RECT),
        ("orientation", wintypes.UINT),
        ("pressure", wintypes.UINT),
    ]


def set_dpi_awareness() -> None:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    try:
        # Per-monitor DPI aware v2 when available.
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


def touch_api() -> ctypes.WinDLL:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.InitializeTouchInjection.argtypes = [wintypes.UINT, wintypes.DWORD]
    user32.InitializeTouchInjection.restype = wintypes.BOOL
    user32.InjectTouchInput.argtypes = [wintypes.UINT, ctypes.POINTER(POINTER_TOUCH_INFO)]
    user32.InjectTouchInput.restype = wintypes.BOOL
    return user32


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inject real Windows touch taps at the center of GUI_TEST_PC StarCG slot windows."
    )
    parser.add_argument("--slots", default="1-15", help="Slot list, e.g. 1-15 or 1,2,3. Default: 1-15")
    parser.add_argument("--taps", type=int, default=10, help="Tap rounds. Default: 10")
    parser.add_argument("--backend", choices=["native", "ctypes"], default="native", help="Injection backend. Default: native")
    parser.add_argument("--hold-ms", type=int, default=80, help="Touch hold duration per tap. Default: 80")
    parser.add_argument("--gap-ms", type=int, default=300, help="Delay between tap rounds. Default: 300")
    parser.add_argument("--radius", type=int, default=8, help="Touch contact rectangle radius in pixels. Default: 8")
    parser.add_argument("--pressure", type=int, default=512, help="Touch pressure 0-1024. Default: 512")
    parser.add_argument(
        "--full-touch-mask",
        action="store_true",
        help="Also set orientation/pressure masks. Default only sets contact area for maximum compatibility.",
    )
    parser.add_argument(
        "--set-hwnd-target",
        action="store_true",
        help="Set POINTER_INFO.hwndTarget. Default leaves it null and lets Windows hit-test screen coordinates.",
    )
    parser.add_argument(
        "--diagnose-on-fail",
        action="store_true",
        help="If the 15-contact frame fails, try smaller contact counts and log which counts are accepted.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Only log targets and coordinates; do not inject touch.")
    return parser.parse_args()


def log_line(handle: Any, message: str, payload: Any | None = None) -> None:
    stamp = datetime.now().isoformat(timespec="milliseconds")
    if payload is None:
        line = f"{stamp} | {message}"
    else:
        line = f"{stamp} | {message} | {json.dumps(payload, ensure_ascii=False, default=str)}"
    print(line, flush=True)
    handle.write(line + "\n")
    handle.flush()


def center_of_client_rect(target: dict[str, Any]) -> tuple[int, int]:
    client = target.get("client_rect") or []
    if len(client) != 4:
        raise RuntimeError(f"target has no client_rect: {target}")
    left, top, right, bottom = [int(value) for value in client]
    return (left + right) // 2, (top + bottom) // 2


def selected_targets(slot_values: list[int]) -> list[dict[str, Any]]:
    rows = target_slots(game_windows(enum_windows()))
    by_slot = {int(row["slot"]): row for row in rows}
    targets: list[dict[str, Any]] = []
    missing: list[int] = []
    for slot in slot_values:
        row = by_slot.get(slot)
        target = row.get("target") if row else None
        if not target:
            missing.append(slot)
            continue
        target = dict(target)
        target["slot"] = slot
        target["center_screen"] = center_of_client_rect(target)
        targets.append(target)
    if missing:
        raise RuntimeError(f"Missing runnable StarCG window for slots: {missing}")
    return targets


def make_touch(
    pointer_id: int,
    hwnd: int,
    x: int,
    y: int,
    flags: int,
    radius: int,
    pressure: int,
    set_hwnd_target: bool,
    full_touch_mask: bool,
    primary: bool,
) -> POINTER_TOUCH_INFO:
    if primary:
        flags |= POINTER_FLAG_PRIMARY
    info = POINTER_TOUCH_INFO()
    info.pointerInfo.pointerType = PT_TOUCH
    info.pointerInfo.pointerId = pointer_id
    info.pointerInfo.frameId = 0
    info.pointerInfo.pointerFlags = flags
    info.pointerInfo.sourceDevice = None
    info.pointerInfo.hwndTarget = int(hwnd) if set_hwnd_target else None
    info.pointerInfo.ptPixelLocation = POINT(int(x), int(y))
    info.pointerInfo.ptHimetricLocation = POINT(0, 0)
    info.pointerInfo.ptPixelLocationRaw = POINT(int(x), int(y))
    info.pointerInfo.ptHimetricLocationRaw = POINT(0, 0)
    info.pointerInfo.dwTime = 0
    info.pointerInfo.historyCount = 1
    info.pointerInfo.InputData = 0
    info.pointerInfo.dwKeyStates = 0
    info.pointerInfo.PerformanceCount = 0
    info.pointerInfo.ButtonChangeType = POINTER_CHANGE_NONE
    info.touchFlags = 0
    info.touchMask = TOUCH_MASK_CONTACTAREA
    if full_touch_mask:
        info.touchMask |= TOUCH_MASK_ORIENTATION | TOUCH_MASK_PRESSURE
    info.rcContact = RECT(int(x - radius), int(y - radius), int(x + radius), int(y + radius))
    info.rcContactRaw = RECT(int(x - radius), int(y - radius), int(x + radius), int(y + radius))
    info.orientation = 90
    info.pressure = max(0, min(1024, int(pressure)))
    return info


def inject_frame(user32: ctypes.WinDLL, touches: list[POINTER_TOUCH_INFO]) -> None:
    array_type = POINTER_TOUCH_INFO * len(touches)
    array = array_type(*touches)
    if not user32.InjectTouchInput(len(touches), array):
        error_code = ctypes.get_last_error()
        raise ctypes.WinError(error_code)


def build_tap_frames(
    targets: list[dict[str, Any]],
    *,
    radius: int,
    pressure: int,
    set_hwnd_target: bool,
    full_touch_mask: bool,
) -> tuple[list[POINTER_TOUCH_INFO], list[POINTER_TOUCH_INFO], list[POINTER_TOUCH_INFO]]:
    down_touches: list[POINTER_TOUCH_INFO] = []
    update_touches: list[POINTER_TOUCH_INFO] = []
    up_touches: list[POINTER_TOUCH_INFO] = []
    for idx, target in enumerate(targets, start=1):
        x, y = target["center_screen"]
        hwnd = int(target["hwnd"])
        pointer_id = idx - 1
        primary = idx == 1
        down_touches.append(
            make_touch(
                pointer_id,
                hwnd,
                x,
                y,
                POINTER_FLAG_INRANGE | POINTER_FLAG_INCONTACT | POINTER_FLAG_DOWN,
                radius,
                pressure,
                set_hwnd_target,
                full_touch_mask,
                primary=primary,
            )
        )
        update_touches.append(
            make_touch(
                pointer_id,
                hwnd,
                x,
                y,
                POINTER_FLAG_INRANGE | POINTER_FLAG_INCONTACT | POINTER_FLAG_UPDATE,
                radius,
                pressure,
                set_hwnd_target,
                full_touch_mask,
                primary=primary,
            )
        )
        up_touches.append(
            make_touch(
                pointer_id,
                hwnd,
                x,
                y,
                POINTER_FLAG_UP,
                radius,
                pressure,
                set_hwnd_target,
                full_touch_mask,
                primary=primary,
            )
        )
    return down_touches, update_touches, up_touches


def diagnose_contact_counts(
    user32: ctypes.WinDLL,
    targets: list[dict[str, Any]],
    *,
    radius: int,
    pressure: int,
    set_hwnd_target: bool,
    full_touch_mask: bool,
    log_handle: Any,
) -> None:
    counts = [1, 2, 5, 10, len(targets)]
    seen: set[int] = set()
    for count in counts:
        if count < 1 or count > len(targets) or count in seen:
            continue
        seen.add(count)
        subset = targets[:count]
        down_touches, _update_touches, up_touches = build_tap_frames(
            subset,
            radius=radius,
            pressure=pressure,
            set_hwnd_target=set_hwnd_target,
            full_touch_mask=full_touch_mask,
        )
        try:
            log_line(log_handle, "diagnose_down", {"contacts": count})
            inject_frame(user32, down_touches)
            time.sleep(0.05)
            inject_frame(user32, up_touches)
            log_line(log_handle, "diagnose_ok", {"contacts": count})
        except Exception as exc:
            log_line(log_handle, "diagnose_error", {"contacts": count, "type": type(exc).__name__, "message": str(exc)})


def run_probe(args: argparse.Namespace, log_handle: Any) -> int:
    if sys.platform != "win32":
        raise RuntimeError("InjectTouchInput is Windows-only")
    slot_values = normalize_slots(args.slots)
    if not slot_values:
        slot_values = list(range(1, MAX_SLOT + 1))
    if len(slot_values) > MAX_SLOT:
        raise RuntimeError(f"too many slots: {slot_values}")

    set_dpi_awareness()
    targets = selected_targets(slot_values)
    target_summary = [
        {
            "slot": int(target["slot"]),
            "hwnd": f"0x{int(target['hwnd']):X}",
            "pid": int(target.get("pid") or 0),
            "title": target.get("title"),
            "client_rect": target.get("client_rect"),
            "center_screen": target.get("center_screen"),
            "slot_source": target.get("slot_source"),
        }
        for target in targets
    ]
    log_line(log_handle, "targets", target_summary)
    log_line(
        log_handle,
        "important",
        "All selected GAME windows must be visible and not covered. Touch injection hits screen coordinates.",
    )

    if args.dry_run:
        log_line(log_handle, "dry_run_done")
        return 0

    if args.backend == "native":
        return run_native_probe(args, targets, log_handle)

    user32 = touch_api()
    if not user32.InitializeTouchInjection(len(targets), TOUCH_FEEDBACK_DEFAULT):
        raise ctypes.WinError(ctypes.get_last_error())

    radius = max(1, int(args.radius))
    pressure = max(0, min(1024, int(args.pressure)))
    set_hwnd_target = bool(args.set_hwnd_target)
    full_touch_mask = bool(args.full_touch_mask)
    hold_sec = max(0.0, int(args.hold_ms) / 1000.0)
    gap_sec = max(0.0, int(args.gap_ms) / 1000.0)
    taps = max(1, int(args.taps))
    for tap_index in range(1, taps + 1):
        down_touches, update_touches, up_touches = build_tap_frames(
            targets,
            radius=radius,
            pressure=pressure,
            set_hwnd_target=set_hwnd_target,
            full_touch_mask=full_touch_mask,
        )

        log_line(log_handle, "tap_down", {"tap": tap_index, "contacts": len(down_touches)})
        try:
            inject_frame(user32, down_touches)
        except Exception:
            if bool(args.diagnose_on_fail):
                diagnose_contact_counts(
                    user32,
                    targets,
                    radius=radius,
                    pressure=pressure,
                    set_hwnd_target=set_hwnd_target,
                    full_touch_mask=full_touch_mask,
                    log_handle=log_handle,
                )
            raise
        if hold_sec > 0:
            time.sleep(hold_sec)
            inject_frame(user32, update_touches)
            time.sleep(min(0.03, hold_sec))
        log_line(log_handle, "tap_up", {"tap": tap_index, "contacts": len(up_touches)})
        inject_frame(user32, up_touches)
        if tap_index < taps and gap_sec > 0:
            time.sleep(gap_sec)

    log_line(log_handle, "done", {"taps": taps, "contacts": len(targets)})
    return 0


def run_native_probe(args: argparse.Namespace, targets: list[dict[str, Any]], log_handle: Any) -> int:
    contacts_path = LOG_DIR / "touch_probe_15_contacts.json"
    helper_path = ROOT / "pc_touch_inject_helper.ps1"
    contacts = [
        {
            "slot": int(target["slot"]),
            "x": int(target["center_screen"][0]),
            "y": int(target["center_screen"][1]),
            "hwnd": f"0x{int(target['hwnd']):X}",
        }
        for target in targets
    ]
    contacts_path.write_text(json.dumps(contacts, ensure_ascii=False, indent=2), encoding="utf-8")
    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(helper_path),
        "-ContactsJson",
        str(contacts_path),
        "-Taps",
        str(max(1, int(args.taps))),
        "-HoldMs",
        str(max(0, int(args.hold_ms))),
        "-GapMs",
        str(max(0, int(args.gap_ms))),
        "-Radius",
        str(max(1, int(args.radius))),
        "-Pressure",
        str(max(0, min(1024, int(args.pressure)))),
    ]
    log_line(log_handle, "native_start", {"cmd": cmd, "contacts_path": str(contacts_path)})
    completed = subprocess.run(cmd, cwd=str(ROOT), text=True, capture_output=True, timeout=120)
    for line in completed.stdout.splitlines():
        log_line(log_handle, "native_stdout", line)
    for line in completed.stderr.splitlines():
        log_line(log_handle, "native_stderr", line)
    if completed.returncode != 0:
        raise RuntimeError(f"native helper failed rc={completed.returncode}")
    log_line(log_handle, "native_done", {"contacts": len(contacts), "taps": int(args.taps)})
    return 0


def main() -> int:
    args = parse_args()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / f"touch_probe_15_{stamp}.log"
    last_path = LOG_DIR / "touch_probe_15_last.log"
    try:
        with log_path.open("w", encoding="utf-8") as log_handle:
            log_line(log_handle, "start", vars(args))
            rc = run_probe(args, log_handle)
    except Exception as exc:
        with log_path.open("a", encoding="utf-8") as log_handle:
            log_line(log_handle, "error", {"type": type(exc).__name__, "message": str(exc)})
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        rc = 1
    try:
        shutil.copyfile(log_path, last_path)
    except OSError:
        pass
    print(f"LOG: {log_path}", flush=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
