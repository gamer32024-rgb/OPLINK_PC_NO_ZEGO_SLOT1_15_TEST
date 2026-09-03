from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timedelta, timezone
import json
import mimetypes
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from PIL import Image, ImageDraw, ImageGrab


APP_NAME = "GUI_TEST_PC"
VERSION = "0.8.0-latest-wins"
ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))
from starcg_bot.gui_command_bridge import GuiCommandBridge
from starcg_bot.slot_limits import MAX_SLOT

LOG_DIR = ROOT / "logs_pc"
SCRIPT_DIR = ROOT / "scripts_pc"
CONFIG_DIR = ROOT / "config_pc"
BACKUP_DIR = ROOT / "backups_pc"
STATIC_DIR = ROOT / "static"

DEFAULT_PORT = int(os.environ.get("GUI_TEST_PC_PORT", "5100"))
DEFAULT_HOST = os.environ.get("GUI_TEST_PC_HOST", "0.0.0.0")
DEFAULT_FPS = float(os.environ.get("GUI_TEST_PC_FPS", "8"))
DEFAULT_WIDTH = int(os.environ.get("GUI_TEST_PC_STREAM_WIDTH", "960"))
DEFAULT_QUALITY = int(os.environ.get("GUI_TEST_PC_JPEG_QUALITY", "65"))
DEFAULT_SLOT = int(os.environ.get("GUI_TEST_PC_DEFAULT_SLOT", "1"))
LAUNCHER_LOG_PATH = Path(os.environ.get("GUI_TEST_PC_STARCG_LOG", r"D:\15game\launcher_action.log"))
STARCG_SOURCE = Path(os.environ.get("GUI_TEST_PC_STARCG_SOURCE", r"D:\TWFULLPC1.2.76"))
STARCG_TARGET_ROOT = Path(os.environ.get("GUI_TEST_PC_STARCG_TARGET_ROOT", str(STARCG_SOURCE)))
STARCG_BYPASS_DIR = Path(os.environ.get("GUI_TEST_PC_STARCG_BYPASS_DIR", r"D:\15game"))
LOCAL_LAUNCHER_CONTROL_SCRIPT = ROOT / "launcher" / "starcg_15_control_gui_test_pc.ps1"
DEFAULT_LAUNCHER_CONTROL_SCRIPT = LOCAL_LAUNCHER_CONTROL_SCRIPT
LEGACY_LAUNCHER_CONTROL_SCRIPT = STARCG_BYPASS_DIR / "starcg_15_control.ps1"
LAUNCHER_CONTROL_SCRIPT = Path(os.environ.get("GUI_TEST_PC_STARCG_CONTROL", str(DEFAULT_LAUNCHER_CONTROL_SCRIPT)))
FORCE_BIND_CONFIG = Path(os.environ.get("GUI_TEST_PC_FORCEBIND_CONFIG", r"D:\15game\forcebindip_config.txt"))
DELAYED_FORCE_BIND_CONFIG = CONFIG_DIR / "forcebindip_delayed_config.txt"
NET_BIND_CONFIG = CONFIG_DIR / "netbind_config.txt"
NET_BIND_LAUNCHER = Path(
    os.environ.get("GUI_TEST_PC_NETBIND_LAUNCHER", str(ROOT / "netbind_pc" / "build_ninja" / "GuiTestNetBindLauncher.exe"))
)
NET_BIND_LOG_PATH = Path(os.environ.get("GUI_TEST_PC_NETBIND_LOG", str(LOG_DIR / "gui_test_pc_netbind_hook.log")))
WINDOWS_USER_CONFIG = Path(os.environ.get("GUI_TEST_PC_WINDOWS_USER_CONFIG", str(CONFIG_DIR / "starcg_windows_users.json")))
JOBS_PATH = CONFIG_DIR / "jobs.json"
MODULES_PATH = CONFIG_DIR / "modules_pc.json"
MODULE_GROUPS_PATH = CONFIG_DIR / "module_groups_pc.json"
MODULE_CHAIN_PRESETS_PATH = CONFIG_DIR / "module_chain_presets.json"
SLOT_PID_MAP_PATH = STARCG_BYPASS_DIR / "gui_test_pc_slot_pids.json"
WINDOW_LAYOUT_SCRIPT = ROOT / "scripts_pc" / "arrange_starcg_windows_pc.ps1"
WINDOW_LAYOUT_CONFIG = CONFIG_DIR / "window_layout.json"
PICO_TOUCH_CONFIG_PATH = CONFIG_DIR / "pico_touch.json"
TAILSCALE_PATH_PREFIX = "/gui-test-pc"
GUI_COMMAND_BRIDGE_ROOT = ROOT / "runtime_pc" / "pwa_bridge"
GUI_COMMAND_BRIDGE = GuiCommandBridge(GUI_COMMAND_BRIDGE_ROOT)

MIN_WINDOW_WIDTH = 80
MIN_WINDOW_HEIGHT = 60
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
POWERSHELL_TIMEOUT_SEC = 240
ELEVATED_LAUNCHER_TIMEOUT_SEC = 600
SCHEDULER_POLL_SEC = 2.0
MODULE_CHAIN_PRESET_COUNT = 20
MODULE_CHAIN_PRESETS_LOCK = threading.Lock()
GUI_COMMAND_ADMISSION_LOCK = threading.RLock()
GUI_HEARTBEAT_MAX_AGE_SEC = 8.0
GUI_COMMAND_TTL_SEC = 30.0
GUI_PLAYBACK_ACTIONS = {
    "play_module_chain",
    "play_script",
    "create_playback_automation",
}
GUI_TERMINAL_SLOT_STATUSES = {
    "完成",
    "已中止",
    "錯誤",
    "completed",
    "cancelled",
    "canceled",
    "error",
    "failed",
}
GUI_CONTROLLER_RESTARTER: GuiControllerRestarter | None = None

STARCG_ACTION_MAP = {
    "status": "status",
    "prepare": "prepare",
    "start": "start",
    "restart": "restart",
    "stop": "stop",
    "start-missing": "start-missing",
    "repair-bad": "repair-bad",
    "relabel": "relabel",
    "bind-test": "bind-test",
    "snapshot-login": "snapshot-login",
    "restore-login": "restore-login",
}

SCHEDULE_ACTION_MAP = {
    "starcg.start": "start",
    "starcg.stop": "stop",
    "starcg.restart": "restart",
    "starcg.start_missing": "start-missing",
    "starcg.repair_bad": "repair-bad",
    "starcg.relabel": "relabel",
    "starcg.snapshot_login": "snapshot-login",
    "starcg.restore_login": "restore-login",
}

ELEVATED_LAUNCHER_ACTIONS = {
    "prepare",
    "start",
    "restart",
    "stop",
    "start-missing",
    "repair-bad",
    "relabel",
    "bind-test",
    "snapshot-login",
    "restore-login",
}

SCHEDULER_LOCK = threading.Lock()
SCHEDULER_STARTED = False
SERVER_LOG_LOCK = threading.RLock()
SERVER_LOG_RETENTION_HOURS = 24.0
SERVER_LOG_PRUNE_INTERVAL_SECONDS = 60 * 60
SERVER_LOG_LAST_PRUNE_MONOTONIC = time.monotonic()

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)


def is_user_admin() -> bool:
    try:
        return bool(shell32.IsUserAnAdmin())
    except Exception:
        return False


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

user32.EnumWindows.argtypes = [EnumWindowsProc, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.EnumDesktopWindows.argtypes = [wintypes.HANDLE, EnumWindowsProc, wintypes.LPARAM]
user32.EnumDesktopWindows.restype = wintypes.BOOL
user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
user32.OpenInputDesktop.restype = wintypes.HANDLE
user32.CloseDesktop.argtypes = [wintypes.HANDLE]
user32.CloseDesktop.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.GetClientRect.restype = wintypes.BOOL
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(POINT)]
user32.ClientToScreen.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = wintypes.BOOL
user32.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.ULONG]
user32.mouse_event.restype = None
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MK_LBUTTON = 0x0001
DESKTOP_READOBJECTS = 0x0001
INPUT_DESKTOP_ACCESS = DESKTOP_READOBJECTS


def open_input_desktop() -> int | None:
    handle = user32.OpenInputDesktop(0, False, INPUT_DESKTOP_ACCESS)
    return int(handle) if handle else None


class TargetLookupError(RuntimeError):
    pass


def ensure_dirs() -> None:
    for folder in (LOG_DIR, SCRIPT_DIR, CONFIG_DIR, BACKUP_DIR, STATIC_DIR):
        folder.mkdir(parents=True, exist_ok=True)


def prune_server_log(retention_hours: float = SERVER_LOG_RETENTION_HOURS) -> int:
    global SERVER_LOG_LAST_PRUNE_MONOTONIC
    path = LOG_DIR / "server.log"
    if not path.is_file():
        SERVER_LOG_LAST_PRUNE_MONOTONIC = time.monotonic()
        return 0
    cutoff = datetime.now() - timedelta(hours=max(1.0, float(retention_hours)))
    temp = path.with_name(f".{path.name}.{os.getpid()}.prune.tmp")
    kept = 0
    removed = 0
    with SERVER_LOG_LOCK:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as source, temp.open(
                "w", encoding="utf-8", newline=""
            ) as destination:
                for line in source:
                    try:
                        stamp = datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S")
                    except (TypeError, ValueError):
                        stamp = cutoff
                    if stamp >= cutoff:
                        destination.write(line)
                        kept += 1
                    else:
                        removed += 1
            temp.replace(path)
        except OSError:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
            return 0
        finally:
            SERVER_LOG_LAST_PRUNE_MONOTONIC = time.monotonic()
    return removed


def log(message: str) -> None:
    global SERVER_LOG_LAST_PRUNE_MONOTONIC
    ensure_dirs()
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"{stamp} | {message}\n"
    if time.monotonic() - SERVER_LOG_LAST_PRUNE_MONOTONIC >= SERVER_LOG_PRUNE_INTERVAL_SECONDS:
        prune_server_log()
    with SERVER_LOG_LOCK:
        try:
            with (LOG_DIR / "server.log").open("a", encoding="utf-8") as fh:
                fh.write(line)
        except OSError:
            pass
    print(line, end="", flush=True)


def local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def window_title(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value.strip()


def window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def process_path(pid: int) -> str | None:
    if pid <= 0:
        return None
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        size = wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return None
    finally:
        kernel32.CloseHandle(handle)


def window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    rect = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)


def client_rect_screen(hwnd: int) -> tuple[int, int, int, int] | None:
    rect = RECT()
    origin = POINT(0, 0)
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        return None
    left = int(origin.x)
    top = int(origin.y)
    right = left + int(rect.right - rect.left)
    bottom = top + int(rect.bottom - rect.top)
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def valid_slot(value: int) -> int | None:
    if 1 <= value <= MAX_SLOT:
        return value
    return None


def slot_from_title(title: str) -> int | None:
    text = title.strip()
    if not text:
        return None

    if re.fullmatch(r"\d{1,3}", text):
        return valid_slot(int(text))

    patterns = [
        r"^\[(\d{1,2})\](?:\s|$)",
        r"^SCG0*(\d{1,3})(?:\b|$)",
        r"^\d+-(\d{1,2})(?:\b|$)",
        r"^0*(\d{1,3})(?:\b|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        slot = valid_slot(int(match.group(1)))
        if slot is not None:
            return slot
    return None


def slot_from_process_path(path: str | None) -> int | None:
    if not path:
        return None
    match = re.search(r"StarCG_slot(\d{1,2})[\\/]+StarCG\.exe$", path, flags=re.IGNORECASE)
    if not match:
        return None
    return valid_slot(int(match.group(1)))


def path_looks_like_starcg_exe(path: str | None) -> bool:
    if not path:
        return False
    return Path(path).name.casefold() == "starcg.exe"


def load_slot_pid_slots() -> dict[int, int]:
    if not SLOT_PID_MAP_PATH.exists():
        return {}
    try:
        data = json.loads(SLOT_PID_MAP_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    slots_by_pid: dict[int, int] = {}
    for slot_text, value in data.items():
        try:
            slot = valid_slot(int(slot_text))
            pid = int((value or {}).get("Pid") or (value or {}).get("pid") or 0)
        except (TypeError, ValueError, AttributeError):
            continue
        if slot is not None and pid > 0:
            slots_by_pid[pid] = slot
    return slots_by_pid


def slot_match_quality(title: str) -> int:
    text = title.strip()
    if re.fullmatch(r"0*([1-9]|1[0-5])", text):
        return 0
    if re.search(r"^\[(\d{1,2})\](?:\s|$)", text):
        return 0
    if re.search(r"^SCG0*(\d{1,3})(?:\b|$)", text, flags=re.IGNORECASE):
        return 1
    if re.search(r"^\d+-(\d{1,2})(?:\b|$)", text):
        return 2
    return 9


def title_looks_like_game(title: str) -> bool:
    folded = title.casefold()
    return slot_from_title(title) is not None or re.search(r"\bscg\d{0,3}\b", folded) is not None


def assign_target_ids(items: list[dict[str, Any]]) -> None:
    slot_counts: dict[int, int] = {}
    for item in items:
        slot = item.get("slot")
        if slot is not None:
            slot_counts[int(slot)] = slot_counts.get(int(slot), 0) + 1

    for item in items:
        slot = item.get("slot")
        hwnd = int(item["hwnd"])
        if slot is not None and slot_counts[int(slot)] == 1:
            item["id"] = f"slot:{slot}"
        elif slot is not None:
            item["id"] = f"slot:{slot}:hwnd:{hwnd}"
        else:
            item["id"] = f"hwnd:{hwnd}"


def enum_windows() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    pid_slots = load_slot_pid_slots()

    @EnumWindowsProc
    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        title = window_title(hwnd)
        rect = window_rect(hwnd)
        client = client_rect_screen(hwnd)
        if not rect or not client:
            return True
        width = client[2] - client[0]
        height = client[3] - client[1]
        if width < MIN_WINDOW_WIDTH or height < MIN_WINDOW_HEIGHT:
            return True

        hwnd_int = int(hwnd)
        pid = window_pid(hwnd_int)
        path = process_path(pid)
        path_slot = slot_from_process_path(path)
        title_slot = slot_from_title(title)
        pid_slot = pid_slots.get(pid)
        is_starcg_exe = path_looks_like_starcg_exe(path)
        if not title and not is_starcg_exe:
            return True
        if path_slot is not None:
            slot = path_slot
            slot_source = "process_path"
            slot_quality = 0
        elif pid_slot is not None and (is_starcg_exe or path is None):
            slot = pid_slot
            slot_source = "pid_map"
            slot_quality = 0
        elif title_slot is not None and (is_starcg_exe or path is None):
            slot = title_slot
            slot_source = "title"
            slot_quality = 4 + slot_match_quality(title)
        else:
            slot = None
            slot_source = None
            slot_quality = None
        items.append(
            {
                "id": f"hwnd:{hwnd_int}",
                "kind": "window",
                "hwnd": hwnd_int,
                "pid": pid,
                "process_path": path,
                "title": title,
                "slot": slot,
                "slot_source": slot_source,
                "slot_quality": slot_quality,
                "is_game": (is_starcg_exe or path is None) and (slot is not None or title_looks_like_game(title)),
                "rect": list(rect),
                "client_rect": list(client),
                "width": width,
                "height": height,
            }
        )
        return True

    desktop = open_input_desktop()
    try:
        if desktop:
            enumerated = bool(user32.EnumDesktopWindows(desktop, callback, 0))
        else:
            enumerated = False
        if not enumerated:
            user32.EnumWindows(callback, 0)
    finally:
        if desktop:
            user32.CloseDesktop(desktop)
    assign_target_ids(items)
    items.sort(key=lambda row: (row.get("slot") is None, row.get("slot") or 999, row["title"].lower(), row["hwnd"]))
    return items


def game_windows(windows: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    source = windows if windows is not None else enum_windows()
    games = [item for item in source if item.get("is_game")]
    by_slot: dict[int, dict[str, Any]] = {}
    no_slot: list[dict[str, Any]] = []
    for item in games:
        slot = item.get("slot")
        if slot is None:
            no_slot.append(item)
            continue
        slot_int = int(slot)
        current = by_slot.get(slot_int)
        if current is None or _target_sort_key(item) < _target_sort_key(current):
            by_slot[slot_int] = item
    return sorted(list(by_slot.values()) + no_slot, key=lambda row: (row.get("slot") is None, row.get("slot") or 999, row["title"].lower(), row["hwnd"]))


def _target_sort_key(item: dict[str, Any]) -> tuple[int, int, str, int]:
    return (
        int(item.get("slot_quality") if item.get("slot_quality") is not None else 9),
        0 if int(item.get("width") or 0) >= MIN_WINDOW_WIDTH and int(item.get("height") or 0) >= MIN_WINDOW_HEIGHT else 1,
        str(item.get("title") or "").lower(),
        int(item.get("hwnd") or 0),
    )


def missing_target(target_id: str | None, reason: str) -> dict[str, Any]:
    return {
        "id": target_id or "",
        "kind": "missing",
        "hwnd": None,
        "pid": None,
        "title": reason,
        "slot": None,
        "is_game": False,
        "rect": None,
        "client_rect": None,
        "width": None,
        "height": None,
        "error": reason,
    }


def normalize_target_id(target_id: str | int | None) -> str | None:
    if target_id is None:
        return None
    text = str(target_id).strip()
    if not text or text.casefold() == "desktop":
        return None
    if text.isdigit():
        value = int(text)
        if valid_slot(value) is not None:
            return f"slot:{value}"
        return f"hwnd:{value}"
    return text


def target_slot(target_id: str) -> int | None:
    match = re.match(r"^slot:(\d{1,2})(?::|$)", target_id, flags=re.IGNORECASE)
    if not match:
        return None
    return valid_slot(int(match.group(1)))


def target_hwnd(target_id: str) -> int | None:
    match = re.search(r"(?:^|:)hwnd:(\d+)$", target_id, flags=re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1))


def find_target(target_id: str, windows: list[dict[str, Any]]) -> dict[str, Any] | None:
    for target in windows:
        if target["id"].casefold() == target_id.casefold():
            return target

    slot = target_slot(target_id)
    if slot is not None:
        matches = [target for target in windows if target.get("slot") == slot]
        if matches:
            return matches[0]

    hwnd = target_hwnd(target_id)
    if hwnd is not None:
        for target in windows:
            if int(target["hwnd"]) == hwnd:
                return target
    return None


class TargetState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._target_id = f"slot:{DEFAULT_SLOT}" if valid_slot(DEFAULT_SLOT) is not None else None

    def get(self) -> str | None:
        with self._lock:
            return self._target_id

    def set(self, target_id: str | None) -> None:
        with self._lock:
            self._target_id = normalize_target_id(target_id)


STATE = TargetState()


def resolve_target(
    target_id: str | int | None = None,
    *,
    allow_auto: bool = True,
    strict: bool = False,
    windows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    games = game_windows(windows)
    selected = normalize_target_id(target_id) or STATE.get()

    if selected:
        target = find_target(selected, games)
        if target is not None:
            return target
        if strict:
            raise TargetLookupError(f"game window not found: {selected}")

    if allow_auto and games:
        return games[0]

    reason = f"No StarCG game window found. Start or relabel windows 1-{MAX_SLOT}, then refresh."
    if selected:
        reason = (
            f"StarCG game window not found for {selected}. "
            f"Refresh or relabel windows 1-{MAX_SLOT}."
        )
    if strict:
        raise TargetLookupError(reason)
    return missing_target(selected, reason)


def grab_window_image(target: dict[str, Any]) -> Image.Image:
    if target.get("kind") != "window" or not target.get("client_rect"):
        raise TargetLookupError(str(target.get("error") or "selected target is not a game window"))
    bbox = tuple(int(v) for v in target["client_rect"])
    try:
        return ImageGrab.grab(bbox=bbox, all_screens=True)
    except TypeError:
        return ImageGrab.grab(bbox=bbox)


def placeholder_image(message: str, width: int = 960, height: int = 540) -> Image.Image:
    img = Image.new("RGB", (width, height), (9, 17, 31))
    draw = ImageDraw.Draw(img)
    draw.rectangle((18, 18, width - 18, height - 18), outline=(72, 230, 176), width=3)
    draw.text((34, 38), APP_NAME, fill=(255, 255, 255))
    draw.text((34, 78), message, fill=(210, 224, 242))
    draw.text((34, 120), time.strftime("%Y-%m-%d %H:%M:%S"), fill=(170, 184, 201))
    return img


def encode_jpeg(img: Image.Image, target_width: int, quality: int) -> bytes:
    if target_width > 0 and img.width != target_width:
        new_height = max(1, int(round(img.height * (target_width / img.width))))
        img = img.resize((target_width, new_height), Image.Resampling.BILINEAR)
    if img.mode != "RGB":
        img = img.convert("RGB")
    output = BytesIO()
    img.save(output, format="JPEG", quality=max(20, min(95, quality)), optimize=True)
    return output.getvalue()


class CaptureSession:
    def __init__(self, target: dict[str, Any], target_id: str | None, output_width: int, quality: int) -> None:
        self.target_id = normalize_target_id(target_id) or str(target["id"])
        self.initial_target = target
        self.source_width = int(target["width"])
        self.source_height = int(target["height"])
        self.output_width = int(output_width)
        self.quality = int(quality)

    @classmethod
    def open(cls, target_id: str | None, output_width: int, quality: int) -> "CaptureSession":
        target = resolve_target(target_id, strict=True)
        session = cls(target, target_id, output_width, quality)
        log(
            "measured game window "
            f"target={session.target_id} hwnd={target.get('hwnd')} title={target.get('title')!r} "
            f"client={session.source_width}x{session.source_height} output_width={session.output_width}"
        )
        return session

    def current_target(self) -> dict[str, Any]:
        return resolve_target(self.target_id, strict=True)

    def capture_jpeg(self) -> bytes:
        target = self.current_target()
        image = grab_window_image(target)
        if image.size != (self.source_width, self.source_height):
            log(
                "game window client size changed during stream; "
                f"target={self.target_id} current={image.width}x{image.height} "
                f"fixed={self.source_width}x{self.source_height}"
            )
            image = image.resize((self.source_width, self.source_height), Image.Resampling.BILINEAR)
        return encode_jpeg(image, self.output_width, self.quality)

    def as_dict(self) -> dict[str, Any]:
        output_width = self.output_width if self.output_width > 0 else self.source_width
        output_height = int(round(self.source_height * (output_width / self.source_width)))
        return {
            "target": self.initial_target,
            "target_id": self.target_id,
            "source_size": {"w": self.source_width, "h": self.source_height},
            "output_size": {"w": output_width, "h": output_height},
            "quality": self.quality,
        }


def capture_jpeg(target_id: str | None, width: int, quality: int) -> bytes:
    try:
        session = CaptureSession.open(target_id, width, quality)
        return session.capture_jpeg()
    except Exception as exc:
        log(f"capture failed target={target_id or STATE.get()} error={exc}")
        return encode_jpeg(placeholder_image(str(exc)), width, quality)


def mouse_lparam(x: int, y: int) -> int:
    return ((int(y) & 0xFFFF) << 16) | (int(x) & 0xFFFF)


def post_client_click(hwnd: int, x: int, y: int, duration_ms: int = 35) -> None:
    hwnd = int(hwnd)
    x = int(x)
    y = int(y)
    hold = max(0.0, min(2.0, int(duration_ms) / 1000.0))
    lp = mouse_lparam(x, y)
    for message, wparam in (
        (WM_MOUSEMOVE, 0),
        (WM_LBUTTONDOWN, MK_LBUTTON),
    ):
        if not user32.PostMessageW(hwnd, message, wparam, lp):
            raise RuntimeError(f"PostMessageW failed hwnd=0x{hwnd:X} message=0x{message:X}")
    if hold:
        time.sleep(hold)
    if not user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lp):
        raise RuntimeError(f"PostMessageW failed hwnd=0x{hwnd:X} message=0x{WM_LBUTTONUP:X}")


def parse_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return {}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def launcher_script_path() -> Path:
    if LAUNCHER_CONTROL_SCRIPT.exists():
        return LAUNCHER_CONTROL_SCRIPT
    if LOCAL_LAUNCHER_CONTROL_SCRIPT.exists():
        return LOCAL_LAUNCHER_CONTROL_SCRIPT
    if LEGACY_LAUNCHER_CONTROL_SCRIPT.exists():
        return LEGACY_LAUNCHER_CONTROL_SCRIPT
    return LAUNCHER_CONTROL_SCRIPT


def normalize_slots(value: Any) -> list[int]:
    if value is None or value == "":
        return []
    raw: list[Any]
    if isinstance(value, list):
        raw = value
    else:
        raw = str(value).replace(" ", "").split(",")

    slots: list[int] = []
    seen: set[int] = set()

    def append_slot(candidate: int) -> None:
        slot = valid_slot(candidate)
        if slot is not None and slot not in seen:
            seen.add(slot)
            slots.append(slot)

    for item in raw:
        text = str(item).strip()
        if not text:
            continue
        if re.fullmatch(r"\d+-\d+", text):
            start_text, end_text = text.split("-", 1)
            start = int(start_text)
            end = int(end_text)
            if start > end:
                start, end = end, start
            for slot in range(start, end + 1):
                append_slot(slot)
            continue
        if text.isdigit():
            append_slot(int(text))
    return slots


def slots_to_text(slots: list[int]) -> str:
    return ",".join(str(slot) for slot in slots)


def read_key_value_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    try:
        lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    except OSError:
        return values
    for line in lines:
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def normalize_forcebind_mode(value: Any) -> str:
    text = str(value or "netbind").strip().lower().replace("_", "-")
    if text in {"off", "none", "disabled", "no", "no-forcebind", "without-forcebind"}:
        return "off"
    if text in {"netbind", "gui-test-pc", "gui-test-netbind", "loopback-safe", "new", "safe"}:
        return "netbind"
    if text in {"delayed", "delay", "legacy-delayed", "safe-delayed", "delayed-injection", "delay-injection"}:
        return "delayed"
    return "normal"


def delayed_forcebind_config_path() -> Path:
    ensure_dirs()
    if FORCE_BIND_CONFIG.exists():
        lines = FORCE_BIND_CONFIG.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    else:
        lines = [
            "FORCEBINDIP_PATH=D:\\15game\\ForceBindIP\\ForceBindIP64.exe",
            "DEFAULT_IP=",
        ]

    output: list[str] = []
    wrote_flag = False
    for line in lines:
        if line.strip().upper().startswith("USE_DELAYED_INJECTION="):
            output.append("USE_DELAYED_INJECTION=1")
            wrote_flag = True
        else:
            output.append(line)
    if not wrote_flag:
        output.append("USE_DELAYED_INJECTION=1")
    DELAYED_FORCE_BIND_CONFIG.write_text("\n".join(output) + "\n", encoding="utf-8")
    return DELAYED_FORCE_BIND_CONFIG


def netbind_config_path() -> Path:
    ensure_dirs()
    if NET_BIND_CONFIG.exists():
        return NET_BIND_CONFIG
    if FORCE_BIND_CONFIG.exists():
        lines = FORCE_BIND_CONFIG.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    else:
        lines = [
            "DEFAULT_IP=",
            "GROUP_A_DESCRIPTION=Intel(R) Ethernet Controller (3) I225-V",
            "GROUP_B_INTERFACE_INDEX=20",
            "GROUP_B_DESCRIPTION=Remote NDIS based Internet Sharing Device",
            "GROUP_B_GATEWAY=192.168.0.1",
            "GROUP_C_DESCRIPTION=Intel(R) Wi-Fi 6 AX201 160MHz",
            "GROUP_D_INTERFACE_INDEX=3",
            "GROUP_D_DESCRIPTION=Intel(R) Ethernet Controller (3) I225-V",
            "GROUP_D_GATEWAY=119.236.6.252",
            "SLOT_01_GROUP=A",
            "SLOT_02_GROUP=A",
            "SLOT_03_GROUP=A",
            "SLOT_04_GROUP=A",
            "SLOT_05_GROUP=A",
            "SLOT_06_GROUP=C",
            "SLOT_07_GROUP=C",
            "SLOT_08_GROUP=C",
            "SLOT_09_GROUP=C",
            "SLOT_10_GROUP=C",
            "SLOT_11_GROUP=B",
            "SLOT_12_GROUP=B",
            "SLOT_13_GROUP=B",
            "SLOT_14_GROUP=B",
            "SLOT_15_GROUP=B",
            "SLOT_16_GROUP=D",
            "SLOT_17_GROUP=D",
            "SLOT_18_GROUP=D",
            "SLOT_19_GROUP=D",
            "SLOT_20_GROUP=D",
        ]

    output: list[str] = [
        "# GUI_TEST_PC netbind config.",
        "# This file intentionally does not use D:\\15game\\ForceBindIP\\BindIP64.dll.",
        "BINDER=GUI_TEST_PC_NETBIND",
    ]
    for line in lines:
        upper = line.strip().upper()
        if upper.startswith("FORCEBINDIP_PATH=") or upper.startswith("USE_DELAYED_INJECTION="):
            continue
        output.append(line)
    NET_BIND_CONFIG.write_text("\n".join(output) + "\n", encoding="utf-8")
    return NET_BIND_CONFIG


def launcher_action_args(
    ps_action: str,
    slot_list: str,
    *,
    json_output: bool,
    forcebind_mode: str = "netbind",
    use_windows_users: bool = False,
) -> list[str]:
    mode = normalize_forcebind_mode(forcebind_mode)
    if mode == "netbind":
        force_bind_config = netbind_config_path()
    elif mode == "delayed":
        force_bind_config = delayed_forcebind_config_path()
    else:
        force_bind_config = FORCE_BIND_CONFIG
    force_bind_values = read_key_value_file(force_bind_config)
    args = [
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(launcher_script_path()),
        "-Action",
        ps_action,
        "-SlotList",
        slot_list,
        "-Source",
        str(STARCG_SOURCE),
        "-TargetRoot",
        str(STARCG_TARGET_ROOT),
        "-BypassDir",
        str(STARCG_BYPASS_DIR),
        "-Slots",
        str(MAX_SLOT),
        "-LogPath",
        str(LAUNCHER_LOG_PATH),
        "-ForceBindConfig",
        str(force_bind_config),
    ]
    bind_ip = force_bind_values.get("DEFAULT_IP", force_bind_values.get("BIND_IP", "")).strip()
    if bind_ip:
        args += ["-BindIP", bind_ip]
    if mode == "netbind":
        args += [
            "-UseNetBind",
            "-NetBindLauncherPath",
            str(NET_BIND_LAUNCHER),
            "-NetBindLogPath",
            str(NET_BIND_LOG_PATH),
        ]
    else:
        args += [
            "-ForceBindIPPath",
            force_bind_values.get("FORCEBINDIP_PATH", ""),
        ]
    if mode == "off":
        args.append("-NoForceBindIP")
    if use_windows_users:
        args += [
            "-UseWindowsUsers",
            "-WindowsUserConfigPath",
            str(WINDOWS_USER_CONFIG),
        ]
    if json_output:
        args.append("-Json")
    return args


def ps_single_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def elevated_launcher_command(argument_line: str, working_directory: str) -> str:
    return (
        "$process = Start-Process -FilePath 'powershell.exe' "
        f"-ArgumentList {ps_single_quote(argument_line)} "
        f"-WorkingDirectory {ps_single_quote(working_directory)} "
        "-Verb RunAs -WindowStyle Hidden -PassThru; "
        "$process.WaitForExit(); "
        "exit $process.ExitCode"
    )


def launcher_log_state() -> dict[str, Any]:
    if not LAUNCHER_LOG_PATH.exists():
        return {"exists": False, "size": 0, "mtime_ns": 0, "tail": []}
    try:
        stat = LAUNCHER_LOG_PATH.stat()
    except OSError:
        return {"exists": False, "size": 0, "mtime_ns": 0, "tail": []}
    return {
        "exists": True,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "tail": read_launcher_log_tail(10),
    }


def wait_for_launcher_log_change(before: dict[str, Any], timeout_sec: float = 8.0) -> tuple[bool, dict[str, Any]]:
    deadline = time.time() + max(0.0, timeout_sec)
    after = launcher_log_state()
    while time.time() < deadline:
        after = launcher_log_state()
        if (
            after.get("exists") != before.get("exists")
            or after.get("size") != before.get("size")
            or after.get("mtime_ns") != before.get("mtime_ns")
            or after.get("tail") != before.get("tail")
        ):
            return True, after
        time.sleep(0.25)
    return False, after


def parse_json_from_stdout(stdout: str) -> Any:
    text = stdout.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    for marker in ("\n[", "\n{"):
        index = text.rfind(marker)
        if index >= 0:
            try:
                return json.loads(text[index + 1 :])
            except json.JSONDecodeError:
                continue
    return None


def run_launcher_action(
    action_key: str,
    slots: list[int] | None = None,
    *,
    forcebind_mode: str = "netbind",
    use_windows_users: bool = False,
) -> dict[str, Any]:
    if action_key not in STARCG_ACTION_MAP:
        raise ValueError(f"unsupported launcher action: {action_key}")
    script = launcher_script_path()
    if not script.exists():
        raise FileNotFoundError(f"launcher control script not found: {script}")

    ps_action = STARCG_ACTION_MAP[action_key]
    mode = normalize_forcebind_mode(forcebind_mode)
    slot_list = slots_to_text(slots or [])
    args = [
        "powershell.exe",
        *launcher_action_args(
            ps_action,
            slot_list,
            json_output=True,
            forcebind_mode=mode,
            use_windows_users=use_windows_users,
        ),
    ]

    log(
        "launcher action start "
        f"action={ps_action} slots={slot_list or 'all'} forcebind={mode} "
        f"windows_users={use_windows_users} script={script}"
    )
    completed = subprocess.run(
        args,
        cwd=str(script.parent),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=POWERSHELL_TIMEOUT_SEC,
    )
    payload = {
        "ok": completed.returncode == 0,
        "action": ps_action,
        "slots": slots or [],
        "forcebind_mode": mode,
        "use_windows_users": use_windows_users,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "data": parse_json_from_stdout(completed.stdout),
    }
    log(f"launcher action end action={ps_action} rc={completed.returncode}")
    return payload


def run_launcher_action_elevated(
    action_key: str,
    slots: list[int] | None = None,
    *,
    forcebind_mode: str = "netbind",
    use_windows_users: bool = False,
) -> dict[str, Any]:
    if action_key not in STARCG_ACTION_MAP:
        raise ValueError(f"unsupported launcher action: {action_key}")
    mode = normalize_forcebind_mode(forcebind_mode)
    if STARCG_ACTION_MAP[action_key] not in ELEVATED_LAUNCHER_ACTIONS:
        return run_launcher_action(action_key, slots, forcebind_mode=mode, use_windows_users=use_windows_users)
    script = launcher_script_path()
    if not script.exists():
        raise FileNotFoundError(f"launcher control script not found: {script}")

    ps_action = STARCG_ACTION_MAP[action_key]
    slot_list = slots_to_text(slots or [])
    launcher_args = launcher_action_args(
        ps_action,
        slot_list,
        json_output=False,
        forcebind_mode=mode,
        use_windows_users=use_windows_users,
    )
    argument_line = subprocess.list2cmdline(launcher_args)
    command = elevated_launcher_command(argument_line, str(script.parent))
    args = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command]

    before_log = launcher_log_state()
    log(
        "launcher runas action request "
        f"action={ps_action} slots={slot_list or 'all'} forcebind={mode} "
        f"windows_users={use_windows_users} script={script}"
    )
    log(f"launcher runas argument line: {argument_line}")
    completed = subprocess.run(
        args,
        cwd=str(script.parent),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=ELEVATED_LAUNCHER_TIMEOUT_SEC,
    )
    log_updated = False
    after_log = before_log
    if completed.returncode == 0:
        log_updated, after_log = wait_for_launcher_log_change(before_log)

    payload = {
        "ok": completed.returncode == 0,
        "action": ps_action,
        "slots": slots or [],
        "forcebind_mode": mode,
        "use_windows_users": use_windows_users,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
        "elevated": True,
        "run_as": True,
        "admin_parent": is_user_admin(),
        "script": str(script),
        "log_path": str(LAUNCHER_LOG_PATH),
        "log_updated": log_updated,
        "log_tail": after_log.get("tail", []),
    }
    log(
        "launcher runas action dispatched "
        f"action={ps_action} rc={completed.returncode} log_updated={log_updated}"
    )
    return payload


def run_window_layout_action(action: str, slots: list[int] | None = None) -> dict[str, Any]:
    if action not in {"status", "arrange", "ensure"}:
        raise ValueError(f"unsupported window layout action: {action}")
    if not WINDOW_LAYOUT_SCRIPT.exists():
        raise FileNotFoundError(f"window layout script not found: {WINDOW_LAYOUT_SCRIPT}")

    slot_list = slots_to_text(slots or list(range(1, MAX_SLOT + 1)))
    args = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(WINDOW_LAYOUT_SCRIPT),
        "-Action",
        action,
        "-SlotList",
        slot_list,
        "-SlotPidMapPath",
        str(SLOT_PID_MAP_PATH),
        "-ConfigPath",
        str(WINDOW_LAYOUT_CONFIG),
        "-Json",
    ]
    completed = subprocess.run(
        args,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    result: dict[str, Any] | None = None
    for line in reversed(completed.stdout.splitlines()):
        text = line.strip()
        if not text:
            continue
        try:
            candidate = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            result = candidate
            break
    if result is None:
        result = {
            "ok": False,
            "action": action,
            "error": "window layout script did not return JSON",
        }
    result.update(
        {
            "ok": bool(result.get("ok")) and completed.returncode == 0,
            "returncode": completed.returncode,
            "script": str(WINDOW_LAYOUT_SCRIPT),
            "config_path": str(WINDOW_LAYOUT_CONFIG),
        }
    )
    if completed.stderr.strip():
        result["stderr"] = completed.stderr.strip()
    log(
        "window layout "
        f"action={action} slots={slot_list} rc={completed.returncode} ready={result.get('ready')} "
        f"moved={result.get('moved_slots', [])}"
    )
    return result


def read_launcher_log_tail(max_lines: int = 80) -> list[str]:
    if not LAUNCHER_LOG_PATH.exists():
        return []
    try:
        lines = LAUNCHER_LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return lines[-max(1, min(500, int(max_lines))) :]


def safe_script_summary(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return {"name": path.name, "path": str(path), "error": str(exc)}
    size = data.get("client_size") or {}
    target = data.get("target") or {}
    duration_ms = data.get("duration_ms")
    return {
        "name": path.name,
        "stem": path.name.removesuffix(".pcscript.json"),
        "path": str(path),
        "format": data.get("format", ""),
        "events": len(data.get("events") or []),
        "duration_ms": duration_ms,
        "duration_sec": round(float(duration_ms or 0) / 1000.0, 3),
        "client_size": {"w": int(size.get("w") or 0), "h": int(size.get("h") or 0)},
        "target_slot": target.get("slot"),
        "created_at": data.get("created_at", ""),
    }


def list_pc_scripts() -> list[dict[str, Any]]:
    ensure_dirs()
    return [safe_script_summary(path) for path in sorted(SCRIPT_DIR.glob("*.pcscript.json"), key=lambda p: p.name.casefold())]


def load_pc_modules() -> dict[str, list[str]]:
    if not MODULES_PATH.exists():
        return {}
    try:
        data = json.loads(MODULES_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    if isinstance(data, dict) and isinstance(data.get("modules"), dict):
        data = data["modules"]
    if not isinstance(data, dict):
        return {}
    modules: dict[str, list[str]] = {}
    for name, scripts in data.items():
        if isinstance(name, str) and isinstance(scripts, list):
            modules[name] = [str(item) for item in scripts]
    return modules


def load_pc_module_groups() -> list[dict[str, Any]]:
    modules = load_pc_modules()
    assignments: dict[str, str] = {}
    group_order: list[str] = []
    if MODULE_GROUPS_PATH.exists():
        try:
            data = json.loads(MODULE_GROUPS_PATH.read_text(encoding="utf-8-sig"))
        except Exception:
            data = {}
        if isinstance(data, dict):
            raw_groups = data.get("groups")
            if isinstance(raw_groups, list):
                group_order = [str(name).strip() for name in raw_groups if str(name).strip()]
            raw_assignments = data.get("assignments")
            if isinstance(raw_assignments, dict):
                assignments = {
                    str(module): str(group).strip()
                    for module, group in raw_assignments.items()
                    if str(module) in modules and str(group).strip()
                }

    buckets: dict[str, list[str]] = {}
    for module_name in modules:
        group_name = assignments.get(module_name, "未分組")
        buckets.setdefault(group_name, []).append(module_name)
    ordered_names = list(dict.fromkeys([*group_order, *buckets.keys()]))
    return [
        {"name": name, "modules": buckets[name]}
        for name in ordered_names
        if buckets.get(name)
    ]


def _default_module_chain_presets() -> list[dict[str, Any]]:
    return [
        {"index": index, "name": f"連串 {index}", "modules": []}
        for index in range(1, MODULE_CHAIN_PRESET_COUNT + 1)
    ]


def load_module_chain_presets() -> list[dict[str, Any]]:
    defaults = _default_module_chain_presets()
    if not MODULE_CHAIN_PRESETS_PATH.exists():
        return defaults
    try:
        data = json.loads(MODULE_CHAIN_PRESETS_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        return defaults
    raw_presets = data.get("presets") if isinstance(data, dict) else data
    if not isinstance(raw_presets, list):
        return defaults

    available_modules = set(load_pc_modules())
    by_index: dict[int, dict[str, Any]] = {}
    for offset, item in enumerate(raw_presets, start=1):
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index") or offset)
        except (TypeError, ValueError):
            continue
        if index < 1 or index > MODULE_CHAIN_PRESET_COUNT:
            continue
        name = str(item.get("name") or f"連串 {index}").strip()[:40] or f"連串 {index}"
        raw_modules = item.get("modules")
        modules = []
        if isinstance(raw_modules, list):
            modules = [
                str(module).strip()
                for module in raw_modules[:10]
                if str(module).strip() in available_modules
            ]
        by_index[index] = {"index": index, "name": name, "modules": modules}
    return [by_index.get(index, defaults[index - 1]) for index in range(1, MODULE_CHAIN_PRESET_COUNT + 1)]


def save_module_chain_preset(index: int, name: str, modules: list[Any]) -> dict[str, Any]:
    if index < 1 or index > MODULE_CHAIN_PRESET_COUNT:
        raise ValueError(f"preset index must be 1-{MODULE_CHAIN_PRESET_COUNT}")
    normalized_name = str(name or "").strip()
    if not normalized_name:
        raise ValueError("preset name is required")
    if len(normalized_name) > 40:
        raise ValueError("preset name must be 40 characters or fewer")
    if not isinstance(modules, list):
        raise ValueError("modules must be an ordered list")
    normalized_modules = [str(module).strip() for module in modules if str(module).strip()]
    if not normalized_modules or len(normalized_modules) > 10:
        raise ValueError("preset requires 1-10 modules")
    available_modules = set(load_pc_modules())
    invalid = [module for module in normalized_modules if module not in available_modules]
    if invalid:
        raise ValueError(f"unknown modules: {invalid}")

    with MODULE_CHAIN_PRESETS_LOCK:
        presets = load_module_chain_presets()
        preset = {"index": index, "name": normalized_name, "modules": normalized_modules}
        presets[index - 1] = preset
        ensure_dirs()
        payload = {
            "updated_at": utc_now_iso(),
            "presets": presets,
        }
        temp_path = MODULE_CHAIN_PRESETS_PATH.with_suffix(".tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp_path.replace(MODULE_CHAIN_PRESETS_PATH)
    return preset


def script_path_from_name(name: str) -> Path:
    text = str(name or "").strip()
    if not text:
        raise ValueError("script name is required")
    path = Path(text)
    if not path.is_absolute():
        path = SCRIPT_DIR / path.name
    resolved = path.resolve()
    script_root = SCRIPT_DIR.resolve()
    if script_root not in resolved.parents and resolved != script_root:
        raise ValueError(f"script path must be under {SCRIPT_DIR}")
    if not resolved.exists() or resolved.suffix.lower() != ".json" or not resolved.name.endswith(".pcscript.json"):
        raise FileNotFoundError(f"PC script not found: {text}")
    return resolved


def module_entry_path_from_name(name: str) -> Path:
    text = str(name or "").strip()
    if not text:
        raise ValueError("module entry name is required")
    path = Path(text)
    if not path.is_absolute():
        path = SCRIPT_DIR / path.name
    resolved = path.resolve()
    script_root = SCRIPT_DIR.resolve()
    if script_root not in resolved.parents and resolved != script_root:
        raise ValueError(f"module entry path must be under {SCRIPT_DIR}")
    allowed_suffixes = (".pcscript.json", ".battle.json")
    if not resolved.exists() or not resolved.name.casefold().endswith(allowed_suffixes):
        raise FileNotFoundError(f"playable module entry not found: {text}")
    return resolved


def module_script_paths(module_name: str) -> list[Path]:
    modules = load_pc_modules()
    if module_name not in modules:
        raise ValueError(f"module not found: {module_name}")
    paths = [module_entry_path_from_name(name) for name in modules.get(module_name, [])]
    if not paths:
        raise ValueError(f"module has no playable scripts: {module_name}")
    return paths


def validate_module_play_request(slots: list[int], module_names: list[str]) -> None:
    descriptor_steps: list[tuple[int, Path, str, set[int]]] = []
    for index, module_name in enumerate(module_names):
        for path in module_script_paths(module_name):
            if not path.name.casefold().endswith(".battle.json"):
                continue
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            if payload.get("format") != "gui_test_pc_battle_interrupt_v1":
                raise ValueError(f"unsupported battle module descriptor: {path.name}")
            mode = str(payload.get("mode") or "").strip().casefold()
            if mode not in {"dry_run", "active"}:
                raise ValueError(f"invalid battle module mode: {path.name}")
            allowed_slots = [int(value) for value in payload.get("allowed_slots") or []]
            if (
                not allowed_slots
                or len(set(allowed_slots)) != len(allowed_slots)
                or any(slot < 1 or slot > MAX_SLOT for slot in allowed_slots)
            ):
                raise ValueError(
                    f"battle module allowed_slots must contain unique SLOT "
                    f"1-{MAX_SLOT} values: {path.name}"
                )
            descriptor_steps.append((index, path, mode, set(allowed_slots)))
    if not descriptor_steps:
        return
    selected_slots = {int(slot) for slot in slots}
    for index, path, mode, allowed_slots in descriptor_steps:
        unsupported = sorted(selected_slots - allowed_slots)
        if unsupported:
            raise ValueError(f"{path.name} 不允許以下 SLOT: {unsupported}")
        if mode == "dry_run" and index != len(module_names) - 1:
            raise ValueError(f"Dry-run 必須是模組鏈最後一步: {path.name}")


def normalize_playback_automation_payload(body: dict[str, Any]) -> dict[str, Any]:
    mode = str(body.get("mode") or "").strip()
    if mode not in {"loop", "scheduled_once"}:
        raise ValueError("mode must be loop or scheduled_once")
    slots = normalize_slots(body.get("slots"))
    if not slots:
        raise ValueError("at least one slot is required")
    target_kind = str(body.get("target_kind") or "module_chain").strip()
    if target_kind not in {"module_chain", "script"}:
        raise ValueError("target_kind must be module_chain or script")

    modules = [str(name).strip() for name in body.get("modules", []) if str(name).strip()]
    script = str(body.get("script") or "").strip()
    if target_kind == "module_chain":
        if not modules:
            raise ValueError("at least one module is required")
        if len(modules) > 10:
            raise ValueError("a module chain can contain at most 10 modules")
        validate_module_play_request(slots, modules)
    else:
        script = script_path_from_name(script).name
    if mode == "loop" and target_kind != "module_chain":
        raise ValueError("loop playback requires a module chain")

    try:
        cooldown_seconds = float(body.get("cooldown_seconds") or 0.0)
    except (TypeError, ValueError) as exc:
        raise ValueError("cooldown_seconds must be a number") from exc
    if cooldown_seconds < 0 or cooldown_seconds > 86400:
        raise ValueError("cooldown_seconds must be between 0 and 86400")

    repeat_count: int | None = None
    raw_repeat_count = body.get("repeat_count")
    if mode == "loop" and raw_repeat_count is not None and raw_repeat_count != "":
        try:
            repeat_count = int(raw_repeat_count)
        except (TypeError, ValueError) as exc:
            raise ValueError("repeat_count must be a positive integer or empty") from exc
        if repeat_count <= 0:
            raise ValueError("repeat_count must be a positive integer or empty")

    run_at: str | float | None = None
    if mode == "scheduled_once":
        raw_run_at = body.get("run_at")
        if isinstance(raw_run_at, (int, float)):
            run_at_timestamp = float(raw_run_at)
            run_at = run_at_timestamp
        else:
            text = str(raw_run_at or "").strip()
            if not text:
                raise ValueError("run_at is required")
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("run_at must be an ISO date time") from exc
            if parsed.tzinfo is None:
                raise ValueError("run_at must include a timezone offset")
            run_at_timestamp = parsed.timestamp()
            run_at = parsed.isoformat(timespec="seconds")
        if run_at_timestamp <= time.time():
            raise ValueError("定時播放時間必須晚於目前台北時間")

    return {
        "mode": mode,
        "target_kind": target_kind,
        "slots": slots,
        "modules": modules,
        "script": script,
        "cooldown_seconds": cooldown_seconds,
        "repeat_count": repeat_count,
        "run_at": run_at,
    }


class GuiCommandAdmissionError(RuntimeError):
    def __init__(self, message: str, http_status: int) -> None:
        super().__init__(message)
        self.http_status = int(http_status)


def _parse_iso_timestamp(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def gui_heartbeat_age_seconds(heartbeat: dict[str, Any] | None = None) -> float | None:
    state = heartbeat if heartbeat is not None else GUI_COMMAND_BRIDGE.read_heartbeat()
    if not isinstance(state, dict):
        return None
    updated_at = _parse_iso_timestamp(state.get("updated_at"))
    if updated_at is None:
        return None
    return max(0.0, (datetime.now(timezone.utc) - updated_at).total_seconds())


def gui_heartbeat_is_fresh(heartbeat: dict[str, Any] | None = None) -> bool:
    state = heartbeat if heartbeat is not None else GUI_COMMAND_BRIDGE.read_heartbeat()
    age = gui_heartbeat_age_seconds(state)
    return bool(
        isinstance(state, dict)
        and state.get("online") is True
        and age is not None
        and age <= GUI_HEARTBEAT_MAX_AGE_SEC
    )


def _command_reservation_slots(command: dict[str, Any], now: datetime) -> set[int]:
    action = str(command.get("action") or "")
    status = str(command.get("status") or "")
    if action not in GUI_PLAYBACK_ACTIONS:
        return set()
    if status in {"queued", "running"}:
        slots = normalize_slots(command.get("slots") or command.get("payload", {}).get("slots"))
        slot_statuses = command.get("slot_status")
        if not isinstance(slot_statuses, dict):
            return set(slots)
        reserved: set[int] = set()
        for slot in slots:
            slot_status = slot_statuses.get(str(slot), slot_statuses.get(slot, ""))
            if str(slot_status or "").strip().casefold() not in GUI_TERMINAL_SLOT_STATUSES:
                reserved.add(slot)
        return reserved
    if action != "create_playback_automation" or status != "completed":
        return set()
    finished_at = _parse_iso_timestamp(command.get("finished_at"))
    if finished_at is None or (now - finished_at).total_seconds() > 5.0:
        return set()
    return set(normalize_slots(command.get("slots") or command.get("payload", {}).get("slots")))


def reserved_gui_playback_slots(
    heartbeat: dict[str, Any] | None = None,
) -> dict[int, str]:
    state = heartbeat if isinstance(heartbeat, dict) else GUI_COMMAND_BRIDGE.read_heartbeat()
    reservations: dict[int, str] = {}
    if isinstance(state, dict):
        for slot in normalize_slots(state.get("playing_slots")):
            reservations[slot] = "playing"
        automations = state.get("playback_automations")
        if isinstance(automations, list):
            for automation in automations:
                if not isinstance(automation, dict):
                    continue
                status = str(automation.get("status") or "")
                if status not in {"waiting", "running", "cooling"}:
                    continue
                for slot in normalize_slots(automation.get("slots")):
                    reservations.setdefault(slot, f"automation:{status}")
    now = datetime.now(timezone.utc)
    for command in GUI_COMMAND_BRIDGE.list_commands(limit=200):
        for slot in _command_reservation_slots(command, now):
            reservations.setdefault(slot, f"command:{command.get('status')}")
    return reservations


def gui_controller_status() -> dict[str, Any]:
    heartbeat = GUI_COMMAND_BRIDGE.read_heartbeat()
    age = gui_heartbeat_age_seconds(heartbeat)
    return {
        "online": gui_heartbeat_is_fresh(heartbeat),
        "heartbeat_age_seconds": age,
        "heartbeat_pid": heartbeat.get("pid") if isinstance(heartbeat, dict) else None,
        "shutdown_requested": bool(heartbeat.get("shutdown_requested")) if isinstance(heartbeat, dict) else False,
        "reserved_slots": sorted(reserved_gui_playback_slots(heartbeat)),
    }


def enqueue_gui_command(
    action: str,
    payload: dict[str, Any],
    *,
    label: str,
    reserve_slots: bool = False,
    allow_partial: bool = False,
    request_id: str | None = None,
    require_online: bool = True,
) -> dict[str, Any]:
    with GUI_COMMAND_ADMISSION_LOCK:
        GUI_COMMAND_BRIDGE.expire_stale()
        heartbeat = GUI_COMMAND_BRIDGE.read_heartbeat()
        if require_online and not gui_heartbeat_is_fresh(heartbeat):
            raise GuiCommandAdmissionError(
                "GUI_TEST_PC 控制器沒有回應；命令未加入佇列，請先重啟控制器。",
                HTTPStatus.SERVICE_UNAVAILABLE,
            )
        clean_request_id = str(request_id or "").strip()
        if clean_request_id:
            existing = GUI_COMMAND_BRIDGE.find_by_request_id(clean_request_id)
            if existing is not None:
                return {
                    "ok": True,
                    "relayed_to": "GUI_TEST_PC",
                    "job": existing,
                    "gui": heartbeat,
                    "accepted_slots": normalize_slots(existing.get("slots")),
                    "skipped_slots": [],
                    "skipped_reasons": {},
                    "duplicate": True,
                }

        command_payload = dict(payload)
        requested_slots = normalize_slots(command_payload.get("slots"))
        accepted_slots = list(requested_slots)
        skipped_slots: list[int] = []
        skipped_reasons: dict[str, str] = {}
        if reserve_slots:
            reservations = reserved_gui_playback_slots(heartbeat)
            accepted_slots = [slot for slot in requested_slots if slot not in reservations]
            skipped_slots = [slot for slot in requested_slots if slot in reservations]
            skipped_reasons = {str(slot): reservations[slot] for slot in skipped_slots}
            if skipped_slots and not allow_partial:
                accepted_slots = []
            if not accepted_slots:
                busy_text = ",".join(str(slot) for slot in skipped_slots or requested_slots)
                raise GuiCommandAdmissionError(
                    f"所選 Slot 已有播放、排隊、定時或循環工作：{busy_text}；本次未建立新工作。",
                    HTTPStatus.CONFLICT,
                )
            command_payload["slots"] = accepted_slots

        if command_payload.get("replace_existing") and accepted_slots:
            GUI_COMMAND_BRIDGE.supersede_queued_slots(
                accepted_slots,
                actions={"play_module_chain", "play_script", "create_playback_automation"},
                reason="Superseded by the latest per-Slot playback setting",
            )

        command = GUI_COMMAND_BRIDGE.enqueue(
            action,
            command_payload,
            label=label,
            expires_in_seconds=GUI_COMMAND_TTL_SEC,
            request_id=clean_request_id or None,
        )
        return {
            "ok": True,
            "relayed_to": "GUI_TEST_PC",
            "job": command,
            "gui": heartbeat,
            "accepted_slots": accepted_slots,
            "skipped_slots": skipped_slots,
            "skipped_reasons": skipped_reasons,
            "duplicate": False,
        }


def get_gui_bridge_jobs() -> list[dict[str, Any]]:
    return GUI_COMMAND_BRIDGE.list_commands(limit=100)


def load_jobs() -> list[dict[str, Any]]:
    if not JOBS_PATH.exists():
        return []
    try:
        data = json.loads(JOBS_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, dict):
        data = data.get("jobs", [])
    if not isinstance(data, list):
        return []
    return [job for job in data if isinstance(job, dict)]


def save_jobs(jobs: list[dict[str, Any]]) -> None:
    ensure_dirs()
    payload = {"updated_at": utc_now_iso(), "jobs": jobs}
    JOBS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8-sig")


def new_job_id() -> str:
    return datetime.now().strftime("job-%Y%m%d-%H%M%S-%f")


def parse_run_at(body: dict[str, Any]) -> float:
    if "delay_seconds" in body:
        return time.time() + max(0, float(body.get("delay_seconds") or 0))
    if "delay_minutes" in body:
        return time.time() + max(0, float(body.get("delay_minutes") or 0)) * 60
    text = str(body.get("run_at") or "").strip()
    if text:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return parsed.timestamp()
        except ValueError:
            raise ValueError("run_at must be ISO datetime, or use delay_minutes/delay_seconds")
    return time.time()


def create_schedule_job(body: dict[str, Any]) -> dict[str, Any]:
    action = str(body.get("action") or "").strip()
    if action not in SCHEDULE_ACTION_MAP and action not in ("pc.shutdown", "pc.reboot"):
        raise ValueError(f"unsupported schedule action: {action}")
    if action in ("pc.shutdown", "pc.reboot") and body.get("confirm") is not True:
        raise ValueError("pc power actions require confirm=true")

    slots = normalize_slots(body.get("slots"))
    run_at = parse_run_at(body)
    job = {
        "id": new_job_id(),
        "action": action,
        "slots": slots,
        "forcebind_mode": normalize_forcebind_mode(body.get("forcebind_mode")),
        "use_windows_users": bool(body.get("use_windows_users")),
        "run_at": run_at,
        "run_at_iso": datetime.fromtimestamp(run_at).astimezone().isoformat(timespec="seconds"),
        "enabled": True,
        "status": "pending",
        "created_at": utc_now_iso(),
        "last_result": None,
    }
    with SCHEDULER_LOCK:
        jobs = load_jobs()
        jobs.append(job)
        save_jobs(jobs)
    return job


def execute_schedule_job(job: dict[str, Any]) -> dict[str, Any]:
    action = str(job.get("action") or "")
    slots = normalize_slots(job.get("slots"))
    if action in SCHEDULE_ACTION_MAP:
        return run_launcher_action_elevated(
            SCHEDULE_ACTION_MAP[action],
            slots,
            forcebind_mode=job.get("forcebind_mode", "netbind"),
            use_windows_users=bool(job.get("use_windows_users")),
        )
    if action == "pc.shutdown":
        return run_power_action("shutdown", int(job.get("power_delay_seconds", 30) or 30))
    if action == "pc.reboot":
        return run_power_action("reboot", int(job.get("power_delay_seconds", 30) or 30))
    raise ValueError(f"unsupported schedule action: {action}")


def run_power_action(action: str, delay_seconds: int) -> dict[str, Any]:
    seconds = max(0, min(86400, int(delay_seconds)))
    mode = "/s" if action == "shutdown" else "/r"
    args = ["shutdown.exe", mode, "/t", str(seconds), "/c", f"GUI_TEST_PC scheduled {action}"]
    completed = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    return {
        "ok": completed.returncode == 0,
        "action": action,
        "delay_seconds": seconds,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def scheduler_loop() -> None:
    while True:
        try:
            run_due_jobs_once()
        except Exception as exc:
            log(f"scheduler error: {exc}")
        time.sleep(SCHEDULER_POLL_SEC)


def start_scheduler() -> None:
    global SCHEDULER_STARTED
    if SCHEDULER_STARTED:
        return
    SCHEDULER_STARTED = True
    thread = threading.Thread(target=scheduler_loop, name="gui-test-pc-scheduler", daemon=True)
    thread.start()


def run_due_jobs_once() -> None:
    now = time.time()
    with SCHEDULER_LOCK:
        jobs = load_jobs()
        due_ids = [
            str(job.get("id"))
            for job in jobs
            if job.get("enabled", True) and job.get("status") == "pending" and float(job.get("run_at", 0) or 0) <= now
        ]
        if not due_ids:
            return
        for job in jobs:
            if str(job.get("id")) in due_ids:
                job["status"] = "running"
                job["started_at"] = utc_now_iso()
        save_jobs(jobs)

    for job_id in due_ids:
        result: dict[str, Any]
        try:
            jobs = load_jobs()
            job = next(item for item in jobs if str(item.get("id")) == job_id)
            result = execute_schedule_job(job)
            status = "done" if result.get("ok") else "failed"
        except Exception as exc:
            result = {"ok": False, "error": str(exc)}
            status = "failed"
        with SCHEDULER_LOCK:
            jobs = load_jobs()
            for item in jobs:
                if str(item.get("id")) == job_id:
                    item["status"] = status
                    item["finished_at"] = utc_now_iso()
                    item["last_result"] = result
            save_jobs(jobs)


def target_summary() -> dict[str, Any]:
    windows = enum_windows()
    games = game_windows(windows)
    selected = resolve_target(windows=windows)
    return {
        "app": APP_NAME,
        "version": VERSION,
        "selected": selected,
        "target_count": len(games),
        "all_window_count": len(windows),
        "targets": games,
        "target_slots": target_slots(games),
        "local_url": f"http://127.0.0.1:{SERVER_PORT}/",
        "lan_url": f"http://{local_ip()}:{SERVER_PORT}/",
    }


def target_slots(games: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_slot = {int(item["slot"]): item for item in games if item.get("slot") is not None}
    rows: list[dict[str, Any]] = []
    for slot in range(1, MAX_SLOT + 1):
        target = by_slot.get(slot)
        rows.append(
            {
                "slot": slot,
                "target_id": target["id"] if target else f"slot:{slot}",
                "running": target is not None,
                "target": target,
            }
        )
    return rows


INDEX_HTML = r"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>GUI_TEST_PC Game Stream</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #07111f;
      --panel: rgba(18, 26, 38, 0.94);
      --line: #334256;
      --text: #edf4ff;
      --muted: #9fb0c4;
      --accent: #46e6b0;
      --bad: #ff6b7a;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Microsoft JhengHei", "Noto Sans TC", "Segoe UI", sans-serif;
      color: var(--text);
      background:
        radial-gradient(circle at 10% 8%, rgba(70, 230, 176, .16), transparent 26rem),
        linear-gradient(135deg, #07111f, #0b1018 56%, #111827);
      line-height: 1.55;
    }
    main { width: min(1160px, calc(100% - 24px)); margin: 0 auto; padding: 24px 0 50px; }
    header, section {
      border: 1px solid var(--line);
      border-radius: 22px;
      background: var(--panel);
      box-shadow: 0 22px 70px rgba(0,0,0,.28);
    }
    header { padding: 24px; }
    section { margin-top: 16px; padding: 18px; }
    h1 { margin: 0 0 8px; font-size: clamp(1.9rem, 5vw, 3.4rem); line-height: 1.05; }
    h2 { margin: 0 0 12px; font-size: 1.25rem; }
    p { margin: 0 0 10px; color: var(--muted); }
    code { background: #07111f; border: 1px solid rgba(148,163,184,.28); border-radius: 7px; padding: .12em .36em; }
    .row { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
    button, select, input {
      min-height: 40px;
      border: 1px solid rgba(148,163,184,.35);
      border-radius: 12px;
      background: #0b1728;
      color: var(--text);
      padding: 8px 12px;
      font: inherit;
    }
    button:disabled, select:disabled { opacity: .52; cursor: not-allowed; }
    button.primary { background: linear-gradient(135deg, #14b89b, #2b7bd8); border-color: transparent; color: white; font-weight: 700; }
    button.danger { border-color: rgba(255,107,122,.55); color: #ffd8dd; }
    select { flex: 1 1 420px; max-width: 100%; }
    .streamWrap {
      display: grid;
      place-items: center;
      background: #020617;
      border: 1px solid rgba(148,163,184,.28);
      border-radius: 18px;
      overflow: hidden;
      min-height: 180px;
    }
    img {
      display: block;
      width: 100%;
      height: auto;
      object-fit: contain;
      background: #020617;
    }
    .status { white-space: pre-wrap; color: #dbeafe; font-family: Consolas, monospace; font-size: .92rem; }
    .pill { display:inline-flex; border: 1px solid rgba(70,230,176,.4); border-radius: 999px; padding: 4px 10px; margin: 4px 6px 0 0; color:#dcfff5; }
    .grid { display: grid; grid-template-columns: repeat(12, 1fr); gap: 12px; }
    .card { grid-column: span 6; border: 1px solid rgba(148,163,184,.25); border-radius: 16px; background: rgba(2,6,23,.42); padding: 14px; }
    .card.full { grid-column: span 12; }
    table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: .9rem; }
    th, td { border: 1px solid rgba(148,163,184,.22); padding: 8px; text-align: left; vertical-align: top; }
    th { color: #fff; background: rgba(70,230,176,.1); }
    .logBox { white-space: pre-wrap; max-height: 220px; overflow: auto; background: #020617; border: 1px solid rgba(148,163,184,.25); border-radius: 12px; padding: 10px; color: #dbeafe; font-family: Consolas, monospace; font-size: .85rem; }
    .mini { font-size: .84rem; color: var(--muted); }
    @media (max-width: 720px) {
      main { width: min(100% - 16px, 1160px); padding-top: 12px; }
      header, section { border-radius: 18px; }
      .card { grid-column: span 12; }
    }
  </style>
</head>
<body>
<main>
  <header>
    <h1>GUI_TEST_PC Game Stream</h1>
    <p>只串流星詠魔力遊戲視窗。串流開始前會先量度視窗 client 有效畫面，之後每個 frame 固定同一尺寸，手機端只做縮放，不裁切 Desktop。</p>
    <div>
      <span class="pill" id="localUrl">local</span>
      <span class="pill" id="lanUrl">lan</span>
      <span class="pill" id="selectedLabel">selected</span>
      <span class="pill" id="sizeLabel">size</span>
    </div>
  </header>

  <section>
    <h2>遊戲視窗</h2>
    <div class="row">
      <select id="targets"></select>
      <button id="refreshBtn">重新掃描</button>
      <button id="selectBtn" class="primary">選定視窗</button>
    </div>
    <p>視窗標題支援 <code>1</code> 到 <code>20</code>，並相容舊格式 <code>[01]</code>、<code>SCG001</code>。如果未看到遊戲視窗，先按啟動器的 Relabel 或重開遊戲。</p>
  </section>

  <section>
    <h2>串流</h2>
    <div class="row">
      <button id="startBtn" class="primary">開始串流</button>
      <button id="frameBtn">單張測試</button>
      <button id="stopBtn" class="danger">停止顯示</button>
      <label>FPS <input id="fps" type="number" min="1" max="30" value="8" style="width:80px"></label>
      <label>輸出寬度 <input id="width" type="number" min="0" max="2560" value="960" style="width:96px"></label>
    </div>
    <p>輸出寬度設 <code>0</code> 代表使用遊戲視窗原始有效尺寸；其他數值只等比縮放。</p>
    <div class="streamWrap" style="margin-top:12px">
      <img id="stream" alt="stream not started">
    </div>
  </section>

  <section>
    <h2>Launcher / 定時頁功能</h2>
    <p>此區只呼叫白名單 action，包裝 <code>D:\15game\starcg_15_control.ps1</code>；不會讓手機端送任意 PowerShell。</p>
    <div class="grid">
      <div class="card">
        <h3>遊戲開啟 / 關閉</h3>
        <label>Slots <input id="launcherSlots" value="1-20" style="width:170px"></label>
        <label>ForceBindIP
          <select id="launcherForceBind">
            <option value="netbind">netbind loopback-safe</option>
            <option value="off">off test</option>
            <option value="delayed">legacy delayed</option>
            <option value="normal">legacy normal old</option>
          </select>
        </label>
        <label><input id="launcherWindowsUsers" type="checkbox" checked> Windows user isolation</label>
        <div class="row" style="margin-top:10px">
          <button id="slotStatusBtn">刷新 20 槽</button>
          <button id="startSlotsBtn" class="primary">Start Selected</button>
          <button id="stopSlotsBtn" class="danger">Stop Selected</button>
          <button id="restartSlotsBtn">Restart Selected</button>
        </div>
        <div class="row" style="margin-top:10px">
          <button id="startMissingBtn">Start Missing</button>
          <button id="repairBadBtn">Repair Bad</button>
          <button id="relabelBtn">Relabel</button>
          <button id="bindTestBtn">Bind Test</button>
        </div>
        <p class="mini">Stop 會走控制腳本的 snapshot/stop 流程；Repair Bad 只在你明確按下或排程時執行。</p>
      </div>
      <div class="card">
        <h3>排程 / 電腦</h3>
        <label>Action
          <select id="scheduleAction">
            <option value="starcg.start">啟動遊戲 slots</option>
            <option value="starcg.stop">關閉遊戲 slots</option>
            <option value="starcg.restart">重啟遊戲 slots</option>
            <option value="starcg.start_missing">只啟動未運行</option>
            <option value="starcg.repair_bad">修復壞槽</option>
            <option value="starcg.relabel">重新命名視窗</option>
            <option value="pc.shutdown">關閉電腦</option>
            <option value="pc.reboot">重啟電腦</option>
          </select>
        </label>
        <div class="row" style="margin-top:10px">
          <label>Slots <input id="scheduleSlots" value="1-20" style="width:130px"></label>
          <label>延遲分鐘 <input id="scheduleDelay" type="number" min="0" value="10" style="width:90px"></label>
          <button id="addScheduleBtn" class="primary">新增排程</button>
          <button id="refreshJobsBtn">刷新排程</button>
        </div>
        <p class="mini">關機/重啟需要瀏覽器確認，並由 server 執行 Windows shutdown。沒有遠端開機功能。</p>
      </div>
      <div class="card full">
        <h3>20 槽狀態</h3>
        <div id="slotTable" class="status">尚未刷新</div>
      </div>
      <div class="card">
        <h3>排程列表</h3>
        <div id="jobsTable" class="status">尚未刷新</div>
      </div>
      <div class="card">
        <h3>Launcher Log</h3>
        <div id="launcherLog" class="logBox">尚未載入</div>
      </div>
    </div>
  </section>

  <section>
    <h2>狀態</h2>
    <div class="status" id="status">loading...</div>
  </section>
</main>
<script>
const targetsEl = document.getElementById('targets');
const streamEl = document.getElementById('stream');
const statusEl = document.getElementById('status');
const selectedLabel = document.getElementById('selectedLabel');
const sizeLabel = document.getElementById('sizeLabel');
const localUrl = document.getElementById('localUrl');
const lanUrl = document.getElementById('lanUrl');
const startBtn = document.getElementById('startBtn');
const frameBtn = document.getElementById('frameBtn');
const selectBtn = document.getElementById('selectBtn');
const slotTable = document.getElementById('slotTable');
const jobsTable = document.getElementById('jobsTable');
const launcherLog = document.getElementById('launcherLog');

function optionLabel(t) {
  const slot = t.slot ? `slot=${t.slot} ` : '';
  const source = t.slot_source ? `source=${t.slot_source} ` : '';
  return `${slot}${source}${t.title} | hwnd=${t.hwnd} pid=${t.pid} ${t.width || '?'}x${t.height || '?'}`;
}

function slotOptionLabel(row) {
  const slot = String(row.slot).padStart(2, '0');
  if (!row.running || !row.target) {
    return `[${slot}] 未啟動 / 未綁定 StarCG.exe`;
  }
  return `[${slot}] ${optionLabel(row.target)}`;
}

async function api(path, options) {
  const res = await fetch(path, options);
  const text = await res.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch (err) {
    data = {error: text || String(err)};
  }
  if (!res.ok) throw new Error(data.error || `${res.status} ${res.statusText}`);
  return data;
}

function setButtonsEnabled(enabled) {
  selectBtn.disabled = !enabled;
  startBtn.disabled = !enabled;
  frameBtn.disabled = !enabled;
}

async function refreshTargets() {
  const data = await api('/api/targets');
  targetsEl.innerHTML = '';
  if (!data.target_slots?.length) {
    const opt = document.createElement('option');
    opt.value = '';
    opt.textContent = '未取得 slot 1-20 資料';
    targetsEl.appendChild(opt);
    targetsEl.disabled = true;
    setButtonsEnabled(false);
  } else {
    targetsEl.disabled = false;
    let hasRunningTarget = false;
    for (const row of data.target_slots) {
      const opt = document.createElement('option');
      opt.value = row.target_id;
      opt.textContent = slotOptionLabel(row);
      opt.disabled = !row.running;
      if (row.running) hasRunningTarget = true;
      if (data.selected && row.target && data.selected.id === row.target.id) opt.selected = true;
      targetsEl.appendChild(opt);
    }
    setButtonsEnabled(hasRunningTarget);
  }
  localUrl.textContent = data.local_url;
  lanUrl.textContent = data.lan_url;
  selectedLabel.textContent = `selected: ${data.selected?.title || data.selected?.id || 'none'}`;
  sizeLabel.textContent = data.selected?.width ? `client: ${data.selected.width}x${data.selected.height}` : 'client: none';
  statusEl.textContent = JSON.stringify({
    app: data.app,
    version: data.version,
    selected: data.selected,
    target_count: data.target_count,
    all_window_count: data.all_window_count
  }, null, 2);
}

async function selectTarget() {
  const target_id = targetsEl.value;
  const data = await api('/api/select', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({target_id})
  });
  selectedLabel.textContent = `selected: ${data.selected?.title || data.selected?.id}`;
  sizeLabel.textContent = data.selected?.width ? `client: ${data.selected.width}x${data.selected.height}` : 'client: none';
  statusEl.textContent = JSON.stringify(data, null, 2);
  return data.selected;
}

async function measureTarget() {
  const target_id = targetsEl.value;
  const width = encodeURIComponent(document.getElementById('width').value || '960');
  const data = await api(`/api/measure?target_id=${encodeURIComponent(target_id)}&w=${width}`);
  const src = data.source_size;
  const out = data.output_size;
  sizeLabel.textContent = `client: ${src.w}x${src.h} / output: ${out.w}x${out.h}`;
  selectedLabel.textContent = `selected: ${data.target?.title || data.target_id}`;
  statusEl.textContent = JSON.stringify(data, null, 2);
  streamEl.style.aspectRatio = `${out.w} / ${out.h}`;
  return data;
}

async function startStream(singleFrame = false) {
  await selectTarget();
  const measured = await measureTarget();
  const fps = encodeURIComponent(document.getElementById('fps').value || '8');
  const width = encodeURIComponent(document.getElementById('width').value || '960');
  const target = encodeURIComponent(measured.target_id);
  const ts = Date.now();
  streamEl.src = singleFrame
    ? `/frame.jpg?target_id=${target}&w=${width}&_=${ts}`
    : `/stream.mjpg?target_id=${target}&fps=${fps}&w=${width}&_=${ts}`;
}

function slotsText(id) {
  return document.getElementById(id).value.trim();
}

function renderSlots(slots) {
  if (!Array.isArray(slots) || !slots.length) {
    slotTable.textContent = '沒有 slot 狀態資料';
    return;
  }
  const rows = slots.map(s => `
    <tr>
      <td>${s.Slot ?? ''}</td>
      <td>${s.Status ?? ''}</td>
      <td>${s.Responding}</td>
      <td>${s.Pids ?? ''}</td>
      <td>${s.Title ?? ''}</td>
      <td>${s.LoginData ?? ''}</td>
    </tr>`).join('');
  slotTable.innerHTML = `
    <table>
      <thead><tr><th>Slot</th><th>Status</th><th>Responding</th><th>Pids</th><th>Title</th><th>LoginData</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

function renderJobs(jobs) {
  if (!Array.isArray(jobs) || !jobs.length) {
    jobsTable.textContent = '沒有排程';
    return;
  }
  const rows = jobs.slice().reverse().map(j => `
    <tr>
      <td>${j.id || ''}</td>
      <td>${j.action || ''}</td>
      <td>${(j.slots || []).join(',')}</td>
      <td>${j.run_at_iso || ''}</td>
      <td>${j.status || ''}</td>
    </tr>`).join('');
  jobsTable.innerHTML = `
    <table>
      <thead><tr><th>ID</th><th>Action</th><th>Slots</th><th>Run At</th><th>Status</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>`;
}

async function refreshSlotStatus() {
  const data = await api('/api/starcg/slots');
  renderSlots(data.slots);
  statusEl.textContent = JSON.stringify({starcg_slots_ok: data.ok, count: data.slots?.length || 0}, null, 2);
}

async function refreshLauncherLog() {
  const data = await api('/api/starcg/logs?lines=80');
  launcherLog.textContent = (data.lines || []).join('\n') || '沒有 launcher log';
}

async function launcherAction(action) {
  const body = {
    slots: slotsText('launcherSlots'),
    forcebind_mode: document.getElementById('launcherForceBind')?.value || 'netbind',
    use_windows_users: Boolean(document.getElementById('launcherWindowsUsers')?.checked)
  };
  const data = await api(`/api/starcg/${action}`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)
  });
  statusEl.textContent = JSON.stringify(data, null, 2);
  await refreshSlotStatus().catch(() => {});
  await refreshLauncherLog().catch(() => {});
}

async function refreshJobs() {
  const data = await api('/api/schedule/jobs');
  renderJobs(data.jobs);
}

async function addScheduleJob() {
  const action = document.getElementById('scheduleAction').value;
  const delay = Number(document.getElementById('scheduleDelay').value || '0');
  const body = {
    action,
    slots: slotsText('scheduleSlots'),
    delay_minutes: delay,
    forcebind_mode: document.getElementById('launcherForceBind')?.value || 'netbind',
    use_windows_users: Boolean(document.getElementById('launcherWindowsUsers')?.checked)
  };
  if (action === 'pc.shutdown' || action === 'pc.reboot') {
    if (!confirm(`確認要排程 ${action}？`)) return;
    body.confirm = true;
  }
  const data = await api('/api/schedule/jobs', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)
  });
  statusEl.textContent = JSON.stringify(data.job, null, 2);
  renderJobs(data.jobs);
}

document.getElementById('refreshBtn').addEventListener('click', () => refreshTargets().catch(err => statusEl.textContent = String(err)));
selectBtn.addEventListener('click', () => selectTarget().catch(err => statusEl.textContent = String(err)));
startBtn.addEventListener('click', () => startStream(false).catch(err => statusEl.textContent = String(err)));
frameBtn.addEventListener('click', () => startStream(true).catch(err => statusEl.textContent = String(err)));
document.getElementById('stopBtn').addEventListener('click', () => { streamEl.removeAttribute('src'); });
document.getElementById('slotStatusBtn').addEventListener('click', () => refreshSlotStatus().catch(err => statusEl.textContent = String(err)));
document.getElementById('startSlotsBtn').addEventListener('click', () => launcherAction('start').catch(err => statusEl.textContent = String(err)));
document.getElementById('stopSlotsBtn').addEventListener('click', () => launcherAction('stop').catch(err => statusEl.textContent = String(err)));
document.getElementById('restartSlotsBtn').addEventListener('click', () => launcherAction('restart').catch(err => statusEl.textContent = String(err)));
document.getElementById('startMissingBtn').addEventListener('click', () => launcherAction('start-missing').catch(err => statusEl.textContent = String(err)));
document.getElementById('repairBadBtn').addEventListener('click', () => launcherAction('repair-bad').catch(err => statusEl.textContent = String(err)));
document.getElementById('relabelBtn').addEventListener('click', () => launcherAction('relabel').catch(err => statusEl.textContent = String(err)));
document.getElementById('bindTestBtn').addEventListener('click', () => launcherAction('bind-test').catch(err => statusEl.textContent = String(err)));
document.getElementById('addScheduleBtn').addEventListener('click', () => addScheduleJob().catch(err => statusEl.textContent = String(err)));
document.getElementById('refreshJobsBtn').addEventListener('click', () => refreshJobs().catch(err => statusEl.textContent = String(err)));
refreshTargets().catch(err => statusEl.textContent = String(err));
refreshSlotStatus().catch(err => statusEl.textContent = String(err));
refreshJobs().catch(err => statusEl.textContent = String(err));
refreshLauncherLog().catch(() => {});
</script>
</body>
</html>
"""


class GuiControllerRestarter:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._restart_thread: threading.Thread | None = None
        self._restarting = False
        self._last_restart_at = 0.0
        self._last_reason = ""
        self._last_error = ""
        self._last_started_pid: int | None = None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "restarting": self._restarting,
                "last_restart_at": (
                    datetime.fromtimestamp(self._last_restart_at, timezone.utc).isoformat()
                    if self._last_restart_at
                    else None
                ),
                "last_reason": self._last_reason or None,
                "last_error": self._last_error or None,
                "last_started_pid": self._last_started_pid,
            }

    def request_restart(self, reason: str) -> dict[str, Any]:
        with self._lock:
            if self._restarting:
                return {**self.snapshot(), "accepted": False, "message": "控制器正在重啟"}
            self._restarting = True
            self._last_reason = str(reason)
            self._last_error = ""
            self._restart_thread = threading.Thread(
                target=self._restart_worker,
                args=(str(reason),),
                name="gui-test-pc-controller-restart",
                daemon=True,
            )
            self._restart_thread.start()
            return {**self.snapshot(), "accepted": True, "message": "控制器重啟已開始"}

    def _restart_worker(self, reason: str) -> None:
        try:
            heartbeat = GUI_COMMAND_BRIDGE.read_heartbeat()
            old_pid = int(heartbeat.get("pid") or 0) if isinstance(heartbeat, dict) else 0
            interrupted = GUI_COMMAND_BRIDGE.interrupt_inflight(
                f"GUI_TEST_PC controller restart: {reason}"
            )
            if interrupted:
                log(f"controller restart expired/interrupted commands={interrupted}")
            if old_pid > 0:
                self._terminate_process(old_pid)
            GUI_COMMAND_BRIDGE.write_heartbeat(
                {
                    "online": False,
                    "pid": old_pid or None,
                    "restarting": True,
                    "shutdown_requested": False,
                    "restart_reason": reason,
                    "execution_owner": "GUI_TEST_PC",
                }
            )
            process = self._launch_gui()
            with self._lock:
                self._last_started_pid = int(process.pid)
                self._last_restart_at = time.time()
            log(f"controller restart launched pid={process.pid} reason={reason}")
        except Exception as exc:
            with self._lock:
                self._last_error = str(exc)
                self._last_restart_at = time.time()
            log(f"controller restart failed reason={reason} error={exc}")
        finally:
            with self._lock:
                self._restarting = False

    @staticmethod
    def _terminate_process(pid: int) -> None:
        if pid <= 0 or pid == os.getpid():
            return
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(
            ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=flags,
            check=False,
        )
        if result.returncode == 0:
            return
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"cannot terminate GUI_TEST_PC pid={pid}: {detail}")
        winerror = ctypes.get_last_error()
        if winerror not in {87, 1168}:
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(
                f"cannot verify terminated GUI_TEST_PC pid={pid}: winerror={winerror} {detail}"
            )

    @staticmethod
    def _launch_gui() -> subprocess.Popen[Any]:
        current_python = Path(sys.executable)
        python = current_python.with_name("python.exe")
        executable = python if python.is_file() else current_python
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        creationflags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
        environment = os.environ.copy()
        environment["PYTHONUTF8"] = "1"
        stdout_path = LOG_DIR / "gui_controller_restart.out.log"
        stderr_path = LOG_DIR / "gui_controller_restart.err.log"
        stdout_handle = stdout_path.open("a", encoding="utf-8", buffering=1)
        stderr_handle = stderr_path.open("a", encoding="utf-8", buffering=1)
        try:
            return subprocess.Popen(
                [str(executable), str(ROOT / "gui_test_pc.py")],
                cwd=str(ROOT),
                env=environment,
                stdout=stdout_handle,
                stderr=stderr_handle,
                creationflags=creationflags,
            )
        finally:
            stdout_handle.close()
            stderr_handle.close()


class GuiTestPcHandler(BaseHTTPRequestHandler):
    server_version = f"{APP_NAME}/{VERSION}"

    def log_message(self, fmt: str, *args: Any) -> None:
        log(f"{self.client_address[0]} {fmt % args}")

    def send_bytes(self, data: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        self.send_bytes(
            json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
            "application/json; charset=utf-8",
            status,
        )

    def send_static_file(self, path: Path) -> None:
        root = STATIC_DIR.resolve()
        resolved = path.resolve()
        if root not in resolved.parents and resolved != root:
            self.send_error(HTTPStatus.FORBIDDEN, "Forbidden")
            return
        if not resolved.exists() or not resolved.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        content_type = mimetypes.guess_type(str(resolved))[0] or "application/octet-stream"
        if resolved.suffix == ".webmanifest":
            content_type = "application/manifest+json"
        if resolved.suffix == ".js":
            content_type = "text/javascript; charset=utf-8"
        if resolved.suffix == ".html":
            content_type = "text/html; charset=utf-8"
        self.send_bytes(resolved.read_bytes(), content_type)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == TAILSCALE_PATH_PREFIX:
            path = "/"
        elif path.startswith(TAILSCALE_PATH_PREFIX + "/"):
            path = path[len(TAILSCALE_PATH_PREFIX) :]
        query = parse_qs(parsed.query)

        if path in ("/", "/index.html"):
            self.send_bytes(INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/mobile":
            self.send_response(HTTPStatus.MOVED_PERMANENTLY)
            self.send_header("Location", "/mobile/")
            self.end_headers()
            return
        if path == "/mobile/" or path == "/mobile/index.html":
            self.send_static_file(STATIC_DIR / "mobile" / "index.html")
            return
        if path.startswith("/mobile/"):
            relative = path.removeprefix("/mobile/").strip("/")
            if not relative:
                relative = "index.html"
            self.send_static_file(STATIC_DIR / "mobile" / relative)
            return
        if path == "/api/status":
            self.send_json(target_summary())
            return
        if path == "/api/targets":
            self.send_json(target_summary())
            return
        if path == "/api/scripts":
            self.send_json({"ok": True, "scripts": list_pc_scripts(), "script_dir": str(SCRIPT_DIR)})
            return
        if path == "/api/modules":
            self.send_json({"ok": True, "modules": load_pc_modules(), "path": str(MODULES_PATH)})
            return
        if path == "/api/module-groups":
            self.send_json(
                {"ok": True, "groups": load_pc_module_groups(), "path": str(MODULE_GROUPS_PATH)}
            )
            return
        if path == "/api/module-chain-presets":
            self.send_json(
                {
                    "ok": True,
                    "presets": load_module_chain_presets(),
                    "path": str(MODULE_CHAIN_PRESETS_PATH),
                }
            )
            return
        if path == "/api/play/jobs":
            self.send_json(
                {
                    "ok": True,
                    "jobs": get_gui_bridge_jobs(),
                    "gui": GUI_COMMAND_BRIDGE.read_heartbeat(),
                    "execution_owner": "GUI_TEST_PC",
                    "controller": gui_controller_status(),
                }
            )
            return
        if path == "/api/controller/status":
            self.send_json({"ok": True, "controller": gui_controller_status()})
            return
        if path == "/api/starcg/slots":
            self.handle_starcg_slots()
            return
        if path == "/api/starcg/bind-test":
            slots = normalize_slots(query.get("slots", [""])[0])
            self.handle_starcg_action("bind-test", {"slots": slots})
            return
        if path == "/api/starcg/layout":
            slots = normalize_slots(query.get("slots", [""])[0])
            self.handle_window_layout("status", {"slots": slots})
            return
        if path == "/api/starcg/logs":
            lines = int(query.get("lines", ["80"])[0] or "80")
            self.send_json({"ok": True, "path": str(LAUNCHER_LOG_PATH), "lines": read_launcher_log_tail(lines)})
            return
        if path == "/api/schedule/jobs":
            self.send_json({"ok": True, "jobs": load_jobs(), "path": str(JOBS_PATH)})
            return
        if path == "/api/measure":
            self.handle_measure(query)
            return
        if path == "/frame.jpg":
            target_id = query.get("target_id", [None])[0]
            width = int(query.get("w", [str(DEFAULT_WIDTH)])[0] or DEFAULT_WIDTH)
            quality = int(query.get("quality", [str(DEFAULT_QUALITY)])[0] or DEFAULT_QUALITY)
            self.send_bytes(capture_jpeg(target_id, width, quality), "image/jpeg")
            return
        if path == "/stream.mjpg":
            self.stream_mjpeg(query)
            return
        if path == "/manifest.webmanifest":
            self.send_json(
                {
                    "name": "GUI_TEST_PC",
                    "short_name": "GUI_TEST_PC",
                    "start_url": "/",
                    "display": "standalone",
                    "background_color": "#07111f",
                    "theme_color": "#07111f",
                }
            )
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == TAILSCALE_PATH_PREFIX:
            path = "/"
        elif path.startswith(TAILSCALE_PATH_PREFIX + "/"):
            path = path[len(TAILSCALE_PATH_PREFIX) :]
        body = parse_json_body(self)
        if path == "/api/select":
            self.handle_select(body)
            return
        if path == "/api/click":
            self.handle_click(body)
            return
        if path == "/api/play/script":
            self.handle_play_script(body)
            return
        if path == "/api/play/module":
            self.handle_play_module(body)
            return
        if path == "/api/play/module-chain":
            self.handle_play_module_chain(body)
            return
        if path == "/api/play/automation":
            self.handle_playback_automation_create(body)
            return
        if path == "/api/play/automation/cancel":
            self.handle_playback_automation_cancel(body)
            return
        if path == "/api/controller/restart":
            restarter = GUI_CONTROLLER_RESTARTER
            if restarter is None:
                self.send_json({"ok": False, "error": "controller restarter is unavailable"}, 503)
                return
            result = restarter.request_restart("native OPLINK manual recovery")
            self.send_json({"ok": True, "controller": result}, 202)
            return
        if path.startswith("/api/module-chain-presets/"):
            self.handle_save_module_chain_preset(path, body)
            return
        if path == "/api/play/stop-all":
            try:
                result = enqueue_gui_command(
                    "stop_all_playback",
                    {},
                    label="stop-all-playback",
                    request_id=body.get("request_id"),
                )
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, getattr(exc, "http_status", 400))
                return
            self.send_json(result)
            return
        if path == "/api/play/stop-slot":
            slots = normalize_slots(body.get("slots") or body.get("slot"))
            if len(slots) != 1:
                self.send_json({"ok": False, "error": "exactly one slot is required"}, 400)
                return
            slot = slots[0]
            try:
                result = enqueue_gui_command(
                    "stop_slot_playback",
                    {"slots": [slot]},
                    label=f"stop-slot:{slot}",
                    request_id=body.get("request_id"),
                )
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, getattr(exc, "http_status", 400))
                return
            self.send_json(result)
            return
        if path.startswith("/api/starcg/layout/"):
            layout_action = path.removeprefix("/api/starcg/layout/").strip("/")
            if layout_action == "check":
                layout_action = "status"
            self.handle_window_layout_relay(layout_action, body)
            return
        if path.startswith("/api/starcg/"):
            action = path.removeprefix("/api/starcg/").strip("/")
            if action == "login/snapshot":
                action = "snapshot-login"
            elif action == "login/restore":
                action = "restore-login"
            self.handle_starcg_action(action, body)
            return
        if path == "/api/schedule/jobs":
            self.handle_schedule_create(body)
            return
        if path == "/api/schedule/cancel":
            self.handle_schedule_cancel(body)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def handle_select(self, body: dict[str, Any]) -> None:
        target_id = str(body.get("target_id") or body.get("id") or "")
        try:
            selected = resolve_target(target_id, allow_auto=False, strict=True)
        except TargetLookupError as exc:
            self.send_json({"ok": False, "error": str(exc)}, 404)
            return
        STATE.set(str(selected["id"]))
        log(f"selected target {selected.get('id')} {selected.get('title')}")
        self.send_json({"ok": True, "selected": selected})

    def handle_measure(self, query: dict[str, list[str]]) -> None:
        target_id = query.get("target_id", [None])[0]
        width = int(query.get("w", [str(DEFAULT_WIDTH)])[0] or DEFAULT_WIDTH)
        quality = int(query.get("quality", [str(DEFAULT_QUALITY)])[0] or DEFAULT_QUALITY)
        try:
            session = CaptureSession.open(target_id, width, quality)
        except TargetLookupError as exc:
            self.send_json({"ok": False, "error": str(exc)}, 404)
            return
        self.send_json({"ok": True, **session.as_dict()})

    def handle_starcg_slots(self) -> None:
        try:
            result = run_launcher_action("status", use_windows_users=WINDOWS_USER_CONFIG.exists())
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, 500)
            return
        self.send_json({"ok": result.get("ok", False), "slots": result.get("data"), "raw": result})

    def handle_starcg_action(self, action: str, body: dict[str, Any]) -> None:
        if action not in STARCG_ACTION_MAP:
            self.send_json({"ok": False, "error": f"unsupported launcher action: {action}"}, 400)
            return
        slots = normalize_slots(body.get("slots"))
        payload = {
            "action": STARCG_ACTION_MAP[action],
            "slots": slots,
            "forcebind_mode": normalize_forcebind_mode(body.get("forcebind_mode")),
            "use_windows_users": bool(body.get("use_windows_users")),
        }
        try:
            result = enqueue_gui_command(
                "launcher_action",
                payload,
                label=f"launcher:{payload['action']}",
                request_id=body.get("request_id"),
            )
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, getattr(exc, "http_status", 400))
            return
        self.send_json(result)

    def handle_window_layout_relay(self, action: str, body: dict[str, Any]) -> None:
        if action not in {"status", "ensure", "arrange"}:
            self.send_json({"ok": False, "error": f"unsupported window layout action: {action}"}, 400)
            return
        slots = normalize_slots(body.get("slots")) or list(range(1, MAX_SLOT + 1))
        try:
            result = enqueue_gui_command(
                "window_layout",
                {"action": "ensure" if action == "arrange" else action, "slots": slots},
                label=f"window-layout:{action}",
                request_id=body.get("request_id"),
            )
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, getattr(exc, "http_status", 400))
            return
        self.send_json(result)

    def handle_window_layout(self, action: str, body: dict[str, Any]) -> None:
        slots = normalize_slots(body.get("slots")) or list(range(1, MAX_SLOT + 1))
        try:
            result = run_window_layout_action(action, slots)
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, 500)
            return
        self.send_json(result, 200 if result.get("ok") else 409)

    def handle_schedule_create(self, body: dict[str, Any]) -> None:
        try:
            job = create_schedule_job(body)
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, 400)
            return
        self.send_json({"ok": True, "job": job, "jobs": load_jobs()})

    def handle_schedule_cancel(self, body: dict[str, Any]) -> None:
        job_id = str(body.get("id") or "").strip()
        if not job_id:
            self.send_json({"ok": False, "error": "id is required"}, 400)
            return
        with SCHEDULER_LOCK:
            jobs = load_jobs()
            found = False
            for job in jobs:
                if str(job.get("id")) == job_id:
                    found = True
                    if job.get("status") in ("pending", "running"):
                        job["status"] = "cancelled"
                        job["enabled"] = False
                        job["finished_at"] = utc_now_iso()
            save_jobs(jobs)
        self.send_json({"ok": found, "jobs": load_jobs()}, 200 if found else 404)

    def handle_play_script(self, body: dict[str, Any]) -> None:
        try:
            slots = normalize_slots(body.get("slots"))
            script_name = str(body.get("script") or body.get("script_name") or "")
            script = script_path_from_name(script_name)
            result = enqueue_gui_command(
                "play_script",
                {"slots": slots, "script": script.name, "replace_existing": True},
                label=f"script:{script.name}",
                request_id=body.get("request_id"),
            )
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, getattr(exc, "http_status", 400))
            return
        self.send_json(result)

    def handle_play_module(self, body: dict[str, Any]) -> None:
        try:
            slots = normalize_slots(body.get("slots"))
            module_name = str(body.get("module") or body.get("module_name") or "").strip()
            scripts = module_script_paths(module_name)
            if not scripts:
                raise ValueError(f"module has no valid scripts: {module_name}")
            validate_module_play_request(slots, [module_name])
            result = enqueue_gui_command(
                "play_module_chain",
                {"slots": slots, "modules": [module_name], "replace_existing": True},
                label=f"module:{module_name}",
                request_id=body.get("request_id"),
            )
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, getattr(exc, "http_status", 400))
            return
        self.send_json(result)

    def handle_play_module_chain(self, body: dict[str, Any]) -> None:
        try:
            slots = normalize_slots(body.get("slots"))
            raw_names = body.get("modules") or body.get("module_names") or []
            if not isinstance(raw_names, list):
                raise ValueError("modules must be an ordered list")
            module_names = [str(name).strip() for name in raw_names if str(name).strip()]
            if not module_names:
                raise ValueError("at least one module is required")
            validate_module_play_request(slots, module_names)
            result = enqueue_gui_command(
                "play_module_chain",
                {"slots": slots, "modules": module_names, "replace_existing": True},
                label="module-chain:" + " > ".join(module_names),
                request_id=body.get("request_id"),
            )
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, getattr(exc, "http_status", 400))
            return
        self.send_json(result)

    def handle_playback_automation_create(self, body: dict[str, Any]) -> None:
        try:
            payload = normalize_playback_automation_payload(body)
            payload["replace_existing"] = True
            mode_label = "loop" if payload["mode"] == "loop" else "scheduled"
            result = enqueue_gui_command(
                "create_playback_automation",
                payload,
                label=f"playback-automation:{mode_label}",
                request_id=body.get("request_id"),
            )
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, getattr(exc, "http_status", 400))
            return
        self.send_json(result)

    def handle_playback_automation_cancel(self, body: dict[str, Any]) -> None:
        job_id = str(body.get("id") or body.get("job_id") or "").strip()
        if not job_id:
            self.send_json({"ok": False, "error": "playback automation id is required"}, 400)
            return
        try:
            result = enqueue_gui_command(
                "cancel_playback_automation",
                {"id": job_id},
                label=f"cancel-playback-automation:{job_id}",
                request_id=body.get("request_id"),
            )
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, getattr(exc, "http_status", 400))
            return
        self.send_json(result)

    def handle_save_module_chain_preset(self, path: str, body: dict[str, Any]) -> None:
        try:
            index = int(path.removeprefix("/api/module-chain-presets/").strip("/"))
            raw_modules = body.get("modules")
            if not isinstance(raw_modules, list):
                raise ValueError("modules must be an ordered list")
            preset = save_module_chain_preset(index, str(body.get("name") or ""), raw_modules)
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc)}, 400)
            return
        self.send_json({"ok": True, "preset": preset, "presets": load_module_chain_presets()})

    def handle_click(self, body: dict[str, Any]) -> None:
        try:
            target = resolve_target(str(body.get("target_id") or STATE.get()), strict=True)
        except TargetLookupError as exc:
            self.send_json({"ok": False, "error": str(exc)}, 400)
            return
        if target.get("kind") != "window" or not target.get("client_rect"):
            self.send_json({"ok": False, "error": "click requires selected game window"}, 400)
            return
        try:
            x = int(body.get("x", 0))
            y = int(body.get("y", 0))
        except (TypeError, ValueError):
            self.send_json({"ok": False, "error": "x/y must be integers"}, 400)
            return
        width = int(target.get("width") or 0)
        height = int(target.get("height") or 0)
        if width > 0:
            x = max(0, min(width - 1, x))
        if height > 0:
            y = max(0, min(height - 1, y))
        hwnd = int(target["hwnd"])
        try:
            post_client_click(hwnd, x, y, int(body.get("duration_ms", 35) or 35))
        except Exception as exc:
            self.send_json({"ok": False, "error": str(exc), "target": target}, 500)
            return
        log(f"background click hwnd={hwnd} client=({x},{y})")
        self.send_json({"ok": True, "target": target, "client": [x, y], "backend": "window_message"})

    def stream_mjpeg(self, query: dict[str, list[str]]) -> None:
        target_id = query.get("target_id", [None])[0]
        width = int(query.get("w", [str(DEFAULT_WIDTH)])[0] or DEFAULT_WIDTH)
        quality = int(query.get("quality", [str(DEFAULT_QUALITY)])[0] or DEFAULT_QUALITY)
        fps = float(query.get("fps", [str(DEFAULT_FPS)])[0] or DEFAULT_FPS)
        fps = max(1.0, min(30.0, fps))
        try:
            session = CaptureSession.open(target_id, width, quality)
        except TargetLookupError as exc:
            self.send_json({"ok": False, "error": str(exc)}, 404)
            return

        interval = 1.0 / fps
        boundary = "gui-test-pc-frame"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={boundary}")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        log(
            f"stream start client={self.client_address[0]} target={session.target_id} "
            f"fps={fps} source={session.source_width}x{session.source_height} width={width}"
        )
        try:
            while True:
                start = time.time()
                frame = session.capture_jpeg()
                self.wfile.write(f"--{boundary}\r\n".encode("ascii"))
                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                self.wfile.write(f"Content-Length: {len(frame)}\r\n\r\n".encode("ascii"))
                self.wfile.write(frame)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
                elapsed = time.time() - start
                if elapsed < interval:
                    time.sleep(interval - elapsed)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            log(f"stream disconnected client={self.client_address[0]}")
        except Exception as exc:
            log(f"stream error client={self.client_address[0]} target={session.target_id} error={exc}")


SERVER_PORT = DEFAULT_PORT


def run_server(host: str, port: int) -> None:
    global GUI_CONTROLLER_RESTARTER, SERVER_PORT
    SERVER_PORT = port
    ensure_dirs()
    prune_server_log()
    start_scheduler()
    GUI_CONTROLLER_RESTARTER = GuiControllerRestarter()
    httpd = ThreadingHTTPServer((host, port), GuiTestPcHandler)
    ip = local_ip()
    log(f"{APP_NAME} {VERSION} listening on {host}:{port}")
    log(f"local: http://127.0.0.1:{port}/")
    log(f"lan:   http://{ip}:{port}/")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("server stopped by keyboard interrupt")
    finally:
        httpd.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GUI_TEST_PC game-window streaming server")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    run_server(args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
