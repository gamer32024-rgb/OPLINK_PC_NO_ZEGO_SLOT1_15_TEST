from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import ctypes
import faulthandler
import threading
import time
import traceback
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

EXPECTED_VENV = Path(r"C:\Users\andyb\Documents\star_cros_bot\.venv")
EXPECTED_VENV_PYTHON = EXPECTED_VENV / "Scripts" / "python.exe"


def ensure_project_runtime() -> None:
    """Re-launch direct .py starts inside the project venv."""
    if __name__ != "__main__" or Path(sys.prefix).resolve() == EXPECTED_VENV.resolve():
        return
    if not EXPECTED_VENV_PYTHON.is_file():
        raise RuntimeError(f"GUI_TEST_PC venv Python was not found: {EXPECTED_VENV_PYTHON}")
    subprocess.Popen(
        [str(EXPECTED_VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]],
        cwd=str(Path(__file__).resolve().parent),
    )
    raise SystemExit(0)


ensure_project_runtime()

if sys.platform == "win32":
    import io

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from PyQt5.QtCore import QDateTime, QSize, QThread, QTimer, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from gui_test_pc_server import (  # noqa: E402
    CONFIG_DIR,
    ELEVATED_LAUNCHER_ACTIONS,
    GUI_COMMAND_BRIDGE_ROOT,
    MAX_SLOT,
    MODULE_GROUPS_PATH,
    SCRIPT_DIR,
    create_schedule_job,
    ensure_dirs,
    enum_windows,
    game_windows,
    load_jobs,
    normalize_slots,
    read_launcher_log_tail,
    run_due_jobs_once,
    run_launcher_action,
    run_launcher_action_elevated,
    run_window_layout_action,
    script_path_from_name,
    target_slots,
)
from starcg_bot.gui_command_bridge import GuiCommandBridge  # noqa: E402
from starcg_bot.live_touch_bridge import GuiTestPcLiveTouchServer  # noqa: E402
from starcg_bot.cooperative_playback import CooperativePlaybackCoordinator  # noqa: E402
from starcg_bot.playback_automation import PlaybackAutomationStore  # noqa: E402

MODULES_PATH = CONFIG_DIR / "modules_pc.json"
MEASUREMENTS_PATH = CONFIG_DIR / "window_measurements.json"
PICO_TOUCH_CONFIG_PATH = CONFIG_DIR / "pico_touch.json"
LAUNCHER_AUTOPLAY_SETTINGS_PATH = CONFIG_DIR / "launcher_autoplay.json"
WINDOW_LAYOUT_CONFIG_PATH = CONFIG_DIR / "window_layout.json"
PLAYBACK_AUTOMATION_PATH = CONFIG_DIR / "playback_automations.json"
CRASH_LOG_PATH = ROOT / "logs_pc" / "gui_test_pc_crash.log"
ACTIVITY_LOG_PATH = ROOT / "logs_pc" / "gui_test_pc_activity.log"
RECORDING_SAFETY_LIMIT_SECONDS = 20 * 60
PLAYBACK_GROUP_SIZE = 5
PLAYBACK_LONG_WAIT_SECONDS = 10.0
MODULE_CHAIN_GAP_SECONDS = 2.0
PLAYBACK_SPEED = 1.0
AUTOPLAY_DEFAULT_CLIENT_SIZE = (1280, 720)
AUTOPLAY_CLIENT_SIZE_TOLERANCE = 2
AUTOPLAY_READY_STABLE_CHECKS = 3
AUTOPLAY_READY_POLL_MS = 1000
AUTOPLAY_READY_TIMEOUT_SECONDS = 180.0
_FAULT_LOG_FILE = None
_ACTIVITY_LOG_LOCK = threading.Lock()
_CONTROLLER_MUTEX_HANDLE = None
_CONTROLLER_MUTEX_NAME = "Local\\GUI_TEST_PC_Controller_20260703"
ERROR_ALREADY_EXISTS = 183


def acquire_controller_instance_mutex() -> bool:
    """Keep one GUI owner for the heartbeat, command queue, and Pico bridge."""
    global _CONTROLLER_MUTEX_HANDLE
    if sys.platform != "win32":
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_bool
    ctypes.set_last_error(0)
    handle = kernel32.CreateMutexW(None, False, _CONTROLLER_MUTEX_NAME)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return False
    _CONTROLLER_MUTEX_HANDLE = handle
    return True


def summarize_playback_integrity(
    plans_by_slot: dict[int, list[dict[str, Any]]],
    results: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    cancelled_slots: list[int],
) -> dict[str, Any]:
    expected_by_slot = {
        int(slot): {
            int(step.get("step_index", index - 1)) + 1
            for index, step in enumerate(plan, start=1)
        }
        for slot, plan in plans_by_slot.items()
    }
    acknowledged_by_slot = {slot: set() for slot in expected_by_slot}
    acknowledgement_failures: list[dict[str, Any]] = []
    for row in results:
        slot = int(row.get("slot") or 0)
        module_index = int(row.get("module_index") or 0)
        event_count = int(row.get("event_count") or 0)
        executed_event_count = int(row.get("executed_event_count") or 0)
        if "step_acknowledged" in row:
            acknowledged = (
                not bool(row.get("cancelled"))
                and bool(row.get("step_acknowledged"))
            )
        else:
            acknowledged = (
                not bool(row.get("cancelled"))
                and bool(row.get("hid_acknowledged"))
                and executed_event_count == event_count
            )
        if acknowledged and slot in acknowledged_by_slot:
            acknowledged_by_slot[slot].add(module_index)
        else:
            acknowledgement_failures.append(
                {
                    "slot": slot,
                    "module_index": module_index,
                    "module": str(row.get("module") or ""),
                    "event_count": event_count,
                    "executed_event_count": executed_event_count,
                    "cancelled": bool(row.get("cancelled")),
                    "outcome": str(row.get("outcome") or ""),
                }
            )

    missing_by_slot = {
        str(slot): sorted(expected - acknowledged_by_slot.get(slot, set()))
        for slot, expected in expected_by_slot.items()
        if expected - acknowledged_by_slot.get(slot, set())
    }
    incomplete_slots = sorted(
        {
            *(int(slot) for slot in missing_by_slot),
            *(int(row.get("slot") or 0) for row in acknowledgement_failures),
        }
        - {0}
    )
    expected_module_count = sum(len(indices) for indices in expected_by_slot.values())
    acknowledged_module_count = sum(
        len(indices) for indices in acknowledged_by_slot.values()
    )
    clean_cancelled_slots = sorted({int(slot) for slot in cancelled_slots})
    return {
        "ok": not errors and not clean_cancelled_slots and not incomplete_slots,
        "all_steps_acknowledged": (
            not errors
            and not clean_cancelled_slots
            and not incomplete_slots
            and acknowledged_module_count == expected_module_count
        ),
        "expected_module_count": expected_module_count,
        "acknowledged_module_count": acknowledged_module_count,
        "missing_module_indices_by_slot": missing_by_slot,
        "acknowledgement_failures": acknowledgement_failures,
        "incomplete_slots": incomplete_slots,
    }


def append_crash_log(message: str) -> None:
    try:
        CRASH_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with CRASH_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(message.rstrip() + "\n")
    except Exception:
        pass


def append_activity_log(message: str) -> None:
    try:
        ACTIVITY_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _ACTIVITY_LOG_LOCK, ACTIVITY_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(message.rstrip() + "\n")
    except Exception:
        pass


def prune_activity_log(retention_hours: float = 24.0) -> None:
    cutoff = datetime.now().astimezone() - timedelta(hours=max(1.0, float(retention_hours)))
    try:
        with _ACTIVITY_LOG_LOCK:
            if not ACTIVITY_LOG_PATH.is_file():
                return
            retained: list[str] = []
            for line in ACTIVITY_LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    timestamp = datetime.fromisoformat(line[:19]).astimezone()
                except (TypeError, ValueError):
                    continue
                if timestamp >= cutoff:
                    retained.append(line)
            temp = ACTIVITY_LOG_PATH.with_suffix(".log.prune.tmp")
            temp.write_text(("\n".join(retained) + "\n") if retained else "", encoding="utf-8")
            os.replace(temp, ACTIVITY_LOG_PATH)
    except OSError:
        pass


def load_launcher_autoplay_settings(
    path: Path = LAUNCHER_AUTOPLAY_SETTINGS_PATH,
    *,
    include_modules: bool = False,
) -> dict[str, Any]:
    defaults: dict[str, Any] = {"module": None, "delay_seconds": 3.0}
    if include_modules:
        defaults["modules"] = []
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError):
        return defaults
    if not isinstance(payload, dict):
        return defaults
    raw_modules = payload.get("modules")
    modules: list[str] = []
    if isinstance(raw_modules, list):
        modules = [str(name).strip() for name in raw_modules if str(name).strip()]
    if not modules:
        module_text = str(payload.get("module") or "").strip()
        if module_text:
            modules = [module_text]
    try:
        delay_seconds = float(payload.get("delay_seconds", 3.0))
    except (TypeError, ValueError):
        delay_seconds = 3.0
    settings: dict[str, Any] = {
        "module": modules[0] if modules else None,
        "delay_seconds": min(600.0, max(0.0, delay_seconds)),
    }
    if include_modules:
        settings["modules"] = modules[:10]
    return settings


def save_launcher_autoplay_settings(
    module_names: list[str] | str | None,
    delay_seconds: float,
    path: Path = LAUNCHER_AUTOPLAY_SETTINGS_PATH,
) -> None:
    if isinstance(module_names, str):
        normalized_modules = [module_names.strip()] if module_names.strip() else []
    else:
        normalized_modules = [
            str(name).strip()
            for name in (module_names or [])
            if str(name).strip()
        ][:10]
    payload = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "module": normalized_modules[0] if normalized_modules else None,
        "modules": normalized_modules,
        "delay_seconds": min(600.0, max(0.0, float(delay_seconds))),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(path)


def load_launcher_autoplay_expected_client_size(
    path: Path = WINDOW_LAYOUT_CONFIG_PATH,
) -> tuple[int, int]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        expected = payload.get("expected_client") or {}
        width = int(expected.get("width") or 0)
        height = int(expected.get("height") or 0)
        if width > 0 and height > 0:
            return width, height
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return AUTOPLAY_DEFAULT_CLIENT_SIZE


def advance_launcher_autoplay_slot_readiness(
    target: dict[str, Any] | None,
    state: dict[str, Any],
    *,
    now: float,
    delay_seconds: float,
    expected_client_size: tuple[int, int],
    stable_checks_required: int = AUTOPLAY_READY_STABLE_CHECKS,
) -> tuple[bool, str]:
    expected_width, expected_height = expected_client_size

    def reset(phase: str, reason: str) -> tuple[bool, str]:
        state.update(
            {
                "phase": phase,
                "signature": None,
                "stable_checks": 0,
                "delay_started_at": None,
                "reason": reason,
            }
        )
        return False, reason

    if not target:
        return reset("waiting-window", "window not ready")

    width = int(target.get("width") or 0)
    height = int(target.get("height") or 0)
    if (
        abs(width - expected_width) > AUTOPLAY_CLIENT_SIZE_TOLERANCE
        or abs(height - expected_height) > AUTOPLAY_CLIENT_SIZE_TOLERANCE
    ):
        return reset(
            "waiting-size",
            f"client {width}x{height}, expected {expected_width}x{expected_height}",
        )

    signature = (
        int(target.get("pid") or 0),
        int(target.get("hwnd") or 0),
        width,
        height,
    )
    required = max(1, int(stable_checks_required))
    if state.get("signature") == signature:
        stable_checks = int(state.get("stable_checks") or 0) + 1
    else:
        stable_checks = 1
        state["delay_started_at"] = None
    state["signature"] = signature
    state["stable_checks"] = stable_checks

    if stable_checks < required:
        state["phase"] = "stabilizing"
        state["reason"] = f"stable check {stable_checks}/{required}"
        return False, str(state["reason"])

    delay_started_at = state.get("delay_started_at")
    if delay_started_at is None:
        delay_started_at = float(now)
        state["delay_started_at"] = delay_started_at
    elapsed = max(0.0, float(now) - float(delay_started_at))
    remaining = max(0.0, float(delay_seconds) - elapsed)
    if remaining > 0:
        state["phase"] = "delay"
        state["reason"] = f"delay remaining {remaining:.1f}s"
        return False, str(state["reason"])

    state["phase"] = "ready"
    state["reason"] = "ready"
    return True, "ready"


def install_crash_logging() -> None:
    global _FAULT_LOG_FILE
    try:
        CRASH_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _FAULT_LOG_FILE = CRASH_LOG_PATH.open("a", encoding="utf-8")
        _FAULT_LOG_FILE.write(f"\n=== GUI_TEST_PC start {datetime.now().isoformat(timespec='seconds')} ===\n")
        _FAULT_LOG_FILE.flush()
        faulthandler.enable(file=_FAULT_LOG_FILE, all_threads=True)
    except Exception:
        pass

    def excepthook(exc_type: type[BaseException], exc: BaseException, tb: object) -> None:
        append_crash_log("".join(traceback.format_exception(exc_type, exc, tb)))
        sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = excepthook


class TaskWorker(QThread):
    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, func: Callable[[], Any], label: str = "") -> None:
        super().__init__()
        self.func = func
        self.label = label

    def run(self) -> None:
        try:
            self.finished_ok.emit(self.func())
        except Exception as exc:
            message = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            append_crash_log(f"\n--- worker failed: {self.label or 'task'} ---\n{message}")
            self.failed.emit(message.strip() or str(exc))


def safe_file_stem(text: str) -> str:
    text = text.strip()
    if not text:
        return datetime.now().strftime("recording_%Y%m%d_%H%M%S")
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text)
    text = re.sub(r"\s+", "_", text)
    return text[:80] or datetime.now().strftime("recording_%Y%m%d_%H%M%S")


def script_display_name(path: Path) -> str:
    return path.name.removesuffix(".pcscript.json")


def list_pc_scripts() -> list[Path]:
    ensure_dirs()
    return sorted(SCRIPT_DIR.glob("*.pcscript.json"), key=lambda p: p.name.casefold())


def load_script_summary(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return {"name": path.name, "error": str(exc)}
    size = data.get("client_size") or {}
    return {
        "name": script_display_name(path),
        "path": str(path),
        "format": data.get("format", ""),
        "events": len(data.get("events") or []),
        "client_size": f"{size.get('w', '?')}x{size.get('h', '?')}",
        "duration_ms": data.get("duration_ms", ""),
        "target_slot": (data.get("target") or {}).get("slot", ""),
        "created_at": data.get("created_at", ""),
    }


def load_modules() -> dict[str, list[str]]:
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


def save_modules(modules: dict[str, list[str]]) -> None:
    ensure_dirs()
    payload = {
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "modules": modules,
    }
    MODULES_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8-sig",
    )


def load_module_group_settings() -> tuple[list[str], dict[str, str]]:
    if not MODULE_GROUPS_PATH.exists():
        return [], {}
    try:
        payload = json.loads(MODULE_GROUPS_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, TypeError):
        return [], {}
    if not isinstance(payload, dict):
        return [], {}

    groups: list[str] = []
    seen: set[str] = set()
    raw_groups = payload.get("groups")
    if isinstance(raw_groups, list):
        for value in raw_groups:
            name = str(value).strip()
            if name and name != "未分組" and name not in seen:
                groups.append(name)
                seen.add(name)

    assignments: dict[str, str] = {}
    raw_assignments = payload.get("assignments")
    if isinstance(raw_assignments, dict):
        for module_name, value in raw_assignments.items():
            module = str(module_name).strip()
            group = str(value).strip()
            if not module or not group or group == "未分組":
                continue
            assignments[module] = group
            if group not in seen:
                groups.append(group)
                seen.add(group)
    return groups, assignments


def save_module_group_settings(groups: list[str], assignments: dict[str, str]) -> None:
    known_modules = set(load_modules())
    normalized_groups: list[str] = []
    seen: set[str] = set()
    for value in [*groups, *assignments.values()]:
        name = str(value).strip()
        if name and name != "未分組" and name not in seen:
            normalized_groups.append(name)
            seen.add(name)
    normalized_assignments = {
        str(module): str(group).strip()
        for module, group in assignments.items()
        if str(module) in known_modules
        and str(group).strip()
        and str(group).strip() != "未分組"
    }
    payload = {
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "groups": normalized_groups,
        "assignments": normalized_assignments,
    }
    MODULE_GROUPS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = MODULE_GROUPS_PATH.with_suffix(MODULE_GROUPS_PATH.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(MODULE_GROUPS_PATH)


def update_module_group_membership(
    groups: list[str],
    assignments: dict[str, str],
    group_name: str,
    checked_modules: set[str],
    module_names: list[str],
) -> tuple[list[str], dict[str, str]]:
    group_name = group_name.strip() or "未分組"
    known_modules = set(module_names)
    checked = {name for name in checked_modules if name in known_modules}
    updated_groups = list(groups)
    updated_assignments = {
        module_name: assigned_group
        for module_name, assigned_group in assignments.items()
        if module_name in known_modules
    }

    if group_name == "未分組":
        for module_name in checked:
            updated_assignments.pop(module_name, None)
        return updated_groups, updated_assignments

    if group_name not in updated_groups:
        updated_groups.append(group_name)
    for module_name in module_names:
        if module_name in checked:
            updated_assignments[module_name] = group_name
        elif updated_assignments.get(module_name) == group_name:
            updated_assignments.pop(module_name, None)
    return updated_groups, updated_assignments


def grouped_module_names(module_names: list[str]) -> list[tuple[str, list[str]]]:
    groups, assignments = load_module_group_settings()
    buckets: dict[str, list[str]] = {name: [] for name in groups}
    ungrouped: list[str] = []
    for module_name in module_names:
        group = assignments.get(module_name)
        if group and group in buckets:
            buckets[group].append(module_name)
        else:
            ungrouped.append(module_name)
    result = [(name, buckets[name]) for name in groups if buckets[name]]
    if ungrouped:
        result.append(("未分組", ungrouped))
    return result


def module_script_paths(module_name: str | None) -> list[Path]:
    if not module_name:
        return []
    modules = load_modules()
    paths: list[Path] = []
    for name in modules.get(str(module_name), []):
        path = Path(name)
        if not path.is_absolute():
            path = SCRIPT_DIR / name
        if path.exists():
            paths.append(path)
    return paths


def battle_interrupt_plan_error(
    plans_by_slot: dict[int, list[dict[str, Any]]],
    slots: list[int],
) -> str | None:
    from starcg_bot.battle_interrupt_runtime import (
        is_battle_interrupt_descriptor,
        load_battle_interrupt_descriptor,
    )

    descriptor_steps: list[tuple[int, int, Path, str, set[int]]] = []
    for slot, plan in plans_by_slot.items():
        for index, step in enumerate(plan):
            path = Path(step["script_path"])
            if is_battle_interrupt_descriptor(path):
                payload = load_battle_interrupt_descriptor(path)
                mode = str(payload["mode"])
                allowed_slots = {int(value) for value in payload["allowed_slots"]}
                descriptor_steps.append((int(slot), index, path, mode, allowed_slots))
    if not descriptor_steps:
        return None
    invalid_slots = sorted({int(slot) for slot in slots if int(slot) < 1 or int(slot) > MAX_SLOT})
    if invalid_slots:
        return f"中斷戰鬥只支援 SLOT 1-{MAX_SLOT}：{invalid_slots}"
    for slot, index, path, mode, allowed_slots in descriptor_steps:
        if slot not in allowed_slots:
            return f"{path.name} 不允許 SLOT {slot}。"
        if mode == "dry_run" and index != len(plans_by_slot[slot]) - 1:
            return f"Dry-run 必須是模組鏈最後一步，避免未實際取消戰鬥後繼續執行：{path.name}"
    return None


def run_battle_interrupt_in_playback_turn(
    playback_coordinator: Any,
    playback_handle: int,
    stop_requested: Callable[[], bool],
    operation: Callable[[], dict[str, Any]],
) -> dict[str, Any] | None:
    """Start battle vision only after this slot owns the shared Pico turn."""
    requested_at = time.monotonic()
    delay = playback_coordinator.wait_for_turn(
        int(playback_handle),
        requested_at,
        stop_requested=stop_requested,
    )
    if delay is None:
        return None
    try:
        result = dict(operation())
        result["scheduler_delay_ms"] = round(max(0.0, float(delay)) * 1000.0)
        return result
    finally:
        playback_coordinator.release_turn(int(playback_handle))


def set_windows_app_id() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("GUI_TEST_PC.StarCG.Native")
    except Exception:
        pass


def build_app_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor("#0f172a"))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor("#14b8a6"))
    painter.setPen(QColor("#0f766e"))
    painter.drawRoundedRect(6, 6, 52, 52, 14, 14)
    painter.setPen(QColor("#ffffff"))
    font = QFont("Segoe UI", 17, QFont.Bold)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "PC")
    painter.end()
    return QIcon(pixmap)


def build_record_control_icon(kind: str, color: str) -> QIcon:
    pixmap = QPixmap(28, 28)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color))
    if kind == "record":
        painter.drawEllipse(5, 5, 18, 18)
    elif kind == "stop":
        painter.drawRoundedRect(6, 6, 16, 16, 2, 2)
    else:
        painter.end()
        raise ValueError(f"unsupported recording control icon: {kind}")
    painter.end()
    return QIcon(pixmap)


def save_window_measurements(slot_rows: list[dict[str, Any]]) -> dict[str, Any]:
    ensure_dirs()
    measured_at = datetime.now().astimezone().isoformat(timespec="seconds")
    measurements: list[dict[str, Any]] = []
    for row in slot_rows:
        target = row.get("target")
        if not target:
            continue
        width = int(target.get("width") or 0)
        height = int(target.get("height") or 0)
        rect = target.get("rect") or []
        client_rect = target.get("client_rect") or []
        window_size = {}
        if len(rect) == 4:
            window_size = {"w": int(rect[2]) - int(rect[0]), "h": int(rect[3]) - int(rect[1])}
        measurements.append(
            {
                "slot": int(row["slot"]),
                "measured_at": measured_at,
                "hwnd": int(target.get("hwnd") or 0),
                "hwnd_hex": f"0x{int(target.get('hwnd') or 0):X}",
                "pid": int(target.get("pid") or 0),
                "title": target.get("title") or "",
                "process_path": target.get("process_path") or "",
                "slot_source": target.get("slot_source") or "",
                "client_size": {"w": width, "h": height},
                "client_rect_screen": client_rect,
                "window_rect_screen": rect,
                "window_size": window_size,
                "measurement_method": "Win32 GetClientRect + ClientToScreen + GetWindowRect",
                "coordinate_space": "client pixels",
            }
        )
    payload = {
        "updated_at": measured_at,
        "source": "GUI_TEST_PC .exe window measurement; not shared with old GUI_TEST",
        "script_dir": str(SCRIPT_DIR),
        "module_path": str(MODULES_PATH),
        "measurements": measurements,
    }
    MEASUREMENTS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8-sig")
    return payload


class GuiTestPcMainWindow(QMainWindow):
    recorder_status_changed = pyqtSignal(str)
    playback_slot_finished = pyqtSignal(int, str, str)
    playback_slot_progress = pyqtSignal(int, str)
    playback_slot_module = pyqtSignal(int, str)
    live_touch_event = pyqtSignal(str)
    bridge_io_ready = pyqtSignal(object)

    def __init__(self) -> None:
        super().__init__()
        self.recorder_status_changed.connect(self._set_recorder_status)
        self.playback_slot_finished.connect(self._finish_slot_playback)
        self.playback_slot_progress.connect(self._set_slot_playback_progress)
        self.playback_slot_module.connect(self._set_slot_current_module)
        self.live_touch_event.connect(self.log)
        self.bridge_io_ready.connect(self._on_bridge_io_ready)
        ensure_dirs()
        self.setWindowTitle("GUI_TEST_PC")
        self.resize(1830, 1230)
        self.setMinimumSize(1500, 1000)
        self._workers: list[TaskWorker] = []
        self._scheduler_worker: TaskWorker | None = None
        self._record_stop_event: threading.Event | None = None
        self._record_started_at: float | None = None
        self._record_ui_state = "idle"
        self._launcher_busy = False
        self._launcher_action: str | None = None
        self._launcher_slots: set[int] = set()
        self._window_layout_busy = False
        self._launcher_autoplay_generation = 0
        autoplay_settings = load_launcher_autoplay_settings(include_modules=True)
        self._launcher_autoplay_modules: list[str] = list(autoplay_settings["modules"])
        self._launcher_autoplay_delay_seconds = float(autoplay_settings["delay_seconds"])
        self._launcher_autoplay_expected_client_size = load_launcher_autoplay_expected_client_size()
        self.launcher_action_buttons: dict[str, QPushButton] = {}
        self.launcher_autoplay_module_buttons: dict[str, QPushButton] = {}
        self.launcher_autoplay_module_buttons_layout: QGridLayout | None = None
        self.launcher_autoplay_step_combos: list[QComboBox] = []
        self.window_layout_buttons: list[QPushButton] = []
        self.slot_rows: list[dict[str, Any]] = []
        self.slot_buttons: dict[int, QPushButton] = {}
        self.slot_status_labels: dict[int, QLabel] = {}
        self.slot_indicator_groups: list[dict[int, QLabel]] = []
        self._slot_selection_order: list[int] = []
        self._playback_coordinator = CooperativePlaybackCoordinator(
            group_size=PLAYBACK_GROUP_SIZE,
            long_wait_seconds=PLAYBACK_LONG_WAIT_SECONDS,
        )
        self._slot_playback_runs: dict[int, dict[str, Any]] = {}
        self._playing_slots: set[int] = set()
        self._queued_playback_commands: dict[str, set[int]] = {}
        self._prepared_replacement_commands: set[str] = set()
        self._slot_window_misses: dict[int, int] = {}
        self._gui_command_bridge = GuiCommandBridge(GUI_COMMAND_BRIDGE_ROOT)
        self._playback_automations = PlaybackAutomationStore(PLAYBACK_AUTOMATION_PATH)
        self._startup_expired_automations = self._playback_automations.expire_overdue_scheduled(
            grace_seconds=120.0
        )
        self._bridge_io_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="gui-test-pc-bridge-io",
        )
        self._bridge_io_future: Future[object] | None = None
        self._gui_log_entries: deque[tuple[datetime, str]] = deque(maxlen=20000)
        self._live_touch_server: GuiTestPcLiveTouchServer | None = None
        self._pwa_playback_outcomes: dict[str, dict[int, str]] = {}
        self.chain_step_combos: list[QComboBox] = []
        self.chain_module_buttons: list[QPushButton] = []
        self.chain_module_buttons_layout: QGridLayout | None = None

        self._build_ui()
        self._apply_style()
        self.refresh_all_local()
        self._compact_all_buttons()
        self._start_live_touch_server()
        for expired in self._startup_expired_automations:
            self.log(f"過期定時播放未補播: {expired.get('id')} slots={expired.get('slots')}")
        self._bridge_io_executor.submit(prune_activity_log)

        self.record_elapsed_timer = QTimer(self)
        self.record_elapsed_timer.timeout.connect(self._update_recording_elapsed)
        self.record_elapsed_timer.start(250)

        self.scheduler_timer = QTimer(self)
        self.scheduler_timer.timeout.connect(self._scheduler_tick)
        self.scheduler_timer.start(5000)

        self.playback_automation_timer = QTimer(self)
        self.playback_automation_timer.timeout.connect(self._playback_automation_tick)
        self.playback_automation_timer.start(1000)

        self.window_status_timer = QTimer(self)
        self.window_status_timer.timeout.connect(self.refresh_windows)
        self.window_status_timer.start(3000)

        self.pwa_bridge_timer = QTimer(self)
        self.pwa_bridge_timer.timeout.connect(self._pwa_bridge_tick)
        self.pwa_bridge_timer.start(500)
        self._pwa_bridge_tick()

        self.log_prune_timer = QTimer(self)
        self.log_prune_timer.timeout.connect(self._prune_gui_log)
        self.log_prune_timer.start(60_000)

        self.activity_log_prune_timer = QTimer(self)
        self.activity_log_prune_timer.timeout.connect(
            lambda: self._bridge_io_executor.submit(prune_activity_log)
        )
        self.activity_log_prune_timer.start(60 * 60 * 1000)

    def _pwa_bridge_tick(self) -> None:
        if self._bridge_io_future is not None and not self._bridge_io_future.done():
            return
        try:
            state = self._collect_bridge_heartbeat_state()
            future = self._bridge_io_executor.submit(self._bridge_io_cycle, state)
            self._bridge_io_future = future

            def completed(done: Future[object]) -> None:
                try:
                    self.bridge_io_ready.emit({"commands": done.result(), "error": None})
                except Exception as exc:
                    self.bridge_io_ready.emit({"commands": [], "error": str(exc)})

            future.add_done_callback(completed)
        except Exception as exc:
            self.log(f"PWA bridge submit error: {exc}")

    def _collect_bridge_heartbeat_state(self) -> dict[str, Any]:
        running_slots = sorted(
            int(row.get("slot"))
            for row in self.slot_rows
            if row.get("slot") and row.get("running")
        )
        automation_summaries = [
            {key: value for key, value in job.items() if key != "last_result"}
            for job in self._playback_automations.active_jobs()
        ]
        live_touch_health = (
            self._live_touch_server.health()
            if self._live_touch_server is not None
            else {"enabled": False}
        )
        pico_activity = live_touch_health.get("pico_activity")
        pico_activity = pico_activity if isinstance(pico_activity, dict) else {}
        queued_playback_slots = sorted(
            {
                slot
                for slots in self._queued_playback_commands.values()
                for slot in slots
            }
        )
        slot_playback_status = {
            str(slot): str(run.get("progress") or "播放中")
            for slot, run in sorted(self._slot_playback_runs.items())
            if slot in self._playing_slots
        }
        for slot in queued_playback_slots:
            slot_playback_status.setdefault(str(slot), "等待取代舊播放")
        return {
            "online": True,
            "pid": os.getpid(),
            "window_title": self.windowTitle(),
            "running_slots": running_slots,
            "playing_slots": sorted(self._playing_slots),
            "queued_playback_slots": queued_playback_slots,
            "slot_playback_status": slot_playback_status,
            "slot_current_module": {
                str(slot): str(run.get("current_module") or run.get("first_module") or "")
                for slot, run in sorted(self._slot_playback_runs.items())
                if slot in self._playing_slots
            },
            "pico_activity_slot": pico_activity.get("slot"),
            "launcher_busy": bool(self._launcher_busy),
            "launcher_action": self._launcher_action,
            "launcher_slots": sorted(self._launcher_slots),
            "playback_automations": automation_summaries,
            "shutdown_requested": False,
            "execution_owner": "GUI_TEST_PC",
            "live_touch": live_touch_health,
        }

    def _bridge_io_cycle(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        self._gui_command_bridge.expire_stale()
        self._gui_command_bridge.write_heartbeat(state)
        return self._gui_command_bridge.queued_commands(limit=20)

    def _on_bridge_io_ready(self, payload: object) -> None:
        self._bridge_io_future = None
        result = payload if isinstance(payload, dict) else {}
        error = result.get("error")
        if error:
            self.log(f"PWA bridge I/O error: {error}")
            return
        commands = result.get("commands")
        if not isinstance(commands, list):
            return
        playback_actions = {"play_module_chain", "play_script", "create_playback_automation"}
        self._queued_playback_commands = {
            str(command.get("id") or ""): set(
                normalize_slots(
                    (command.get("payload") or {}).get("slots")
                    if isinstance(command.get("payload"), dict)
                    else []
                )
            )
            for command in commands
            if isinstance(command, dict)
            and str(command.get("action") or "") in playback_actions
            and str(command.get("id") or "")
        }
        self._prepared_replacement_commands.intersection_update(self._queued_playback_commands)
        for command in commands:
            if not isinstance(command, dict):
                continue
            action = str(command.get("action") or "")
            if action == "launcher_action" and self._launcher_busy:
                continue
            if action == "window_layout" and self._window_layout_busy:
                continue
            payload_data = command.get("payload") if isinstance(command.get("payload"), dict) else {}
            requested_slots = normalize_slots(payload_data.get("slots"))
            command_id = str(command.get("id") or "")
            if action in playback_actions and payload_data.get("replace_existing"):
                self._prepare_latest_playback_command(command_id, requested_slots)
                busy_slots = [slot for slot in requested_slots if slot in self._slot_playback_runs]
                if busy_slots:
                    self.log(
                        f"PWA latest-wins waiting for old playback to release: "
                        f"id={command_id} busy={busy_slots}"
                    )
                    for slot in busy_slots:
                        self._gui_command_bridge.update(
                            command_id,
                            slot=slot,
                            slot_status="已停止舊播放，等待 Pico 釋放",
                        )
                    continue
            self._execute_pwa_command(command)
            self._prepared_replacement_commands.discard(command_id)
            break

    def _prepare_latest_playback_command(self, command_id: str, slots: list[int]) -> None:
        if not command_id or command_id in self._prepared_replacement_commands:
            return
        cancelled_jobs: set[str] = set()
        stopping_slots: list[int] = []
        for slot in slots:
            for job in self._active_automation_jobs_for_slot(slot):
                job_id = str(job.get("id") or "")
                if job_id and job_id not in cancelled_jobs:
                    self._playback_automations.cancel(job_id)
                    cancelled_jobs.add(job_id)
            run = self._slot_playback_runs.get(slot)
            if run is not None:
                run["cancel"].set()
                stopping_slots.append(slot)
        self._prepared_replacement_commands.add(command_id)
        self.log(
            f"PWA latest-wins prepared: id={command_id} slots={slots} "
            f"stopping={stopping_slots} cancelled_jobs={sorted(cancelled_jobs)}"
        )

    def _execute_pwa_command(self, command: dict[str, Any]) -> None:
        command_id = str(command.get("id") or "")
        action = str(command.get("action") or "")
        payload = command.get("payload") if isinstance(command.get("payload"), dict) else {}
        slots = normalize_slots(payload.get("slots"))
        self._gui_command_bridge.update(command_id, status="running")
        self.log(f"PWA -> GUI_TEST_PC command: id={command_id} action={action} slots={slots}")
        try:
            if action == "play_module_chain":
                modules = [str(name).strip() for name in payload.get("modules", []) if str(name).strip()]
                if not slots or not modules:
                    raise ValueError("play_module_chain requires slots and modules")
                self._play_module_chain(
                    modules,
                    slots,
                    f"PWA 模組連串 {' > '.join(modules)}",
                    show_warnings=False,
                    bridge_command_id=command_id,
                )
                if not any(run.get("bridge_command_id") == command_id for run in self._slot_playback_runs.values()):
                    raise RuntimeError("GUI_TEST_PC could not start the requested module chain")
                return
            if action == "play_script":
                script = script_path_from_name(str(payload.get("script") or ""))
                if not slots:
                    raise ValueError("play_script requires slots")
                self._play_scripts_to_slots(
                    [script],
                    slots,
                    f"PWA 腳本 {script.name}",
                    show_warnings=False,
                    bridge_command_id=command_id,
                )
                if not any(run.get("bridge_command_id") == command_id for run in self._slot_playback_runs.values()):
                    raise RuntimeError("GUI_TEST_PC could not start the requested script")
                return
            if action == "create_playback_automation":
                jobs = self._create_playback_automations(payload, source="pwa")
                self._gui_command_bridge.update(
                    command_id,
                    status="completed",
                    result={
                        "automations": jobs,
                        "automation": jobs[0] if len(jobs) == 1 else None,
                    },
                )
                return
            if action == "cancel_playback_automation":
                job_id = str(payload.get("id") or payload.get("job_id") or "").strip()
                if not job_id:
                    raise ValueError("cancel_playback_automation requires id")
                job = self.cancel_playback_automation(job_id)
                if job is None:
                    raise ValueError(f"unknown playback automation: {job_id}")
                self._gui_command_bridge.update(
                    command_id,
                    status="completed",
                    result={"automation": job},
                )
                return
            if action == "stop_all_playback":
                active = sorted(self._slot_playback_runs)
                self.stop_all_playback()
                self._gui_command_bridge.update(
                    command_id,
                    status="completed",
                    result={"stopping_slots": active},
                )
                return
            if action == "stop_slot_playback":
                if len(slots) != 1:
                    raise ValueError("stop_slot_playback requires exactly one slot")
                slot = slots[0]
                stopped = self.stop_slot_playback(slot)
                self._gui_command_bridge.update(
                    command_id,
                    status="completed",
                    result={"slot": slot, "stopped": stopped},
                    slot=slot,
                    slot_status="已要求中止" if stopped else "沒有播放中的模組",
                )
                return
            if action == "launcher_action":
                launcher_action = str(payload.get("action") or "")
                if not slots:
                    slots = list(range(1, MAX_SLOT + 1))
                self.launcher_slots_edit.setText(",".join(str(slot) for slot in slots))
                forcebind_mode = str(payload.get("forcebind_mode") or "netbind")
                combo_index = self.launcher_forcebind_combo.findData(forcebind_mode)
                if combo_index >= 0:
                    self.launcher_forcebind_combo.setCurrentIndex(combo_index)
                self.launcher_windows_users_cb.setChecked(bool(payload.get("use_windows_users", True)))
                self.launcher_action(launcher_action, quiet=True)
                self._gui_command_bridge.update(
                    command_id,
                    status="completed",
                    result={"dispatched": True, "action": launcher_action},
                )
                return
            if action == "window_layout":
                if not slots:
                    slots = list(range(1, MAX_SLOT + 1))
                self.launcher_slots_edit.setText(",".join(str(slot) for slot in slots))
                layout_action = str(payload.get("action") or "ensure")
                self.window_layout_action(layout_action)
                self._gui_command_bridge.update(
                    command_id,
                    status="completed",
                    result={"dispatched": True, "action": layout_action},
                )
                return
            raise ValueError(f"unsupported PWA bridge action: {action}")
        except Exception as exc:
            self._gui_command_bridge.update(command_id, status="failed", error=str(exc))
            self.log(f"PWA command failed: id={command_id} error={exc}")

    def _build_ui(self) -> None:
        root = QWidget()
        main = QVBoxLayout(root)
        main.setContentsMargins(15, 15, 15, 15)
        main.setSpacing(12)
        self.setCentralWidget(root)

        header = QHBoxLayout()
        title_block = QVBoxLayout()
        title = QLabel("GUI_TEST_PC")
        title.setObjectName("Title")
        subtitle = QLabel("Python 桌面版。串流交由 oplink_pc；本工具負責啟動器、腳本、模組、定時與日誌。")
        subtitle.setObjectName("Subtitle")
        title_block.addWidget(title)
        title_block.addWidget(subtitle)
        header.addLayout(title_block, 1)

        self.connection_label = QLabel("尚未掃描")
        self.connection_label.setObjectName("StatusPill")
        header.addWidget(self.connection_label)
        self.btn_refresh_all = QPushButton("刷新全部")
        self.btn_refresh_all.clicked.connect(self.refresh_all_local)
        header.addWidget(self.btn_refresh_all)
        main.addLayout(header)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._create_tab_script_chain(), "模組連串")
        self.tabs.addTab(self._create_tab_script_mgmt(), "腳本管理")
        self.tabs.addTab(self._create_tab_sync(), "同步器")
        self.tabs.addTab(self._create_tab_launcher(), "啟動")
        self.tabs.addTab(self._create_tab_log(), "日誌")
        self.tabs.setCurrentIndex(3)
        main.addWidget(self.tabs, 1)

    def closeEvent(self, event: object) -> None:
        running_workers = [worker for worker in self._workers if worker.isRunning()]
        if self._scheduler_worker is not None and self._scheduler_worker.isRunning():
            running_workers.append(self._scheduler_worker)
        if running_workers:
            QMessageBox.warning(
                self,
                "背景工作仍在執行",
                "GUI_TEST_PC 正在執行啟動器/狀態刷新/排程檢查。\n"
                f"為避免中斷 {MAX_SLOT} 開流程，請等背景工作完成後再關閉。",
            )
            event.ignore()
            return
        self.scheduler_timer.stop()
        self.playback_automation_timer.stop()
        self.window_status_timer.stop()
        self.pwa_bridge_timer.stop()
        self.record_elapsed_timer.stop()
        self.log_prune_timer.stop()
        self.activity_log_prune_timer.stop()
        try:
            self._gui_command_bridge.write_heartbeat(
                {
                    "online": False,
                    "pid": os.getpid(),
                    "shutdown_requested": True,
                    "execution_owner": "GUI_TEST_PC",
                }
            )
        except Exception:
            pass
        self._bridge_io_executor.shutdown(wait=False, cancel_futures=True)
        if self._live_touch_server is not None:
            self._live_touch_server.stop()
            self._live_touch_server = None
        super().closeEvent(event)

    def _start_live_touch_server(self) -> None:
        port = int(os.environ.get("GUI_TEST_PC_LIVE_TOUCH_PORT", "5111"))
        try:
            server = GuiTestPcLiveTouchServer(
                PICO_TOUCH_CONFIG_PATH,
                self._live_touch_target_for_slot,
                port=port,
                event_logger=self.live_touch_event.emit,
            )
            server.start()
        except Exception as exc:
            self.log(f"GUI_TEST_PC live touch bridge unavailable on 127.0.0.1:{port}: {exc}")
            return
        self._live_touch_server = server
        self.log(f"GUI_TEST_PC live touch bridge ready: {server.address}")

    def _live_touch_target_for_slot(self, slot: int) -> dict[str, Any] | None:
        target = self.validated_target_for_slot(int(slot))
        return dict(target) if target is not None else None

    def _create_slot_grid(self) -> QWidget:
        wrapper = QWidget()
        wrapper.setObjectName("CompactSlotGrid")
        layout = QGridLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        for slot in range(1, MAX_SLOT + 1):
            tile = QWidget()
            tile.setObjectName("SlotTile")
            tile.setMinimumWidth(72)
            tile.setMaximumWidth(108)
            tile_layout = QVBoxLayout(tile)
            tile_layout.setContentsMargins(3, 3, 3, 3)
            tile_layout.setSpacing(2)

            btn = QPushButton(str(slot))
            btn.setCheckable(True)
            btn.setProperty("compact", True)
            btn.setFixedHeight(28)
            btn.clicked.connect(lambda checked=False, s=slot: self._handle_slot_button_clicked(s, checked))
            self.slot_buttons[slot] = btn

            label = QLabel("未掃描")
            label.setWordWrap(True)
            label.setAlignment(Qt.AlignCenter)
            label.setFixedHeight(28)
            self.slot_status_labels[slot] = label

            tile_layout.addWidget(btn)
            tile_layout.addWidget(label)
            layout.addWidget(tile, 0, slot - 1)
            layout.setColumnStretch(slot - 1, 1)
        return wrapper

    def _create_slot_indicator_strip(self) -> QWidget:
        wrapper = QWidget()
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        indicators: dict[int, QLabel] = {}
        for slot in range(1, MAX_SLOT + 1):
            cell = QWidget()
            cell_layout = QVBoxLayout(cell)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            cell_layout.setSpacing(2)
            dot = QLabel()
            dot.setObjectName("SlotIndicator")
            dot.setFixedSize(13, 13)
            dot.setToolTip(f"GAME {slot:02d}: 未掃描")
            num = QLabel(str(slot))
            num.setAlignment(Qt.AlignCenter)
            num.setObjectName("SlotIndicatorNumber")
            cell_layout.addWidget(dot, 0, Qt.AlignCenter)
            cell_layout.addWidget(num)
            indicators[slot] = dot
            layout.addWidget(cell)
        layout.addStretch(1)
        self.slot_indicator_groups.append(indicators)
        return wrapper

    def _create_tab_script_chain(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(5)

        settings_group = QGroupBox("播放設定")
        settings_group.setProperty("compact", True)
        settings_layout = QHBoxLayout(settings_group)
        settings_layout.setContentsMargins(6, 6, 6, 4)
        settings_layout.setSpacing(5)
        settings_layout.addWidget(QLabel("起始錯峰:"))
        self.chain_slot_stagger_spin = QDoubleSpinBox()
        self.chain_slot_stagger_spin.setProperty("compact", True)
        self.chain_slot_stagger_spin.setRange(0.0, 0.0)
        self.chain_slot_stagger_spin.setSingleStep(0.0)
        self.chain_slot_stagger_spin.setSuffix("s")
        self.chain_slot_stagger_spin.setValue(0.0)
        self.chain_slot_stagger_spin.setEnabled(False)
        self.chain_slot_stagger_spin.setToolTip(
            "固定 0 秒。實際動作仍嚴格依腳本錄製時間，不會提早執行。"
        )
        settings_layout.addWidget(self.chain_slot_stagger_spin)
        lead_label = QLabel("每 5 SLOT 一組 / >10s 讓出 / 模組間 2s")
        lead_label.setObjectName("StatusPill")
        settings_layout.addWidget(lead_label)
        self.speed_spin = QDoubleSpinBox()
        self.speed_spin.setProperty("compact", True)
        self.speed_spin.setRange(PLAYBACK_SPEED, PLAYBACK_SPEED)
        self.speed_spin.setValue(PLAYBACK_SPEED)
        self.speed_spin.setEnabled(False)
        self.speed_spin.hide()
        self.btn_test_pico = QPushButton("測試 Pico 連線")
        self.btn_test_pico.setProperty("compact", True)
        self.btn_test_pico.setToolTip("只檢查 COM5、Pico 韌體與 HID 狀態，不會向 GAME 送出觸控。")
        self.btn_test_pico.clicked.connect(self.test_pico_connection)
        settings_layout.addWidget(self.btn_test_pico)
        self.allow_size_mismatch_cb = QCheckBox("允許尺寸不同並等比例換算")
        self.allow_size_mismatch_cb.setToolTip(
            "正常測試應保持 GAME 視窗尺寸不變；勾選後會將錄製的 client 座標等比例換算到目前 GAME client 區域。"
        )
        settings_layout.addWidget(self.allow_size_mismatch_cb)
        outer.addWidget(settings_group)

        windows_group = QGroupBox("GAME — 點選選取，再點模組立即播放")
        windows_group.setProperty("compact", True)
        windows_layout = QVBoxLayout(windows_group)
        windows_layout.setContentsMargins(6, 6, 6, 5)
        windows_layout.setSpacing(4)
        toolbar = QHBoxLayout()
        toolbar.setSpacing(4)
        self.selected_count_label = QLabel("已選 0 個 GAME")
        toolbar.addWidget(self.selected_count_label)
        btn_select_all = QPushButton("全選在線 GAME")
        btn_select_all.setProperty("compact", True)
        btn_select_all.clicked.connect(self.select_running_slots)
        btn_clear = QPushButton("清除選取")
        btn_clear.setProperty("compact", True)
        btn_clear.clicked.connect(self.clear_slot_selection)
        self.btn_stop_all_playback = QPushButton("中止全部播放")
        self.btn_stop_all_playback.setObjectName("DangerButton")
        self.btn_stop_all_playback.setProperty("compact", True)
        self.btn_stop_all_playback.setEnabled(False)
        self.btn_stop_all_playback.clicked.connect(self.stop_all_playback)
        self.btn_playback_automation = QPushButton("循環 / 定時播放")
        self.btn_playback_automation.setProperty("compact", True)
        self.btn_playback_automation.clicked.connect(self.open_playback_automation_dialog)
        btn_scan = QPushButton("刷新遊戲視窗狀態")
        btn_scan.setProperty("compact", True)
        btn_scan.clicked.connect(self.refresh_windows)
        btn_measure = QPushButton("量度尺寸")
        btn_measure.setProperty("compact", True)
        btn_measure.clicked.connect(self.measure_window_sizes)
        toolbar.addStretch(1)
        toolbar.addWidget(btn_select_all)
        toolbar.addWidget(btn_clear)
        toolbar.addWidget(self.btn_stop_all_playback)
        toolbar.addWidget(self.btn_playback_automation)
        toolbar.addWidget(btn_scan)
        toolbar.addWidget(btn_measure)
        windows_layout.addLayout(toolbar)
        windows_layout.addWidget(self._create_slot_grid())
        outer.addWidget(windows_group)

        custom_group = QGroupBox("模組連串 — 依順序選擇最多 10 個模組")
        custom_group.setProperty("compact", True)
        custom_layout = QGridLayout(custom_group)
        custom_layout.setContentsMargins(6, 6, 6, 5)
        custom_layout.setHorizontalSpacing(3)
        custom_layout.setVerticalSpacing(2)
        self.chain_script_combo = QComboBox()
        self.chain_script_combo.setParent(page)
        self.chain_script_combo.setVisible(False)
        self.btn_play_script = QPushButton("播放單腳本")
        self.btn_play_script.setParent(page)
        self.btn_play_script.clicked.connect(self.play_selected_script)
        self.btn_play_script.setVisible(False)
        for idx in range(10):
            combo = QComboBox()
            combo.setObjectName("ChainStepCombo")
            combo.setProperty("compact", True)
            combo.setMinimumWidth(78)
            combo.setFixedHeight(30)
            combo.setToolTip(f"模組連串第 {idx + 1} 步")
            self.chain_step_combos.append(combo)
            step_label = QLabel(str(idx + 1))
            step_label.setAlignment(Qt.AlignCenter)
            step_label.setObjectName("CompactStepNumber")
            custom_layout.addWidget(step_label, 0, idx)
            custom_layout.addWidget(combo, 1, idx)
        btn_clear_chain = QPushButton("清空")
        btn_clear_chain.setProperty("compact", True)
        btn_clear_chain.clicked.connect(self.clear_custom_chain)
        self.btn_play_custom_chain = QPushButton("播放模組連串")
        self.btn_play_custom_chain.setObjectName("PrimaryButton")
        self.btn_play_custom_chain.setProperty("compact", True)
        self.btn_play_custom_chain.clicked.connect(self.play_custom_chain)
        custom_layout.addWidget(btn_clear_chain, 2, 8)
        custom_layout.addWidget(self.btn_play_custom_chain, 2, 9)
        outer.addWidget(custom_group)

        preset_group = QGroupBox("模組 — 選擇名稱或按快捷按鈕播放")
        preset_group.setProperty("compact", True)
        preset_layout = QVBoxLayout(preset_group)
        preset_layout.setContentsMargins(6, 6, 6, 5)
        preset_layout.setSpacing(3)
        select_row = QHBoxLayout()
        select_row.setSpacing(4)
        select_row.addWidget(QLabel("模組:"))
        self.chain_module_combo = QComboBox()
        self.chain_module_combo.setProperty("compact", True)
        self.chain_module_combo.setMinimumWidth(220)
        select_row.addWidget(self.chain_module_combo, 1)
        self.btn_play_module = QPushButton("播放模組到選取 GAME")
        self.btn_play_module.setObjectName("PrimaryButton")
        self.btn_play_module.setProperty("compact", True)
        self.btn_play_module.clicked.connect(self.play_selected_module)
        select_row.addWidget(self.btn_play_module)
        preset_layout.addLayout(select_row)
        self.chain_module_buttons_layout = QGridLayout()
        self.chain_module_buttons_layout.setSpacing(3)
        preset_layout.addLayout(self.chain_module_buttons_layout)
        hint = QLabel(
            "模組內容在「腳本管理」頁編輯，至少需要 1 個有效腳本。選取多個 GAME 時，"
            "每個 GAME 只播放 1 個腳本，並按模組腳本順序循環分配；快槽開始下一模組最多領先 1 步。"
        )
        hint.setObjectName("Hint")
        hint.setWordWrap(False)
        preset_layout.addWidget(hint)
        outer.addWidget(preset_group)
        outer.addStretch(1)

        return page

    def _populate_step_combo(
        self,
        combo: QComboBox,
        scripts: list[tuple[str, str]],
        current_data: str | None = None,
    ) -> None:
        combo.clear()
        combo.addItem("(空)", "")
        for label, data in scripts:
            combo.addItem(label, data)
        if current_data:
            idx = combo.findData(current_data)
            if idx >= 0:
                combo.setCurrentIndex(idx)

    def rebuild_chain_module_buttons(self, module_names: list[str]) -> None:
        if self.chain_module_buttons_layout is None:
            return
        while self.chain_module_buttons_layout.count():
            item = self.chain_module_buttons_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.chain_module_buttons.clear()
        colors = ["#d1fae5", "#cffafe", "#fee2e2", "#ede9fe", "#dcfce7", "#e0e7ff", "#fef3c7", "#fce7f3"]
        row = 0
        color_index = 0
        for group_name, names in grouped_module_names(module_names):
            group_label = QLabel(group_name)
            group_label.setObjectName("Hint")
            group_label.setStyleSheet("font-weight: 700;")
            self.chain_module_buttons_layout.addWidget(group_label, row, 0, 1, 8)
            row += 1
            for idx, name in enumerate(names):
                script_count = len(module_script_paths(name))
                btn = QPushButton(name)
                btn.setObjectName("ModuleTile")
                btn.setProperty("compact", True)
                btn.setFixedHeight(30)
                btn.setStyleSheet(f"background:{colors[color_index % len(colors)]};")
                btn.setEnabled(script_count >= 1)
                btn.setToolTip(
                    f"{group_name} / {name}: {script_count} 個有效腳本"
                    if script_count >= 1
                    else f"{group_name} / {name}: 沒有有效腳本"
                )
                btn.clicked.connect(lambda _checked=False, n=name: self.play_module_by_name(n))
                self.chain_module_buttons.append(btn)
                self.chain_module_buttons_layout.addWidget(btn, row + idx // 8, idx % 8)
                color_index += 1
            row += (len(names) + 7) // 8
        if not module_names:
            empty = QLabel("尚未建立 PC 模組。請先到「腳本管理」新增模組並加入腳本。")
            empty.setObjectName("Hint")
            self.chain_module_buttons_layout.addWidget(empty, 0, 0)

    def rebuild_launcher_autoplay_module_buttons(self, module_names: list[str]) -> None:
        if not self.launcher_autoplay_step_combos:
            return
        valid_modules = [
            name for name in module_names
            if module_script_paths(name)
        ]
        valid_module_set = set(valid_modules)
        retained_modules = [
            name for name in self._launcher_autoplay_modules
            if name in valid_module_set
        ][:10]
        removed_modules = [
            name for name in self._launcher_autoplay_modules
            if name not in valid_module_set
        ]
        if removed_modules:
            self._launcher_autoplay_modules = retained_modules
            self._launcher_autoplay_generation += 1
            save_launcher_autoplay_settings(
                self._launcher_autoplay_modules,
                self._launcher_autoplay_delay_seconds,
            )
            self.log(
                "Auto-play modules removed because they are unavailable: "
                + ", ".join(removed_modules)
            )
        for index, combo in enumerate(self.launcher_autoplay_step_combos):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("(空)", "")
            for name in valid_modules:
                combo.addItem(name, name)
            if index < len(self._launcher_autoplay_modules):
                selected_index = combo.findData(self._launcher_autoplay_modules[index])
                if selected_index >= 0:
                    combo.setCurrentIndex(selected_index)
            combo.blockSignals(False)
        self._update_launcher_autoplay_ui()

    def clear_custom_chain(self) -> None:
        for combo in self.chain_step_combos:
            combo.setCurrentIndex(0)

    def selected_custom_chain_modules(self) -> list[str]:
        modules: list[str] = []
        for combo in self.chain_step_combos:
            data = combo.currentData()
            if data:
                modules.append(str(data))
        return modules

    def play_custom_chain(self) -> None:
        slots = self.selected_slots()
        if not slots:
            QMessageBox.information(self, "播放自選連串", "請先選擇 GAME。")
            return
        modules = self.selected_custom_chain_modules()
        if not modules:
            QMessageBox.information(self, "播放模組連串", "請先在步驟格選擇模組。")
            return
        self._play_module_chain(modules, slots, "播放 PC 模組連串")

    def play_module_by_name(self, module_name: str) -> None:
        idx = self.chain_module_combo.findData(module_name)
        if idx >= 0:
            self.chain_module_combo.setCurrentIndex(idx)
        slots = self.selected_slots()
        if not slots:
            QMessageBox.information(self, "播放模組", "請先選擇 GAME。")
            return
        if not module_script_paths(module_name):
            QMessageBox.information(self, "播放模組", f"模組 {module_name} 沒有有效腳本。")
            return
        self._play_module_chain([module_name], slots, f"播放模組 {module_name}")

    @staticmethod
    def _parse_automation_run_at(value: Any) -> float:
        if isinstance(value, (int, float)):
            return float(value)
        text = str(value or "").strip()
        if not text:
            raise ValueError("定時播放需要指定時間")
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("run_at 必須是 ISO 日期時間") from exc
        if parsed.tzinfo is None:
            parsed = parsed.astimezone()
        return parsed.timestamp()

    def _create_playback_automation(
        self,
        payload: dict[str, Any],
        *,
        source: str,
    ) -> dict[str, Any]:
        mode = str(payload.get("mode") or "").strip()
        target_kind = str(payload.get("target_kind") or "").strip()
        slots = normalize_slots(payload.get("slots"))
        modules = [
            str(name).strip()
            for name in payload.get("modules", [])
            if str(name).strip()
        ]
        script = str(payload.get("script") or "").strip()
        if target_kind == "module_chain":
            invalid = [name for name in modules if not module_script_paths(name)]
            if invalid:
                raise ValueError(f"以下模組不存在或沒有有效腳本: {invalid}")
        elif target_kind == "script":
            script = script_path_from_name(script).name
        run_at = None
        if mode == "scheduled_once":
            run_at = self._parse_automation_run_at(payload.get("run_at"))
        job = self._playback_automations.create_job(
            mode=mode,
            target_kind=target_kind,
            slots=slots,
            modules=modules,
            script=script,
            cooldown_seconds=float(payload.get("cooldown_seconds") or 0.0),
            repeat_count=payload.get("repeat_count"),
            run_at=run_at,
            source=source,
        )
        target_text = script if target_kind == "script" else " > ".join(modules)
        self.log(
            f"已建立播放工作 {job['id']}: mode={mode} slots={slots} target={target_text}"
        )
        QTimer.singleShot(0, self._playback_automation_tick)
        return job

    def _create_playback_automations(
        self,
        payload: dict[str, Any],
        *,
        source: str,
    ) -> list[dict[str, Any]]:
        slots = normalize_slots(payload.get("slots"))
        if not slots:
            raise ValueError("至少需要一個 SLOT")
        mode = str(payload.get("mode") or "").strip()
        jobs: list[dict[str, Any]] = []
        for slot in slots:
            for current in self._active_automation_jobs_for_slot(slot):
                current_id = str(current.get("id") or "")
                if current_id:
                    self.cancel_playback_automation(current_id)
            slot_payload = dict(payload)
            slot_payload["slots"] = [slot]
            jobs.append(self._create_playback_automation(slot_payload, source=source))
        return jobs

    def _active_automation_jobs_for_slot(
        self,
        slot: int,
        *,
        mode: str | None = None,
    ) -> list[dict[str, Any]]:
        wanted_slot = int(slot)
        return [
            job
            for job in self._playback_automations.active_jobs()
            if wanted_slot in normalize_slots(job.get("slots"))
            and (mode is None or str(job.get("mode") or "") == mode)
        ]

    def open_playback_automation_dialog(self) -> None:
        slots = self.selected_slots()
        if not slots:
            QMessageBox.information(self, "循環 / 定時播放", "請先選擇一個或多個 GAME。")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle("循環 / 定時播放")
        dialog.setMinimumSize(980, 620)
        layout = QVBoxLayout(dialog)
        slot_label = QLabel("套用 GAME: " + ", ".join(str(slot) for slot in slots))
        slot_label.setObjectName("StatusPill")
        layout.addWidget(slot_label)

        form = QGridLayout()
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(8)
        mode_combo = QComboBox()
        mode_combo.addItem("無限循環", "loop")
        mode_combo.addItem("定時執行一次", "scheduled_once")
        target_kind_combo = QComboBox()
        target_combo = QComboBox()
        target_combo.setMinimumWidth(360)
        chain_label = QLabel()
        chain_label.setWordWrap(True)
        chain_label.setObjectName("Hint")
        cooldown_spin = QDoubleSpinBox()
        cooldown_spin.setRange(0.0, 86400.0)
        cooldown_spin.setDecimals(1)
        cooldown_spin.setSuffix(" 秒")
        cooldown_spin.setValue(10.0)
        run_at_edit = QDateTimeEdit(QDateTime.currentDateTime().addSecs(60))
        run_at_edit.setCalendarPopup(True)
        run_at_edit.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        run_at_edit.setMinimumDateTime(QDateTime.currentDateTime())
        form.addWidget(QLabel("模式:"), 0, 0)
        form.addWidget(mode_combo, 0, 1)
        form.addWidget(QLabel("內容:"), 0, 2)
        form.addWidget(target_kind_combo, 0, 3)
        form.addWidget(QLabel("選擇:"), 1, 0)
        form.addWidget(target_combo, 1, 1, 1, 3)
        form.addWidget(chain_label, 2, 0, 1, 4)
        cooldown_label = QLabel("每輪冷卻:")
        run_at_label = QLabel("執行時間:")
        form.addWidget(cooldown_label, 3, 0)
        form.addWidget(cooldown_spin, 3, 1)
        form.addWidget(run_at_label, 3, 2)
        form.addWidget(run_at_edit, 3, 3)
        layout.addLayout(form)

        action_row = QHBoxLayout()
        create_button = QPushButton("建立播放工作")
        create_button.setObjectName("PrimaryButton")
        action_row.addWidget(create_button)
        action_row.addStretch(1)
        cancel_button = QPushButton("取消選取工作")
        cancel_button.setObjectName("DangerButton")
        action_row.addWidget(cancel_button)
        layout.addLayout(action_row)

        jobs_table = QTableWidget(0, 7)
        jobs_table.setHorizontalHeaderLabels(
            ["ID", "模式", "GAME", "內容", "下次執行", "次數", "狀態"]
        )
        jobs_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        jobs_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        jobs_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        layout.addWidget(jobs_table, 1)

        close_buttons = QDialogButtonBox(QDialogButtonBox.Close)
        close_buttons.rejected.connect(dialog.reject)
        layout.addWidget(close_buttons)

        module_names = [
            name
            for name in sorted(load_modules(), key=str.casefold)
            if module_script_paths(name)
        ]
        script_paths = list_pc_scripts()

        def refresh_target_options() -> None:
            current_mode = str(mode_combo.currentData())
            previous_kind = target_kind_combo.currentData()
            target_kind_combo.blockSignals(True)
            target_kind_combo.clear()
            if current_mode == "loop":
                target_kind_combo.addItem("單一模組", "single_module")
                target_kind_combo.addItem("目前模組連串", "module_chain")
            else:
                target_kind_combo.addItem("單一腳本", "script")
                target_kind_combo.addItem("目前模組連串", "module_chain")
            previous_index = target_kind_combo.findData(previous_kind)
            target_kind_combo.setCurrentIndex(previous_index if previous_index >= 0 else 0)
            target_kind_combo.blockSignals(False)
            refresh_target_value()
            is_loop = current_mode == "loop"
            cooldown_label.setVisible(is_loop)
            cooldown_spin.setVisible(is_loop)
            run_at_label.setVisible(not is_loop)
            run_at_edit.setVisible(not is_loop)

        def refresh_target_value() -> None:
            kind = str(target_kind_combo.currentData())
            target_combo.clear()
            if kind == "single_module":
                for name in module_names:
                    target_combo.addItem(name, name)
                target_combo.setVisible(True)
                chain_label.setVisible(False)
            elif kind == "script":
                for path in script_paths:
                    target_combo.addItem(script_display_name(path), path.name)
                target_combo.setVisible(True)
                chain_label.setVisible(False)
            else:
                modules = self.selected_custom_chain_modules()
                chain_label.setText(
                    "目前連串快照: " + (" > ".join(modules) if modules else "尚未選擇模組")
                )
                target_combo.setVisible(False)
                chain_label.setVisible(True)

        def format_job_target(job: dict[str, Any]) -> str:
            if job.get("target_kind") == "script":
                return str(job.get("script") or "")
            return " > ".join(str(name) for name in job.get("modules") or [])

        def refresh_jobs_table() -> None:
            jobs = list(reversed(self._playback_automations.list_jobs()))
            jobs_table.setRowCount(0)
            mode_texts = {"loop": "無限循環", "scheduled_once": "定時一次"}
            status_texts = {
                "waiting": "等待執行",
                "running": "播放中",
                "cooling": "冷卻中",
                "completed": "已完成",
                "cancelled": "已取消",
                "failed": "失敗",
            }
            for job in jobs:
                row = jobs_table.rowCount()
                jobs_table.insertRow(row)
                values = [
                    job.get("id", ""),
                    mode_texts.get(str(job.get("mode")), job.get("mode", "")),
                    ",".join(str(slot) for slot in job.get("slots") or []),
                    format_job_target(job),
                    job.get("next_run_at_iso", ""),
                    job.get("iteration", 0),
                    status_texts.get(str(job.get("status")), job.get("status", "")),
                ]
                for column, value in enumerate(values):
                    jobs_table.setItem(row, column, QTableWidgetItem(str(value or "")))
            jobs_table.resizeColumnsToContents()

        def create_job() -> None:
            kind = str(target_kind_combo.currentData())
            modules: list[str] = []
            script = ""
            if kind == "single_module":
                selected_module = str(target_combo.currentData() or "")
                if selected_module:
                    modules = [selected_module]
                target_kind = "module_chain"
            elif kind == "module_chain":
                modules = self.selected_custom_chain_modules()
                target_kind = "module_chain"
            else:
                script = str(target_combo.currentData() or "")
                target_kind = "script"
            payload: dict[str, Any] = {
                "mode": str(mode_combo.currentData()),
                "target_kind": target_kind,
                "slots": slots,
                "modules": modules,
                "script": script,
                "cooldown_seconds": cooldown_spin.value(),
            }
            if payload["mode"] == "scheduled_once":
                payload["run_at"] = run_at_edit.dateTime().toPyDateTime().timestamp()
            try:
                self._create_playback_automations(payload, source="desktop")
            except Exception as exc:
                QMessageBox.warning(dialog, "建立播放工作", str(exc))
                return
            refresh_jobs_table()

        def cancel_selected_jobs() -> None:
            rows = sorted({index.row() for index in jobs_table.selectedIndexes()})
            for row in rows:
                item = jobs_table.item(row, 0)
                if item is not None:
                    self.cancel_playback_automation(item.text())
            refresh_jobs_table()

        mode_combo.currentIndexChanged.connect(refresh_target_options)
        target_kind_combo.currentIndexChanged.connect(refresh_target_value)
        create_button.clicked.connect(create_job)
        cancel_button.clicked.connect(cancel_selected_jobs)
        refresh_target_options()
        refresh_jobs_table()
        refresh_timer = QTimer(dialog)
        refresh_timer.timeout.connect(refresh_jobs_table)
        refresh_timer.start(1000)
        dialog.exec_()

    def cancel_playback_automation(self, job_id: str) -> dict[str, Any] | None:
        current = self._playback_automations.get(job_id)
        if current is None:
            return None
        active_slots = [
            slot
            for slot, run in self._slot_playback_runs.items()
            if str(run.get("automation_job_id") or "") == str(job_id)
        ]
        job = self._playback_automations.cancel(job_id)
        for slot in active_slots:
            self.stop_slot_playback(slot)
        self.log(f"已取消播放工作 {job_id}; stopping_slots={active_slots}")
        return job

    def _playback_automation_tick(self) -> None:
        expire_overdue = getattr(self._playback_automations, "expire_overdue_scheduled", None)
        expired_jobs = expire_overdue(grace_seconds=120.0) if callable(expire_overdue) else []
        for expired in expired_jobs:
            self.log(f"過期定時播放未補播: {expired.get('id')} slots={expired.get('slots')}")
        for job in self._playback_automations.due_jobs():
            job_id = str(job.get("id") or "")
            slots = normalize_slots(job.get("slots"))
            busy = [slot for slot in slots if slot in self._slot_playback_runs]
            if busy:
                continue
            if job.get("mode") == "scheduled_once":
                cancelled_loops: list[str] = []
                for slot in slots:
                    for loop_job in self._active_automation_jobs_for_slot(slot, mode="loop"):
                        loop_id = str(loop_job.get("id") or "")
                        if loop_id and loop_id != job_id:
                            self._playback_automations.cancel(loop_id)
                            cancelled_loops.append(loop_id)
                if cancelled_loops:
                    self.log(
                        f"定時工作 {job_id} 已取得優先權並取消循環工作: "
                        f"{sorted(set(cancelled_loops))}"
                    )
            elif job.get("mode") == "loop":
                now = time.time()
                scheduled_due = any(
                    candidate.get("mode") == "scheduled_once"
                    and candidate.get("status") == "waiting"
                    and float(candidate.get("next_run_at") or 0.0) <= now
                    and set(slots).intersection(normalize_slots(candidate.get("slots")))
                    for candidate in self._playback_automations.active_jobs()
                )
                if scheduled_due:
                    continue
            run_id = f"{job_id}:{int(job.get('iteration') or 0) + 1}:{time.time_ns()}"

            def completed(
                result: dict[str, Any],
                status: str,
                *,
                active_job_id: str = job_id,
                active_run_id: str = run_id,
            ) -> None:
                error = None
                if status == "failed":
                    error = str(result.get("error") or "playback integrity check failed")
                updated = self._playback_automations.finish_run(
                    active_job_id,
                    run_id=active_run_id,
                    status=status,
                    result=result,
                    error=error,
                )
                if updated is not None:
                    self.log(
                        f"播放工作 {active_job_id}: status={updated.get('status')} "
                        f"iteration={updated.get('iteration')}"
                    )

            label = f"播放工作 {job_id} 第 {int(job.get('iteration') or 0) + 1} 次"
            try:
                if job.get("target_kind") == "script":
                    script = script_path_from_name(str(job.get("script") or ""))
                    started = self._play_scripts_to_slots(
                        [script],
                        slots,
                        label,
                        show_warnings=False,
                        automation_job_id=job_id,
                        automation_run_id=run_id,
                        completion_callback=completed,
                    )
                else:
                    started = self._play_module_chain(
                        [str(name) for name in job.get("modules") or []],
                        slots,
                        label,
                        show_warnings=False,
                        automation_job_id=job_id,
                        automation_run_id=run_id,
                        completion_callback=completed,
                    )
            except Exception as exc:
                self._playback_automations.fail(job_id, str(exc))
                self.log(f"播放工作 {job_id} 建立播放計畫失敗: {exc}")
                continue
            if started:
                self._playback_automations.mark_running(job_id, run_id)
                break
            else:
                waiting_message = "等待 GAME 上線且可播放"
                if str(job.get("last_error") or "") != waiting_message:
                    self._playback_automations.mark_waiting(job_id, waiting_message)

    def _create_tab_script_mgmt(self) -> QWidget:
        page = QWidget()
        outer = QHBoxLayout(page)

        script_group = QGroupBox("腳本列表")
        script_layout = QVBoxLayout(script_group)
        self.script_list = QListWidget()
        self.script_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.script_list.currentItemChanged.connect(lambda *_: self.update_script_detail())
        script_layout.addWidget(self.script_list)
        script_btns = QHBoxLayout()
        btn_refresh = QPushButton("刷新")
        btn_refresh.clicked.connect(self.refresh_scripts_and_modules)
        btn_delete = QPushButton("刪除腳本")
        btn_delete.clicked.connect(self.delete_selected_script)
        btn_play_test = QPushButton("測試播放選取腳本")
        btn_play_test.setToolTip("使用「模組連串」頁目前選取的 GAME，只播放目前腳本。")
        btn_play_test.clicked.connect(self.play_selected_script)
        script_btns.addWidget(btn_refresh)
        script_btns.addWidget(btn_play_test)
        script_btns.addWidget(btn_delete)
        script_layout.addLayout(script_btns)
        self.script_detail = QTextEdit()
        self.script_detail.setReadOnly(True)
        self.script_detail.setMaximumHeight(140)
        script_layout.addWidget(self.script_detail)
        outer.addWidget(script_group, 2)

        right = QVBoxLayout()
        record_group = QGroupBox("錄製 PC 腳本")
        record_layout = QGridLayout(record_group)
        record_layout.addWidget(QLabel("腳本名稱:"), 0, 0)
        self.record_name_edit = QLineEdit()
        self.record_name_edit.setPlaceholderText("例如: login_click")
        record_layout.addWidget(self.record_name_edit, 0, 1, 1, 2)
        record_layout.addWidget(QLabel("錄製視窗:"), 1, 0)
        self.record_slot_combo = QComboBox()
        record_layout.addWidget(self.record_slot_combo, 1, 1)
        record_layout.addWidget(QLabel("點擊最長時間(ms):"), 2, 0)
        self.record_click_duration_spin = QSpinBox()
        self.record_click_duration_spin.setRange(50, 500)
        self.record_click_duration_spin.setValue(180)
        record_layout.addWidget(self.record_click_duration_spin, 2, 1)
        record_layout.addWidget(QLabel("點擊最大偏移(px):"), 3, 0)
        self.record_click_move_spin = QSpinBox()
        self.record_click_move_spin.setRange(0, 12)
        self.record_click_move_spin.setValue(3)
        record_layout.addWidget(self.record_click_move_spin, 3, 1)
        controls = QHBoxLayout()
        self.btn_record_script = QPushButton()
        self.btn_record_script.setObjectName("RecordButton")
        self.btn_record_script.setIcon(build_record_control_icon("record", "#dc2626"))
        self.btn_record_script.setIconSize(QSize(24, 24))
        self.btn_record_script.setFixedSize(72, 60)
        self.btn_record_script.setToolTip("開始錄製")
        self.btn_record_script.setAccessibleName("開始錄製")
        self.btn_record_script.clicked.connect(self.record_script)
        controls.addWidget(self.btn_record_script)
        self.btn_stop_record = QPushButton()
        self.btn_stop_record.setObjectName("StopRecordButton")
        self.btn_stop_record.setIcon(build_record_control_icon("stop", "#ffffff"))
        self.btn_stop_record.setIconSize(QSize(24, 24))
        self.btn_stop_record.setFixedSize(72, 60)
        self.btn_stop_record.setToolTip("停止錄製並儲存腳本")
        self.btn_stop_record.setAccessibleName("停止錄製")
        self.btn_stop_record.setEnabled(False)
        self.btn_stop_record.clicked.connect(self.stop_record_script)
        controls.addWidget(self.btn_stop_record)
        self.recorder_status_label = QLabel("未錄製")
        self.recorder_status_label.setObjectName("RecorderStatus")
        self.recorder_status_label.setProperty("state", "idle")
        self.recorder_status_label.setAlignment(Qt.AlignCenter)
        controls.addWidget(self.recorder_status_label, 1)
        record_layout.addLayout(controls, 4, 0, 1, 3)
        hint = QLabel(
            "只有在設定時間內且整條路徑偏移不超過設定值才記為點擊；其他一律記為拖曳。"
            "錄製會持續到按下停止，開始後與最後動作後的空檔都會保留；20 分鐘會自動停止作保險。"
            "錄製時可在左鍵操作前或按住期間按 F8 強制標記拖曳，F8 不會傳給遊戲。"
        )
        hint.setWordWrap(True)
        record_layout.addWidget(hint, 5, 0, 1, 3)
        self.recorder_detail_label = QLabel("F8 是一次性強制拖曳標記")
        self.recorder_detail_label.setObjectName("RecorderDetail")
        self.recorder_detail_label.setWordWrap(True)
        record_layout.addWidget(self.recorder_detail_label, 6, 0, 1, 3)
        right.addWidget(record_group)

        module_group = QGroupBox("模組管理")
        module_layout = QVBoxLayout(module_group)
        module_top = QHBoxLayout()
        module_top.addWidget(QLabel("模組:"))
        self.module_combo = QComboBox()
        self.module_combo.setMinimumWidth(180)
        self.module_combo.currentTextChanged.connect(lambda *_: self.refresh_current_module_editor())
        module_top.addWidget(self.module_combo)
        btn_new_module = QPushButton("新建")
        btn_new_module.clicked.connect(self.create_module)
        module_top.addWidget(btn_new_module)
        btn_rename_module = QPushButton("重命名")
        btn_rename_module.clicked.connect(self.rename_module)
        module_top.addWidget(btn_rename_module)
        btn_delete_module = QPushButton("刪除")
        btn_delete_module.setObjectName("DangerButton")
        btn_delete_module.clicked.connect(self.delete_module)
        module_top.addWidget(btn_delete_module)
        module_top.addStretch(1)
        module_layout.addLayout(module_top)
        module_group_row = QHBoxLayout()
        module_group_row.addWidget(QLabel("分類名稱:"))
        self.module_group_combo = QComboBox()
        self.module_group_combo.setEditable(True)
        self.module_group_combo.setInsertPolicy(QComboBox.NoInsert)
        self.module_group_combo.setMinimumWidth(180)
        self.module_group_combo.lineEdit().setPlaceholderText("輸入新名稱或選擇現有分類")
        module_group_row.addWidget(self.module_group_combo)
        btn_edit_module_group = QPushButton("多選模組加入")
        btn_edit_module_group.clicked.connect(self.edit_module_group_members)
        module_group_row.addWidget(btn_edit_module_group)
        module_group_row.addWidget(QLabel("一次勾選多個模組；不影響播放頁顯示全部模組"))
        module_group_row.addStretch(1)
        module_layout.addLayout(module_group_row)
        self.module_script_list = QListWidget()
        self.module_script_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.module_script_list.setMaximumHeight(150)
        module_layout.addWidget(self.module_script_list)
        module_buttons = QHBoxLayout()
        btn_add_script = QPushButton("從腳本加入")
        btn_add_script.clicked.connect(self.add_script_to_module)
        module_buttons.addWidget(btn_add_script)
        btn_remove_script = QPushButton("移除選中")
        btn_remove_script.clicked.connect(self.remove_script_from_module)
        module_buttons.addWidget(btn_remove_script)
        self.module_info_label = QLabel("0 個腳本")
        self.module_info_label.setObjectName("Hint")
        module_buttons.addWidget(self.module_info_label)
        module_buttons.addStretch(1)
        module_layout.addLayout(module_buttons)
        right.addWidget(module_group, 1)
        outer.addLayout(right, 3)
        return page

    def _create_tab_sync(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        group = QGroupBox("同步器")
        layout = QVBoxLayout(group)
        desc = QLabel(
            "此分頁沿用舊 GUI_TEST 的位置，但 PC 遊戲視窗同步輸入尚未接入。\n"
            "舊版同步器是 ADB/模擬器邏輯；PC 版要等點擊/拖曳後端完成測試後再移植。"
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)
        form = QGridLayout()
        form.addWidget(QLabel("主遊戲視窗:"), 0, 0)
        self.sync_master_combo = QComboBox()
        form.addWidget(self.sync_master_combo, 0, 1)
        form.addWidget(QLabel("延遲最小(ms):"), 1, 0)
        delay_min = QSpinBox()
        delay_min.setRange(0, 5000)
        delay_min.setValue(80)
        delay_min.setEnabled(False)
        form.addWidget(delay_min, 1, 1)
        form.addWidget(QLabel("延遲最大(ms):"), 2, 0)
        delay_max = QSpinBox()
        delay_max.setRange(0, 5000)
        delay_max.setValue(350)
        delay_max.setEnabled(False)
        form.addWidget(delay_max, 2, 1)
        layout.addLayout(form)
        self.btn_sync_start = QPushButton("啟動同步（未接入）")
        self.btn_sync_start.setEnabled(False)
        layout.addWidget(self.btn_sync_start)
        outer.addWidget(group)
        outer.addStretch(1)
        return page

    def _create_tab_launcher(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        launcher_group = QGroupBox(
            f"啟動器：突破 5 開限制 / 固定 1-{MAX_SLOT} Slot 對應"
        )
        launcher_group.setProperty("compact", True)
        launcher_layout = QGridLayout(launcher_group)
        launcher_layout.setContentsMargins(8, 8, 8, 6)
        launcher_layout.setHorizontalSpacing(6)
        launcher_layout.setVerticalSpacing(5)
        explain = QLabel(
            f"固定使用 Slot 1-{MAX_SLOT}、NetBind IP 分組及 Windows 使用者隔離。"
            "Launch ALL 只補開缺少的 GAME；失敗後可直接再次按，不需重開 GUI_TEST_PC。"
        )
        explain.setWordWrap(True)
        launcher_layout.addWidget(explain, 0, 0, 1, 5)

        # These controls remain available to the PWA bridge but are intentionally
        # hidden from the fixed desktop Launch ALL workflow.
        self.launcher_slots_edit = QLineEdit(f"1-{MAX_SLOT}", page)
        self.launcher_slots_edit.setReadOnly(True)
        self.launcher_slots_edit.setVisible(False)
        self.launcher_forcebind_combo = QComboBox(page)
        self.launcher_forcebind_combo.addItem("netbind loopback-safe", "netbind")
        self.launcher_forcebind_combo.addItem("off test", "off")
        self.launcher_forcebind_combo.addItem("legacy delayed", "delayed")
        self.launcher_forcebind_combo.addItem("legacy normal old", "normal")
        self.launcher_forcebind_combo.setVisible(False)
        self.launcher_windows_users_cb = QCheckBox("Windows 使用者隔離帳號資料", page)
        self.launcher_windows_users_cb.setChecked(True)
        self.launcher_windows_users_cb.setToolTip(
            f"用 scg_slot01-{MAX_SLOT:02d} 本機 Windows 使用者啟動 StarCG，"
            "讓每個 slot 有獨立 AppData/HKCU。"
        )
        self.launcher_windows_users_cb.setVisible(False)

        autoplay_group = QGroupBox("啟動後自動播放（視窗尺寸穩定後逐槽倒數）")
        autoplay_group.setProperty("compact", True)
        autoplay_layout = QGridLayout(autoplay_group)
        autoplay_layout.setContentsMargins(6, 6, 6, 5)
        autoplay_layout.setHorizontalSpacing(4)
        autoplay_layout.setVerticalSpacing(3)
        self.launcher_autoplay_status_label = QLabel()
        self.launcher_autoplay_status_label.setObjectName("AutoPlayStatus")
        self.launcher_autoplay_status_label.setWordWrap(True)
        autoplay_layout.addWidget(self.launcher_autoplay_status_label, 0, 0, 1, 10)
        for idx in range(10):
            combo = QComboBox()
            combo.setObjectName("LauncherAutoPlayStepCombo")
            combo.setProperty("compact", True)
            combo.setMinimumWidth(78)
            combo.setFixedHeight(30)
            combo.setToolTip(f"啟動後模組連串第 {idx + 1} 步；變更後立即保存。")
            combo.currentIndexChanged.connect(self._launcher_autoplay_chain_changed)
            self.launcher_autoplay_step_combos.append(combo)
            step_label = QLabel(str(idx + 1))
            step_label.setAlignment(Qt.AlignCenter)
            step_label.setObjectName("CompactStepNumber")
            autoplay_layout.addWidget(step_label, 1, idx)
            autoplay_layout.addWidget(combo, 2, idx)
        delay_row = QHBoxLayout()
        delay_row.setSpacing(4)
        delay_row.addWidget(QLabel("視窗穩定後延遲："))
        self.launcher_autoplay_delay_spin = QDoubleSpinBox()
        self.launcher_autoplay_delay_spin.setProperty("compact", True)
        self.launcher_autoplay_delay_spin.setRange(0.0, 600.0)
        self.launcher_autoplay_delay_spin.setSingleStep(1.0)
        self.launcher_autoplay_delay_spin.setDecimals(1)
        self.launcher_autoplay_delay_spin.setSuffix(" 秒")
        self.launcher_autoplay_delay_spin.setValue(self._launcher_autoplay_delay_seconds)
        self.launcher_autoplay_delay_spin.valueChanged.connect(self._launcher_autoplay_delay_changed)
        delay_row.addWidget(self.launcher_autoplay_delay_spin)
        self.launcher_autoplay_disable_btn = QPushButton("清空")
        self.launcher_autoplay_disable_btn.setObjectName("DangerButton")
        self.launcher_autoplay_disable_btn.setProperty("compact", True)
        self.launcher_autoplay_disable_btn.clicked.connect(
            lambda _checked=False: self.set_launcher_autoplay_modules([])
        )
        delay_row.addWidget(self.launcher_autoplay_disable_btn)
        delay_row.addStretch(1)
        autoplay_layout.addLayout(delay_row, 3, 0, 1, 10)
        launcher_layout.addWidget(autoplay_group, 1, 0, 1, 5)
        self._update_launcher_autoplay_ui()
        actions = [
            ("Launch ALL", "start-missing"),
            ("Close ALL", "stop"),
        ]
        for idx, (text, action) in enumerate(actions):
            btn = QPushButton(text)
            btn.setProperty("compact", True)
            if action == "start-missing":
                btn.setObjectName("PrimaryButton")
            btn.clicked.connect(lambda _checked=False, a=action: self.launcher_all_action(a))
            self.launcher_action_buttons[action] = btn
            launcher_layout.addWidget(btn, 2, idx * 2, 1, 2)
        btn_layout_apply = QPushButton("排列全部視窗")
        btn_layout_apply.setProperty("compact", True)
        btn_layout_apply.clicked.connect(self.arrange_all_windows)
        self.window_layout_buttons.append(btn_layout_apply)
        launcher_layout.addWidget(btn_layout_apply, 2, 4)
        layout_hint = QLabel(
            f"硬規則 starcg_4k_stacked_720p_pico_v2: 物理 3840x2160、"
            f"{MAX_SLOT}視窗固定重疊、client 1280x720。"
            "會調整目前運行中的 slot 尺寸及位置；尺寸、位置或前景不符合時 Pico 禁止輸入。"
        )
        layout_hint.setWordWrap(True)
        layout_hint.setObjectName("Hint")
        launcher_layout.addWidget(layout_hint, 3, 0, 1, 5)
        outer.addWidget(launcher_group)

        status_group = QGroupBox(f"{MAX_SLOT} 槽 Launcher 狀態")
        status_layout = QVBoxLayout(status_group)
        self.slot_table = QTableWidget(0, 6)
        self.slot_table.setHorizontalHeaderLabels(["Slot", "Status", "Responding", "Pids", "Title", "LoginData"])
        status_layout.addWidget(self.slot_table)
        outer.addWidget(status_group, 1)

        schedule_group = QGroupBox("定時啟動 / 定時關閉遊戲 / 定時關電腦")
        schedule_layout = QGridLayout(schedule_group)
        self.schedule_action_combo = QComboBox()
        for label, action in [
            ("啟動遊戲", "starcg.start"),
            ("關閉遊戲", "starcg.stop"),
            ("重啟遊戲", "starcg.restart"),
            ("補開缺失", "starcg.start_missing"),
            ("修復異常", "starcg.repair_bad"),
            ("重新命名視窗", "starcg.relabel"),
            ("電腦關機", "pc.shutdown"),
            ("電腦重啟", "pc.reboot"),
        ]:
            self.schedule_action_combo.addItem(label, action)
        self.schedule_slots_edit = QLineEdit(f"1-{MAX_SLOT}")
        self.schedule_time_edit = QDateTimeEdit(QDateTime.currentDateTime().addSecs(300))
        self.schedule_time_edit.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.schedule_time_edit.setCalendarPopup(True)
        btn_add_job = QPushButton("新增定時")
        btn_add_job.clicked.connect(self.add_schedule_job)
        btn_refresh_jobs = QPushButton("刷新排程")
        btn_refresh_jobs.clicked.connect(self.refresh_jobs)
        schedule_layout.addWidget(QLabel("動作:"), 0, 0)
        schedule_layout.addWidget(self.schedule_action_combo, 0, 1)
        schedule_layout.addWidget(QLabel("Slots:"), 0, 2)
        schedule_layout.addWidget(self.schedule_slots_edit, 0, 3)
        schedule_layout.addWidget(QLabel("時間:"), 1, 0)
        schedule_layout.addWidget(self.schedule_time_edit, 1, 1, 1, 2)
        schedule_layout.addWidget(btn_add_job, 1, 3)
        schedule_layout.addWidget(btn_refresh_jobs, 1, 4)
        self.jobs_table = QTableWidget(0, 5)
        self.jobs_table.setHorizontalHeaderLabels(["ID", "Action", "Slots", "Run At", "Status"])
        schedule_layout.addWidget(self.jobs_table, 2, 0, 1, 5)
        outer.addWidget(schedule_group, 1)
        return page

    def _create_tab_log(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.document().setMaximumBlockCount(20000)
        outer.addWidget(QLabel("GUI_TEST_PC 日誌"))
        outer.addWidget(self.log_text, 1)
        log_toolbar = QHBoxLayout()
        btn_refresh_launcher_log = QPushButton("刷新 Launcher Log")
        btn_refresh_launcher_log.clicked.connect(self.refresh_launcher_log)
        log_toolbar.addWidget(btn_refresh_launcher_log)
        log_toolbar.addStretch(1)
        outer.addLayout(log_toolbar)
        self.launcher_log_text = QPlainTextEdit()
        self.launcher_log_text.setReadOnly(True)
        outer.addWidget(QLabel("D:\\15game\\launcher_action.log"))
        outer.addWidget(self.launcher_log_text, 1)
        return page

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #f4f6f8; color: #1f2933; font-size: 18px; }
            QLabel#Title { font-size: 34px; font-weight: 700; }
            QLabel#Subtitle { color: #52606d; }
            QLabel#StatusPill { background: #e6fffa; border: 1px solid #38b2ac; border-radius: 10px; padding: 5px 10px; }
            QGroupBox { border: 1px solid #bcccdc; border-radius: 8px; margin-top: 10px; padding: 10px; background: #ffffff; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; font-weight: 700; }
            QGroupBox[compact="true"] { margin-top: 8px; padding: 5px; }
            QPushButton { background: #ffffff; border: 1px solid #9fb3c8; border-radius: 8px; padding: 10px 18px; min-height: 48px; font-size: 18px; }
            QPushButton[compact="true"] { padding: 2px 8px; min-height: 28px; max-height: 32px; font-size: 14px; border-radius: 6px; }
            QPushButton:hover { background: #eef4fb; }
            QPushButton:checked { background: #c6f6d5; border-color: #2f855a; font-weight: 700; }
            QPushButton:disabled { color: #829ab1; background: #edf2f7; }
            QPushButton#PrimaryButton { background: #2f855a; color: white; border-color: #276749; font-weight: 700; }
            QPushButton#DangerButton { color: #b91c1c; border-color: #fecaca; background: #fff7f7; }
            QPushButton#RecordButton { background: #fff1f2; border: 2px solid #dc2626; padding: 0; }
            QPushButton#RecordButton[recording="true"], QPushButton#RecordButton[recording="true"]:disabled { background: #dc2626; border-color: #991b1b; }
            QPushButton#StopRecordButton { background: #334155; border: 2px solid #1e293b; padding: 0; }
            QPushButton#StopRecordButton:disabled { background: #cbd5e1; border-color: #94a3b8; }
            QTabWidget::pane { border: 1px solid #bcccdc; background: #ffffff; }
            QTabBar::tab { padding: 13px 24px; background: #d9e2ec; border: 1px solid #bcccdc; font-size: 18px; }
            QTabBar::tab:selected { background: #ffffff; font-weight: 700; }
            QWidget#SlotTile { background: #ffffff; border: 1px solid #d9e2ec; border-radius: 8px; }
            QWidget#SlotTile QLabel { color: #52606d; font-size: 11px; }
            QLabel#CompactStepNumber { color: #52606d; font-size: 11px; }
            QLabel#Hint, QLabel#RecorderDetail { color: #52606d; font-size: 14px; }
            QLabel#RecorderStatus { border-radius: 8px; padding: 9px 14px; font-size: 17px; font-weight: 700; }
            QLabel#RecorderStatus[state="idle"] { color: #475569; background: #e2e8f0; border: 1px solid #cbd5e1; }
            QLabel#RecorderStatus[state="recording"] { color: #ffffff; background: #dc2626; border: 1px solid #991b1b; }
            QLabel#RecorderStatus[state="stopping"] { color: #78350f; background: #fef3c7; border: 1px solid #f59e0b; }
            QLabel#AutoPlayStatus { border-radius: 6px; padding: 4px 8px; font-size: 14px; font-weight: 700; }
            QLabel#AutoPlayStatus[state="enabled"] { color: #14532d; background: #dcfce7; border: 1px solid #22c55e; }
            QLabel#AutoPlayStatus[state="disabled"] { color: #475569; background: #e2e8f0; border: 1px solid #cbd5e1; }
            QComboBox#ChainStepCombo, QComboBox#LauncherAutoPlayStepCombo { min-height: 28px; max-height: 30px; padding: 2px 5px; font-size: 13px; }
            QPushButton#ModuleTile { border-width: 2px; color: #1f2933; font-weight: 700; text-align: left; }
            QPushButton#AutoPlayModuleButton { border-width: 2px; font-weight: 700; }
            QPushButton#AutoPlayModuleButton:checked { background: #22c55e; border-color: #15803d; color: #052e16; }
            QPlainTextEdit, QTextEdit, QListWidget, QTableWidget, QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateTimeEdit {
                background: #ffffff; border: 1px solid #bcccdc; border-radius: 6px; padding: 8px; min-height: 42px; font-size: 18px;
            }
            QComboBox[compact="true"], QSpinBox[compact="true"], QDoubleSpinBox[compact="true"] {
                padding: 2px 5px; min-height: 28px; max-height: 30px; font-size: 14px;
            }
            QListWidget::item { padding: 7px 9px; }
            """
        )

    def _compact_all_buttons(self) -> None:
        for button in self.findChildren(QPushButton):
            if button.objectName() in {"RecordButton", "StopRecordButton"}:
                continue
            if button.property("compact"):
                button.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
                button.setMinimumHeight(28)
                button.setMaximumHeight(32)
                button.setMaximumWidth(max(58, button.sizeHint().width() + 12))
                continue
            button.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
            button.setMinimumHeight(max(50, button.minimumHeight()))
            button.setMaximumWidth(max(72, button.sizeHint().width() + 24))

    def log(self, message: str) -> None:
        timestamp = datetime.now().astimezone()
        line = f"{timestamp:%Y-%m-%d %H:%M:%S} | {message}"
        self._gui_log_entries.append((timestamp, line))
        self.log_text.appendPlainText(line)
        append_activity_log(line)

    def _prune_gui_log(self) -> None:
        cutoff = datetime.now().astimezone() - timedelta(hours=24)
        removed = False
        while self._gui_log_entries and self._gui_log_entries[0][0] < cutoff:
            self._gui_log_entries.popleft()
            removed = True
        if removed:
            self.log_text.setPlainText("\n".join(line for _, line in self._gui_log_entries))

    def _set_recorder_status(self, message: str) -> None:
        if not hasattr(self, "recorder_detail_label"):
            return
        self.recorder_detail_label.setText(message)
        if "強制拖曳" in message and self._record_ui_state != "idle":
            self.recorder_detail_label.setStyleSheet("color: #9a4d00; font-weight: 700;")
        elif "目前手勢" in message:
            self.recorder_detail_label.setStyleSheet("color: #075985; font-weight: 700;")
        else:
            self.recorder_detail_label.setStyleSheet("color: #52606d;")

    def _set_recording_ui_state(self, state: str) -> None:
        self._record_ui_state = state
        recording = state == "recording"
        self.btn_record_script.setProperty("recording", recording)
        self.btn_record_script.setIcon(
            build_record_control_icon("record", "#ffffff" if recording else "#dc2626")
        )
        self.recorder_status_label.setProperty("state", state)
        if state == "recording":
            self.recorder_status_label.setText("錄製中... 00:00")
        elif state == "stopping":
            self.recorder_status_label.setText("正在停止並儲存...")
        else:
            self.recorder_status_label.setText("未錄製")
        for widget in (self.btn_record_script, self.recorder_status_label):
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()

    def _update_recording_elapsed(self) -> None:
        if self._record_ui_state != "recording" or self._record_started_at is None:
            return
        elapsed = min(
            RECORDING_SAFETY_LIMIT_SECONDS,
            max(0, int(time.monotonic() - self._record_started_at)),
        )
        minutes, seconds = divmod(elapsed, 60)
        self.recorder_status_label.setText(f"錄製中... {minutes:02d}:{seconds:02d}")

    def run_task(
        self,
        label: str,
        func: Callable[[], Any],
        on_success: Callable[[Any], None] | None = None,
        *,
        quiet: bool = False,
        on_failure: Callable[[str], None] | None = None,
        on_finished: Callable[[], None] | None = None,
    ) -> None:
        if not quiet:
            self.log(f"{label}: 開始")
        worker = TaskWorker(func, label)
        self._workers.append(worker)

        def cleanup() -> None:
            try:
                if on_finished:
                    on_finished()
            except Exception as exc:
                append_crash_log(f"\n--- GUI task cleanup failed: {label} ---\n{exc}")
                self.log(f"{label}: cleanup failed: {exc}")
            finally:
                if worker in self._workers:
                    self._workers.remove(worker)
                worker.deleteLater()

        def finished(result: Any) -> None:
            if on_success:
                try:
                    on_success(result)
                except Exception as exc:
                    message = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
                    append_crash_log(f"\n--- GUI callback failed: {label} ---\n{message}")
                    self.log(f"{label}: callback failed: {exc}")
                    QMessageBox.warning(self, label, message)
            if not quiet:
                self.log(f"{label}: 完成")

        def failed(message: str) -> None:
            if on_failure:
                on_failure(message)
            self.log(f"{label}: 失敗: {message}")
            QMessageBox.warning(self, label, message)

        worker.finished_ok.connect(finished)
        worker.failed.connect(failed)
        worker.finished.connect(cleanup)
        worker.start()

    def test_pico_connection(self) -> None:
        def task() -> dict[str, Any]:
            from starcg_bot.pico_touch import pico_touch_health_check

            return pico_touch_health_check(PICO_TOUCH_CONFIG_PATH)

        def done(result: dict[str, Any]) -> None:
            port = str(result.get("port") or "")
            status = str(result.get("status") or "")
            cooldown = int(result.get("min_slot_interval_ms") or 0)
            self.log(f"Pico 已連線: {port} | {status} | slot cooldown={cooldown}ms")
            QMessageBox.information(
                self,
                "Pico 連線測試",
                f"已連接 {port}。\n{status}\n\n這次測試沒有向 GAME 輸入。",
            )

        self.run_task("測試 Pico 連線", task, done)

    def refresh_all_local(self) -> None:
        self.refresh_windows()
        self.refresh_scripts_and_modules()
        self.refresh_jobs()
        self.refresh_launcher_log()
        self.launcher_action("status", quiet=True)

    def refresh_windows(self) -> None:
        windows = enum_windows()
        games = game_windows(windows)
        self.slot_rows = target_slots(games)
        running = 0
        record_slot = self.record_slot_combo.currentData()
        sync_master_slot = self.sync_master_combo.currentData()
        self.record_slot_combo.clear()
        self.sync_master_combo.clear()
        for row in self.slot_rows:
            slot = int(row["slot"])
            target = row.get("target")
            btn = self.slot_buttons.get(slot)
            label = self.slot_status_labels.get(slot)
            if target:
                self._slot_window_misses[slot] = 0
                running += 1
                title = str(target.get("title") or "")
                hwnd = int(target.get("hwnd") or 0)
                pid = int(target.get("pid") or 0)
                width = int(target.get("width") or 0)
                height = int(target.get("height") or 0)
                source = target.get("slot_source") or "unknown"
                tooltip = (
                    f"GAME {slot:02d}: 可操作\n"
                    f"source={source}\n"
                    f"hwnd=0x{hwnd:X}\npid={pid}\n"
                    f"client={width}x{height}\n"
                    f"title={title}\n"
                    f"{target.get('process_path') or ''}"
                )
                if btn:
                    btn.setEnabled(True)
                    btn.setToolTip(tooltip)
                    self._refresh_slot_button_style(slot)
                if label:
                    label.setText(f"ONLINE\n{width}x{height}\n0x{hwnd:X}")
                    label.setToolTip(tooltip)
                self._set_slot_indicator(slot, True, tooltip)
                self.record_slot_combo.addItem(f"{slot} - {title[:28]}", slot)
                self.sync_master_combo.addItem(f"{slot} - {title[:28]}", slot)
            else:
                self._slot_window_misses[slot] = self._slot_window_misses.get(slot, 0) + 1
                tooltip = f"GAME {slot:02d}: 沒有可操作 StarCG.exe 視窗"
                if btn:
                    run = self._slot_playback_runs.get(slot)
                    if run and self._slot_window_misses[slot] >= 3:
                        run["cancel"].set()
                        self._playing_slots.discard(slot)
                    btn.setEnabled(False)
                    btn.setChecked(False)
                    btn.setToolTip(tooltip)
                    self._refresh_slot_button_style(slot)
                if label:
                    label.setText("OFFLINE")
                    label.setToolTip(tooltip)
                self._set_slot_indicator(slot, False, tooltip)
        record_index = self.record_slot_combo.findData(record_slot)
        if record_index >= 0:
            self.record_slot_combo.setCurrentIndex(record_index)
        sync_index = self.sync_master_combo.findData(sync_master_slot)
        if sync_index >= 0:
            self.sync_master_combo.setCurrentIndex(sync_index)
        self.connection_label.setText(f"已偵測 {running}/{MAX_SLOT} 個 GAME")
        self._update_selected_count()

    def _set_slot_indicator(self, slot: int, online: bool, tooltip: str) -> None:
        button = self.slot_buttons.get(slot)
        if online and slot in self._playing_slots:
            style = "background:#dc2626; border:1px solid #991b1b; border-radius:6px;"
        elif online and button is not None and button.isChecked():
            style = "background:#22c55e; border:1px solid #15803d; border-radius:6px;"
        elif online:
            style = "background:#9ca3af; border:1px solid #6b7280; border-radius:6px;"
        else:
            style = "background:#e5e7eb; border:1px solid #cbd5e1; border-radius:6px;"
        for group in self.slot_indicator_groups:
            dot = group.get(slot)
            if dot is None:
                continue
            dot.setStyleSheet(style)
            dot.setToolTip(tooltip)

    def measure_window_sizes(self) -> None:
        self.refresh_windows()
        payload = save_window_measurements(self.slot_rows)
        measurements = payload.get("measurements") or []
        if not measurements:
            QMessageBox.information(self, "量度 GAME 尺寸", "目前沒有可量度的 StarCG .exe GAME。請先啟動並刷新。")
            return
        sizes = ", ".join(
            f"{item['slot']}={item['client_size']['w']}x{item['client_size']['h']}" for item in measurements
        )
        self.log(f"已量度 {len(measurements)} 個 .exe 視窗 client size: {sizes}")
        self.log(f"量度資料已寫入: {MEASUREMENTS_PATH}")
        QMessageBox.information(
            self,
            "量度 GAME 尺寸",
            f"已量度 {len(measurements)} 個 GAME。\n{sizes}\n\n已寫入:\n{MEASUREMENTS_PATH}",
        )

    def _update_selected_count(self) -> None:
        self.selected_count_label.setText(f"已選 {len(self.selected_slots())} 個 GAME")
        for slot in self.slot_buttons:
            self._refresh_slot_button_style(slot)

    def selected_slots(self) -> list[int]:
        eligible = {
            slot
            for slot, btn in self.slot_buttons.items()
            if btn.isEnabled() and btn.isChecked() and slot not in self._slot_playback_runs
        }
        ordered = [
            slot
            for slot in self._slot_selection_order
            if slot in eligible
        ]
        ordered.extend(sorted(eligible.difference(ordered)))
        self._slot_selection_order = list(ordered)
        return ordered

    def _handle_slot_button_clicked(self, slot: int, checked: bool) -> None:
        run = self._slot_playback_runs.get(slot)
        if run is not None:
            button = self.slot_buttons.get(slot)
            if button:
                button.setChecked(False)
            self.stop_slot_playback(slot)
            checked = False
        self._slot_selection_order = [
            selected_slot
            for selected_slot in self._slot_selection_order
            if selected_slot != slot
        ]
        if checked:
            self._slot_selection_order.append(slot)
        self._update_selected_count()

    def _refresh_slot_button_style(self, slot: int) -> None:
        button = self.slot_buttons.get(slot)
        if button is None:
            return
        if not button.isEnabled():
            button.setStyleSheet("background:#edf2f7; border-color:#bcccdc; color:#829ab1;")
        elif slot in self._playing_slots:
            button.setStyleSheet(
                "background:#dc2626; border-color:#991b1b; color:#ffffff; font-weight:800;"
            )
        elif button.isChecked():
            button.setStyleSheet(
                "background:#22c55e; border-color:#15803d; color:#052e16; font-weight:800;"
            )
        else:
            button.setStyleSheet(
                "background:#d1d5db; border-color:#9ca3af; color:#374151; font-weight:700;"
            )
        self._set_slot_indicator(slot, button.isEnabled(), button.toolTip())

    def _set_slot_playback_progress(self, slot: int, message: str) -> None:
        run = self._slot_playback_runs.get(int(slot))
        if run is None:
            return
        if str(run.get("progress") or "") == str(message):
            return
        run["progress"] = str(message)
        self.log(f"GAME {slot}: {message}")
        command_id = str(run.get("bridge_command_id") or "")
        if command_id:
            self._gui_command_bridge.update(command_id, slot=slot, slot_status=str(message))

    def _set_slot_current_module(self, slot: int, module_name: str) -> None:
        run = self._slot_playback_runs.get(int(slot))
        if run is not None:
            run["current_module"] = str(module_name)

    def stop_slot_playback(self, slot: int) -> bool:
        slot = int(slot)
        run = self._slot_playback_runs.get(slot)
        if run is None:
            cancelled_loops = []
            for job in self._active_automation_jobs_for_slot(slot, mode="loop"):
                job_id = str(job.get("id") or "")
                if job_id:
                    self._playback_automations.cancel(job_id)
                    cancelled_loops.append(job_id)
            if cancelled_loops:
                self.log(f"已取消 GAME {slot} 的循環播放工作: {cancelled_loops}")
                return True
            return False
        automation_job_id = str(run.get("automation_job_id") or "")
        if automation_job_id:
            self._playback_automations.cancel(automation_job_id)
        run["cancel"].set()
        was_playing = slot in self._playing_slots
        self._playing_slots.discard(slot)
        button = self.slot_buttons.get(slot)
        if button:
            button.setChecked(False)
        self._refresh_slot_button_style(slot)
        self._update_selected_count()
        if was_playing:
            self.log(f"已取消 GAME {slot} 的播放；其他 GAME 繼續。")
        return True

    def stop_all_playback(self) -> None:
        automation_ids = [
            str(job.get("id"))
            for job in self._playback_automations.active_jobs()
        ]
        for job_id in automation_ids:
            self._playback_automations.cancel(job_id)
        active = sorted(self._slot_playback_runs)
        if not active:
            self.btn_stop_all_playback.setEnabled(False)
            if automation_ids:
                self.log(f"已取消循環播放工作: {automation_ids}")
            return
        for run in self._slot_playback_runs.values():
            run["cancel"].set()
        self.btn_stop_all_playback.setEnabled(False)
        self.log(
            f"已要求中止全部播放: slots={active} automations={automation_ids}；"
            "目前手勢會安全釋放後停止。"
        )

    def _finish_slot_playback(self, slot: int, token: str, state: str) -> None:
        run = self._slot_playback_runs.get(slot)
        if run is None or str(run.get("token")) != str(token):
            return
        command_id = str(run.get("bridge_command_id") or "")
        self._slot_playback_runs.pop(slot, None)
        self._playing_slots.discard(slot)
        button = self.slot_buttons.get(slot)
        if button:
            button.setChecked(False)
        self._refresh_slot_button_style(slot)
        self._update_selected_count()
        self.btn_stop_all_playback.setEnabled(bool(self._slot_playback_runs))
        if state == "error":
            self.log(f"GAME {slot} 播放失敗並已回復未選取。")
        elif state == "cancelled":
            self.log(f"GAME {slot} 已停止播放並回復未選取。")

        if command_id:
            outcomes = self._pwa_playback_outcomes.setdefault(command_id, {})
            outcomes[int(slot)] = str(state)
            status_text = "完成" if state == "finished" else ("已中止" if state == "cancelled" else "錯誤")
            self._gui_command_bridge.update(command_id, slot=slot, slot_status=status_text)

    def select_running_slots(self) -> None:
        self._slot_selection_order = []
        for slot, btn in self.slot_buttons.items():
            selected = btn.isEnabled() and slot not in self._slot_playback_runs
            btn.setChecked(selected)
            if selected:
                self._slot_selection_order.append(slot)
        self._update_selected_count()

    def clear_slot_selection(self) -> None:
        self._slot_selection_order = []
        for btn in self.slot_buttons.values():
            btn.setChecked(False)
        self._update_selected_count()

    def target_for_slot(self, slot: int) -> dict[str, Any] | None:
        for row in self.slot_rows:
            if int(row.get("slot") or 0) == int(slot):
                return row.get("target")
        return None

    def validated_target_for_slot(self, slot: int) -> dict[str, Any] | None:
        target = self.target_for_slot(slot)
        if not target:
            return None
        if int(target.get("slot") or 0) != int(slot):
            return None
        if str(target.get("id") or "") != f"slot:{int(slot)}":
            return None
        if not target.get("is_game"):
            return None
        if int(target.get("pid") or 0) <= 0 or int(target.get("hwnd") or 0) <= 0:
            return None
        return target

    def refresh_scripts_and_modules(self) -> None:
        current_script = self.chain_script_combo.currentData()
        current_step_values = [combo.currentData() for combo in self.chain_step_combos]
        self.script_list.clear()
        self.chain_script_combo.clear()
        for path in list_pc_scripts():
            summary = load_script_summary(path)
            duration = summary.get("duration_ms", "")
            duration_text = f", {round(int(duration) / 1000, 1)}s" if str(duration).isdigit() else ""
            slot = summary.get("target_slot", "")
            slot_text = f", slot {slot}" if slot else ""
            label = f"{script_display_name(path)} ({summary.get('events', '?')} events{duration_text}{slot_text}, {summary.get('client_size', '?')})"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, str(path))
            self.script_list.addItem(item)
            self.chain_script_combo.addItem(label, str(path))
        if current_script:
            idx = self.chain_script_combo.findData(current_script)
            if idx >= 0:
                self.chain_script_combo.setCurrentIndex(idx)
        current_module = self.chain_module_combo.currentData()
        current_editor_module = self.module_combo.currentText()
        self.module_combo.blockSignals(True)
        self.module_combo.clear()
        self.chain_module_combo.clear()
        module_names = sorted(load_modules().keys(), key=str.casefold)
        module_options: list[tuple[str, str]] = []
        for name in module_names:
            self.module_combo.addItem(name)
            self.chain_module_combo.addItem(name, name)
            script_count = len(module_script_paths(name))
            if script_count:
                module_options.append((f"{name} ({script_count})", name))
        for combo, current_data in zip(self.chain_step_combos, current_step_values):
            self._populate_step_combo(combo, module_options, str(current_data) if current_data else None)
        if current_module:
            idx = self.chain_module_combo.findData(current_module)
            if idx >= 0:
                self.chain_module_combo.setCurrentIndex(idx)
        if current_editor_module:
            idx = self.module_combo.findText(current_editor_module)
            if idx >= 0:
                self.module_combo.setCurrentIndex(idx)
        self.module_combo.blockSignals(False)
        self.rebuild_chain_module_buttons(module_names)
        self.rebuild_launcher_autoplay_module_buttons(module_names)
        self.update_script_detail()
        self.refresh_current_module_editor()
        QTimer.singleShot(0, self._compact_all_buttons)

    def update_script_detail(self) -> None:
        item = self.script_list.currentItem()
        if not item:
            self.script_detail.clear()
            return
        path = Path(item.data(Qt.UserRole))
        summary = load_script_summary(path)
        self.script_detail.setPlainText(json.dumps(summary, ensure_ascii=False, indent=2))

    def selected_script_path(self) -> Path | None:
        if hasattr(self, "script_list") and self.script_list.currentItem():
            data = self.script_list.currentItem().data(Qt.UserRole)
            if data:
                return Path(str(data))
        data = self.chain_script_combo.currentData()
        return Path(data) if data else None

    def selected_script_paths_from_list(self) -> list[Path]:
        paths: list[Path] = []
        for item in self.script_list.selectedItems():
            data = item.data(Qt.UserRole)
            if data:
                paths.append(Path(data))
        if not paths and self.script_list.currentItem():
            paths.append(Path(self.script_list.currentItem().data(Qt.UserRole)))
        return paths

    def delete_selected_script(self) -> None:
        paths = self.selected_script_paths_from_list()
        if not paths:
            return
        names = "\n".join(path.name for path in paths)
        if QMessageBox.question(self, "刪除腳本", f"確定刪除以下 PC 腳本？\n{names}") != QMessageBox.Yes:
            return
        for path in paths:
            if path.exists():
                path.unlink()
                self.log(f"已刪除腳本: {path.name}")
        self.refresh_scripts_and_modules()

    def record_script(self) -> None:
        if self._record_stop_event is not None:
            QMessageBox.information(self, "錄製腳本", "目前已有錄製工作進行中。")
            return
        slot = self.record_slot_combo.currentData()
        if not slot:
            QMessageBox.warning(self, "錄製腳本", "沒有可錄製的遊戲視窗。請先啟動並刷新視窗。")
            return
        slot = int(slot)
        self.refresh_windows()
        target = self.validated_target_for_slot(slot)
        if not target:
            QMessageBox.warning(self, "錄製腳本", f"Slot {slot} 未運行，或 PID/HWND 配對不唯一。")
            return
        name = safe_file_stem(self.record_name_edit.text())
        output = SCRIPT_DIR / f"{name}.pcscript.json"
        if output.exists():
            if QMessageBox.question(self, "覆蓋腳本", f"{output.name} 已存在，是否覆蓋？") != QMessageBox.Yes:
                return
        seconds = RECORDING_SAFETY_LIMIT_SECONDS
        click_max_duration_ms = int(self.record_click_duration_spin.value())
        click_max_move_px = int(self.record_click_move_spin.value())
        hwnd = int(target["hwnd"])
        stop_event = threading.Event()
        self._record_stop_event = stop_event
        self._record_started_at = time.monotonic()
        self.btn_record_script.setEnabled(False)
        self.btn_stop_record.setEnabled(True)
        self._set_recording_ui_state("recording")
        self._set_recorder_status("一般判定；F8 未啟用")
        target_metadata = {
            "slot": int(slot),
            "target_id": target.get("id"),
            "slot_source": target.get("slot_source"),
            "title": target.get("title"),
            "pid": target.get("pid"),
            "process_id": target.get("pid"),
            "hwnd": hwnd,
            "hwnd_hex": f"0x{hwnd:X}",
        }

        def task() -> dict[str, Any]:
            from starcg_bot.pc_script import record_pc_script

            return record_pc_script(
                output=output,
                hwnd=hwnd,
                seconds=seconds,
                click_max_duration_ms=click_max_duration_ms,
                click_max_move_px=click_max_move_px,
                target_metadata=target_metadata,
                stop_requested=stop_event.is_set,
                status_callback=self.recorder_status_changed.emit,
            )

        def reset_record_ui() -> None:
            self._record_stop_event = None
            self._record_started_at = None
            self.btn_record_script.setEnabled(True)
            self.btn_stop_record.setEnabled(False)
            self._set_recording_ui_state("idle")
            self._set_recorder_status("F8 是一次性強制拖曳標記")

        def done(result: Any) -> None:
            reset_record_ui()
            duration_ms = int(result.get("duration_ms") or 0)
            self.log(f"錄製完成: {output.name} events={len(result.get('events') or [])} duration={duration_ms / 1000:.1f}s")
            self.refresh_scripts_and_modules()

        def failed(_message: str) -> None:
            reset_record_ui()

        self.run_task(f"錄製 slot {slot} 腳本", task, done, on_failure=failed)

    def stop_record_script(self) -> None:
        if self._record_stop_event is None:
            return
        self._record_stop_event.set()
        self.btn_stop_record.setEnabled(False)
        self._set_recording_ui_state("stopping")
        self._set_recorder_status("已要求停止，正在寫入完整錄製時間與事件。")
        self.log("已送出停止錄製，正在寫入腳本...")

    def create_module(self) -> None:
        name, ok = QInputDialog.getText(self, "新建模組", "模組名稱:")
        name = name.strip()
        if not ok or not name:
            return
        modules = load_modules()
        if name in modules:
            QMessageBox.warning(self, "新建模組", "模組已存在。")
            return
        modules[name] = []
        save_modules(modules)
        self.refresh_scripts_and_modules()
        self.module_combo.setCurrentText(name)
        self.log(f"已新建模組: {name}")

    def current_module_name(self) -> str | None:
        name = self.module_combo.currentText().strip()
        return name or None

    def rename_module(self) -> None:
        old_name = self.current_module_name()
        if not old_name:
            return
        new_name, ok = QInputDialog.getText(self, "重命名模組", "新名稱:", text=old_name)
        new_name = new_name.strip()
        if not ok or not new_name or new_name == old_name:
            return
        modules = load_modules()
        if new_name in modules:
            QMessageBox.warning(self, "重命名模組", "同名模組已存在。")
            return
        groups, assignments = load_module_group_settings()
        assigned_group = assignments.pop(old_name, None)
        modules[new_name] = modules.pop(old_name, [])
        save_modules(modules)
        if assigned_group:
            assignments[new_name] = assigned_group
        save_module_group_settings(groups, assignments)
        self.refresh_scripts_and_modules()
        self.module_combo.setCurrentText(new_name)
        self.log(f"已重命名模組: {old_name} -> {new_name}")

    def delete_module(self) -> None:
        name = self.current_module_name()
        if not name:
            return
        if QMessageBox.question(self, "刪除模組", f"確定刪除模組 {name}？") != QMessageBox.Yes:
            return
        modules = load_modules()
        modules.pop(name, None)
        save_modules(modules)
        groups, assignments = load_module_group_settings()
        assignments.pop(name, None)
        save_module_group_settings(groups, assignments)
        self.refresh_scripts_and_modules()
        self.log(f"已刪除模組: {name}")

    def add_script_to_module(self) -> None:
        name = self.current_module_name()
        if not name:
            QMessageBox.information(self, "加入腳本", "請先選擇或建立模組。")
            return
        modules = load_modules()
        current = modules.setdefault(name, [])
        available = [path.name for path in list_pc_scripts() if path.name not in current]
        if not available:
            QMessageBox.information(self, "加入腳本", "沒有可加入的腳本。")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f"加入腳本到「{name}」")
        dialog.setMinimumSize(420, 420)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("選擇一個或多個腳本:"))
        script_picker = QListWidget()
        script_picker.setSelectionMode(QAbstractItemView.ExtendedSelection)
        script_picker.addItems(available)
        layout.addWidget(script_picker)
        dialog_buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        dialog_buttons.accepted.connect(dialog.accept)
        dialog_buttons.rejected.connect(dialog.reject)
        layout.addWidget(dialog_buttons)
        if dialog.exec_() != QDialog.Accepted:
            return
        selected = [item.text() for item in script_picker.selectedItems()]
        if not selected:
            return
        current.extend(selected)
        save_modules(modules)
        self.refresh_scripts_and_modules()
        self.log(f"已加入 {len(selected)} 個腳本到模組: {name}")

    def remove_script_from_module(self) -> None:
        name = self.current_module_name()
        selected_items = self.module_script_list.selectedItems()
        if not name or not selected_items:
            return
        modules = load_modules()
        current = modules.get(name, [])
        selected = {item.text() for item in selected_items}
        modules[name] = [script for script in current if script not in selected]
        save_modules(modules)
        self.refresh_scripts_and_modules()
        self.log(f"已從模組移除 {len(selected)} 個腳本: {name}")

    def refresh_module_script_list(self) -> None:
        self.module_script_list.clear()
        name = self.current_module_name()
        if not name:
            self.module_info_label.setText("0 個腳本")
            return
        scripts = load_modules().get(name, [])
        for script in scripts:
            self.module_script_list.addItem(script)
        self.module_info_label.setText(f"{len(scripts)} 個腳本")

    def refresh_current_module_editor(self) -> None:
        self.refresh_module_script_list()
        self.refresh_module_group_editor()

    def refresh_module_group_editor(self) -> None:
        name = self.current_module_name()
        groups, assignments = load_module_group_settings()
        selected_group = assignments.get(name or "", "未分組")
        self.module_group_combo.blockSignals(True)
        self.module_group_combo.clear()
        self.module_group_combo.addItem("未分組")
        self.module_group_combo.addItems(groups)
        if self.module_group_combo.findText(selected_group) < 0:
            self.module_group_combo.addItem(selected_group)
        self.module_group_combo.setCurrentText(selected_group)
        self.module_group_combo.blockSignals(False)
        self.module_group_combo.setEnabled(bool(name))

    def assign_current_module_group(self) -> None:
        module_name = self.current_module_name()
        if not module_name:
            QMessageBox.information(self, "模組分類", "請先選擇或建立模組。")
            return
        group_name = self.module_group_combo.currentText().strip() or "未分組"
        groups, assignments = load_module_group_settings()
        if group_name == "未分組":
            assignments.pop(module_name, None)
        else:
            assignments[module_name] = group_name
            if group_name not in groups:
                groups.append(group_name)
        save_module_group_settings(groups, assignments)
        self.refresh_scripts_and_modules()
        self.module_combo.setCurrentText(module_name)
        self.log(f"模組分類已更新: {module_name} -> {group_name}")

    def edit_module_group_members(self) -> None:
        group_name = self.module_group_combo.currentText().strip()
        if not group_name:
            QMessageBox.information(self, "模組分類", "請先輸入分類名稱。")
            return

        module_names = sorted(load_modules(), key=str.casefold)
        if not module_names:
            QMessageBox.information(self, "模組分類", "目前沒有可分類的模組。")
            return

        groups, assignments = load_module_group_settings()
        dialog = QDialog(self)
        dialog.setWindowTitle(f"分類「{group_name}」- 多選模組")
        dialog.setMinimumSize(680, 720)
        layout = QVBoxLayout(dialog)
        if group_name == "未分組":
            instruction = "勾選要移至「未分組」的模組。其他模組的分類不會改變。"
        else:
            instruction = (
                "勾選屬於此分類的模組。取消已勾選的原成員會移回「未分組」；"
                "其他分類不受影響。"
            )
        instruction_label = QLabel(instruction)
        instruction_label.setWordWrap(True)
        layout.addWidget(instruction_label)

        module_picker = QListWidget()
        module_picker.setAlternatingRowColors(True)
        current_members = {
            module_name
            for module_name in module_names
            if assignments.get(module_name, "未分組") == group_name
        }
        for module_name in module_names:
            assigned_group = assignments.get(module_name, "未分組")
            item = QListWidgetItem(module_name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if module_name in current_members else Qt.Unchecked)
            item.setToolTip(f"目前分類: {assigned_group}")
            module_picker.addItem(item)
        layout.addWidget(module_picker, 1)

        selection_row = QHBoxLayout()
        selected_count_label = QLabel()
        selected_count_label.setObjectName("Hint")

        def checked_names() -> set[str]:
            return {
                module_picker.item(index).text()
                for index in range(module_picker.count())
                if module_picker.item(index).checkState() == Qt.Checked
            }

        def refresh_selected_count() -> None:
            selected_count_label.setText(
                f"已勾選 {len(checked_names())} / {module_picker.count()} 個模組"
            )

        def set_all_checked(checked: bool) -> None:
            state = Qt.Checked if checked else Qt.Unchecked
            module_picker.blockSignals(True)
            for index in range(module_picker.count()):
                module_picker.item(index).setCheckState(state)
            module_picker.blockSignals(False)
            refresh_selected_count()

        btn_check_all = QPushButton("全選")
        btn_check_all.clicked.connect(lambda: set_all_checked(True))
        selection_row.addWidget(btn_check_all)
        btn_clear_checks = QPushButton("清除勾選")
        btn_clear_checks.clicked.connect(lambda: set_all_checked(False))
        selection_row.addWidget(btn_clear_checks)
        selection_row.addWidget(selected_count_label)
        selection_row.addStretch(1)
        layout.addLayout(selection_row)
        module_picker.itemChanged.connect(lambda *_: refresh_selected_count())
        refresh_selected_count()

        dialog_buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        dialog_buttons.button(QDialogButtonBox.Save).setText("儲存分類")
        dialog_buttons.accepted.connect(dialog.accept)
        dialog_buttons.rejected.connect(dialog.reject)
        layout.addWidget(dialog_buttons)
        if dialog.exec_() != QDialog.Accepted:
            return

        checked = checked_names()
        updated_groups, updated_assignments = update_module_group_membership(
            groups,
            assignments,
            group_name,
            checked,
            module_names,
        )
        save_module_group_settings(updated_groups, updated_assignments)
        self.refresh_scripts_and_modules()
        self.module_group_combo.setCurrentText(group_name)
        self.log(f"模組分類已批次更新: {group_name}，{len(checked)} 個模組")

    def _play_scripts_to_slots(
        self,
        scripts: list[Path],
        slots: list[int],
        label: str,
        *,
        show_warnings: bool = True,
        distribute_one_per_slot: bool = False,
        distribution_slots: list[int] | None = None,
        bridge_command_id: str | None = None,
        automation_job_id: str | None = None,
        automation_run_id: str | None = None,
        completion_callback: Callable[[dict[str, Any], str], None] | None = None,
    ) -> bool:
        if not scripts:
            if show_warnings:
                QMessageBox.warning(self, label, "沒有可播放的腳本。")
            else:
                self.log(f"{label}: no scripts")
            return False
        if distribute_one_per_slot:
            from starcg_bot.pc_script import assign_scripts_round_robin

            assignments = assign_scripts_round_robin(scripts, distribution_slots or slots)
            plans_by_slot = {
                slot: [{"step_index": 0, "module_name": label, "script_path": assignments[slot]}]
                for slot in slots
            }
        else:
            plans_by_slot = {
                slot: [
                    {"step_index": index, "module_name": label, "script_path": script}
                    for index, script in enumerate(scripts)
                ]
                for slot in slots
            }
        return self._play_slot_plans(
            plans_by_slot,
            slots,
            label,
            show_warnings=show_warnings,
            distribution_slots=distribution_slots,
            bridge_command_id=bridge_command_id,
            automation_job_id=automation_job_id,
            automation_run_id=automation_run_id,
            completion_callback=completion_callback,
        )

    def _play_module_chain(
        self,
        module_names: list[str],
        slots: list[int],
        label: str,
        *,
        show_warnings: bool = True,
        bridge_command_id: str | None = None,
        automation_job_id: str | None = None,
        automation_run_id: str | None = None,
        completion_callback: Callable[[dict[str, Any], str], None] | None = None,
    ) -> bool:
        from starcg_bot.module_chain import build_module_chain_plan

        module_steps: list[tuple[str, list[Path]]] = []
        invalid_modules: list[str] = []
        for module_name in module_names:
            scripts = module_script_paths(module_name)
            if not scripts:
                invalid_modules.append(module_name)
            else:
                module_steps.append((module_name, scripts))
        if invalid_modules:
            message = f"以下模組沒有有效腳本: {invalid_modules}"
            if show_warnings:
                QMessageBox.warning(self, label, message)
            else:
                self.log(f"{label}: {message}")
            return False
        assignments = build_module_chain_plan(module_steps, slots)
        plans_by_slot = {
            slot: [
                {
                    "step_index": step.step_index,
                    "module_name": step.module_name,
                    "script_path": step.script_path,
                }
                for step in slot_plan
            ]
            for slot, slot_plan in assignments.items()
        }
        return self._play_slot_plans(
            plans_by_slot,
            slots,
            label,
            show_warnings=show_warnings,
            bridge_command_id=bridge_command_id,
            automation_job_id=automation_job_id,
            automation_run_id=automation_run_id,
            completion_callback=completion_callback,
        )

    def _play_slot_plans(
        self,
        plans_by_slot: dict[int, list[dict[str, Any]]],
        slots: list[int],
        label: str,
        *,
        show_warnings: bool = True,
        distribution_slots: list[int] | None = None,
        bridge_command_id: str | None = None,
        automation_job_id: str | None = None,
        automation_run_id: str | None = None,
        completion_callback: Callable[[dict[str, Any], str], None] | None = None,
    ) -> bool:
        slots = list(dict.fromkeys(int(slot) for slot in slots))
        busy = [slot for slot in slots if slot in self._slot_playback_runs]
        if busy:
            message = f"以下 slot 已在播放或停止中；本次整張指令未啟動，避免部分播放: {busy}"
            if show_warnings:
                QMessageBox.information(self, label, message)
            else:
                self.log(f"{label}: {message}")
            return False
        plans_by_slot = {slot: plans_by_slot.get(slot, []) for slot in slots}
        missing_plans = [slot for slot, plan in plans_by_slot.items() if not plan]
        if missing_plans:
            message = f"以下 slot 沒有播放計畫: {missing_plans}"
            if show_warnings:
                QMessageBox.warning(self, label, message)
            else:
                self.log(f"{label}: {message}")
            return False
        battle_plan_error = battle_interrupt_plan_error(plans_by_slot, slots)
        if battle_plan_error:
            if show_warnings:
                QMessageBox.warning(self, label, battle_plan_error)
            else:
                self.log(f"{label}: {battle_plan_error}")
            return False
        self.refresh_windows()
        targets_by_slot = {
            slot: dict(target)
            for slot in slots
            if (target := self.validated_target_for_slot(slot)) is not None
        }
        invalid = [slot for slot in slots if slot not in targets_by_slot]
        if invalid:
            if show_warnings:
                QMessageBox.warning(self, label, f"以下 slot 未運行，或 PID/HWND 配對不唯一: {invalid}")
            else:
                self.log(f"{label}: invalid slot targets {invalid}")
            return False
        layout_slots = [
            slot for slot in range(1, MAX_SLOT + 1) if self.target_for_slot(slot) is not None
        ]
        speed = PLAYBACK_SPEED
        allow_size_mismatch = self.allow_size_mismatch_cb.isChecked()
        backend = "pico_hid_touch"
        stagger_seconds = 0.0
        scheduler_order = list(distribution_slots or slots)
        scheduler_order = [
            slot for slot in scheduler_order
            if slot in slots
        ] + [
            slot for slot in slots
            if slot not in scheduler_order
        ]
        handles_by_slot = self._playback_coordinator.enqueue(scheduler_order)
        runs_by_slot: dict[int, dict[str, Any]] = {}
        for slot in slots:
            cancel_event = threading.Event()
            token = f"{id(cancel_event):x}"
            first_module = str(plans_by_slot[slot][0]["module_name"])
            run = {
                "token": token,
                "cancel": cancel_event,
                "progress": "等待開始",
                "first_module": first_module,
                "current_module": first_module,
            }
            if automation_job_id:
                run["automation_job_id"] = automation_job_id
                run["automation_run_id"] = automation_run_id
            if bridge_command_id:
                run["bridge_command_id"] = bridge_command_id
                self._gui_command_bridge.update(
                    bridge_command_id,
                    slot=slot,
                    slot_status="等待播放",
                )
            self._slot_playback_runs[slot] = run
            runs_by_slot[slot] = run
            self._playing_slots.add(slot)
            button = self.slot_buttons.get(slot)
            if button:
                button.setChecked(False)
            self._refresh_slot_button_style(slot)
        self._slot_selection_order = [
            slot for slot in self._slot_selection_order
            if slot not in slots
        ]
        self._update_selected_count()
        self.btn_stop_all_playback.setEnabled(True)

        def task() -> dict[str, Any]:
            from concurrent.futures import ThreadPoolExecutor, as_completed

            from starcg_bot.battle_interrupt_runtime import (
                is_battle_interrupt_descriptor,
                run_battle_interrupt_module,
            )
            from starcg_bot.pc_script import play_pc_script

            layout_result = run_window_layout_action("ensure", layout_slots)
            for moved_slot in layout_result.get("moved_slots", []):
                self.playback_slot_progress.emit(
                    int(moved_slot),
                    "視窗已移回標準位置",
                )

            def play_slot(
                slot: int,
                target: dict[str, Any],
                cancel_event: threading.Event,
                slot_plan: list[dict[str, Any]],
                playback_handle: int,
            ) -> dict[str, Any]:
                hwnd = int(target["hwnd"])
                slot_results: list[dict[str, Any]] = []
                cancelled = False
                try:
                    metadata = self._playback_coordinator.metadata(playback_handle)
                    self.playback_slot_progress.emit(
                        slot,
                        f"等待排程第 {metadata['group_number']} 組",
                    )
                    timeline_started_at = self._playback_coordinator.wait_for_timeline_start(
                        playback_handle,
                        stop_requested=cancel_event.is_set,
                    )
                    if timeline_started_at is None:
                        return {"results": slot_results, "cancelled": True}
                    for step_number, step in enumerate(slot_plan, start=1):
                        if cancel_event.is_set():
                            cancelled = True
                            break
                        script = Path(step["script_path"])
                        module_name = str(step["module_name"])
                        self.playback_slot_module.emit(slot, module_name)
                        self.playback_slot_progress.emit(
                            slot,
                            f"模組 {step_number}/{len(slot_plan)}: {module_name} ({script.name})",
                        )
                        if is_battle_interrupt_descriptor(script):
                            self.playback_slot_progress.emit(
                                slot,
                                "等待取得中斷戰鬥的 Pico 獨占輪次",
                            )
                            result = run_battle_interrupt_in_playback_turn(
                                self._playback_coordinator,
                                playback_handle,
                                cancel_event.is_set,
                                lambda: run_battle_interrupt_module(
                                    descriptor_path=script,
                                    hwnd=hwnd,
                                    expected_slot=slot,
                                    pico_config_path=PICO_TOUCH_CONFIG_PATH,
                                    stop_requested=cancel_event.is_set,
                                    progress_callback=lambda message, active_slot=slot: (
                                        self.playback_slot_progress.emit(active_slot, message)
                                    ),
                                ),
                            )
                            if result is None:
                                cancelled = True
                                break
                        else:
                            result = play_pc_script(
                                script_path=script,
                                hwnd=hwnd,
                                speed=speed,
                                allow_size_mismatch=allow_size_mismatch,
                                expected_slot=slot,
                                backend=backend,
                                pico_config_path=PICO_TOUCH_CONFIG_PATH,
                                stop_requested=cancel_event.is_set,
                                playback_coordinator=self._playback_coordinator,
                                playback_handle=playback_handle,
                                timeline_started_at=timeline_started_at,
                            )
                        result_cancelled = bool(result.get("cancelled"))
                        event_count = int(result.get("event_count") or 0)
                        executed_event_count = len(result.get("actions") or [])
                        is_dry_run = bool(result.get("dry_run"))
                        hid_acknowledged = (
                            not is_dry_run
                            and not result_cancelled
                            and event_count > 0
                            and executed_event_count == event_count
                        )
                        step_acknowledged = bool(
                            result.get("step_acknowledged", hid_acknowledged)
                        )
                        slot_results.append(
                            {
                                "slot": slot,
                                "module_index": int(step["step_index"]) + 1,
                                "module": module_name,
                                "script": script.name,
                                "backend": result.get("backend"),
                                "event_count": event_count,
                                "executed_event_count": executed_event_count,
                                "hid_acknowledged": hid_acknowledged,
                                "step_acknowledged": step_acknowledged,
                                "start_delay_ms": 0,
                                "scheduler_delay_ms": result.get("scheduler_delay_ms", 0),
                                "cancelled": result_cancelled,
                                "dry_run": is_dry_run,
                                "outcome": result.get("outcome"),
                                "machine": result.get("machine"),
                                "log_path": result.get("log_path"),
                                "capture_paths": result.get("capture_paths"),
                                "would_click": result.get("would_click"),
                            }
                        )
                        if result_cancelled:
                            cancelled = True
                            break
                        if not step_acknowledged:
                            raise RuntimeError(
                                f"slot {slot} module {module_name} incomplete: "
                                f"outcome={result.get('outcome') or 'unknown'}, "
                                f"executed {executed_event_count}/{event_count} actions"
                            )
                        if step_number < len(slot_plan):
                            self.playback_slot_progress.emit(
                                slot,
                                f"模組間等待 {MODULE_CHAIN_GAP_SECONDS:.1f}s",
                            )
                            if not self._playback_coordinator.wait_delay(
                                playback_handle,
                                MODULE_CHAIN_GAP_SECONDS,
                                stop_requested=cancel_event.is_set,
                            ):
                                cancelled = True
                                break
                            timeline_started_at = time.monotonic()
                    return {
                        "results": slot_results,
                        "cancelled": cancelled or cancel_event.is_set(),
                    }
                finally:
                    self._playback_coordinator.finish(playback_handle)

            results: list[dict[str, Any]] = []
            errors: list[dict[str, Any]] = []
            cancelled_slots: list[int] = []
            max_workers = max(1, min(len(slots), MAX_SLOT))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(
                        play_slot,
                        slot,
                        targets_by_slot[slot],
                        runs_by_slot[slot]["cancel"],
                        plans_by_slot[slot],
                        handles_by_slot[slot],
                    ): slot
                    for slot in slots
                    if targets_by_slot.get(slot)
                }
                for future in as_completed(futures):
                    slot = futures[future]
                    state = "finished"
                    try:
                        outcome = future.result()
                        results.extend(outcome["results"])
                        if outcome["cancelled"]:
                            cancelled_slots.append(slot)
                            state = "cancelled"
                    except Exception as exc:
                        if runs_by_slot[slot]["cancel"].is_set():
                            cancelled_slots.append(slot)
                            state = "cancelled"
                        else:
                            errors.append({"slot": slot, "error": str(exc)})
                            state = "error"
                    finally:
                        self.playback_slot_finished.emit(slot, runs_by_slot[slot]["token"], state)
            integrity = summarize_playback_integrity(
                plans_by_slot,
                results,
                errors,
                cancelled_slots,
            )
            return {
                **integrity,
                "backend": backend,
                "slot_stagger_seconds": stagger_seconds,
                "playback_group_size": PLAYBACK_GROUP_SIZE,
                "long_wait_seconds": PLAYBACK_LONG_WAIT_SECONDS,
                "module_gap_seconds": MODULE_CHAIN_GAP_SECONDS,
                "scheduler_order": scheduler_order,
                "scheduler_grant_runs": self._playback_coordinator.snapshot()["grant_runs"],
                "results": results,
                "errors": errors,
                "cancelled_slots": sorted(cancelled_slots),
            }

        def done(result: Any) -> None:
            self.log(json.dumps(result, ensure_ascii=False, indent=2))
            cancelled_slots = list(result.get("cancelled_slots") or [])
            if cancelled_slots:
                final_status = "cancelled"
                self.log(f"{label}: 已中止，未回報完整完成: slots={cancelled_slots}")
            elif not result.get("all_steps_acknowledged"):
                final_status = "failed"
                self.log(
                    f"{label}: 播放不完整，未回報完成: "
                    f"slots={result.get('incomplete_slots') or []}"
                )
            else:
                final_status = "completed"
                self.log(
                    f"{label}: 完成且已核對 "
                    f"{result.get('acknowledged_module_count')}/"
                    f"{result.get('expected_module_count')} 個模組"
                )
            if bridge_command_id:
                self._gui_command_bridge.update(
                    bridge_command_id,
                    status=final_status,
                    result=result,
                    error=(
                        None
                        if final_status != "failed"
                        else "one or more requested modules did not complete every Pico-acknowledged event"
                    ),
                )
                self._pwa_playback_outcomes.pop(bridge_command_id, None)
            if completion_callback:
                completion_callback(dict(result), final_status)

        def failed(_message: str) -> None:
            for slot, run in runs_by_slot.items():
                run["cancel"].set()
                self._playback_coordinator.finish(handles_by_slot[slot])
                self._finish_slot_playback(slot, run["token"], "error")
            if bridge_command_id:
                self._gui_command_bridge.update(
                    bridge_command_id,
                    status="failed",
                    error=str(_message),
                )
            if completion_callback:
                completion_callback({"error": str(_message)}, "failed")

        self.log(f"{label}: 開始")
        self.run_task(label, task, done, quiet=True, on_failure=failed)
        return True

    def play_selected_script(self) -> None:
        script = self.selected_script_path()
        slots = self.selected_slots()
        if not slots:
            QMessageBox.information(self, "播放腳本", "請先選擇 GAME。")
            return
        if not script:
            QMessageBox.information(self, "播放腳本", "請先選擇腳本。")
            return
        self._play_scripts_to_slots([script], slots, "播放 PC 腳本")

    def play_selected_module(self) -> None:
        module_name = self.chain_module_combo.currentData()
        slots = self.selected_slots()
        if not slots:
            QMessageBox.information(self, "播放模組", "請先選擇 GAME。")
            return
        if not module_name:
            QMessageBox.information(self, "播放模組", "請先選擇模組。")
            return
        if not module_script_paths(str(module_name)):
            QMessageBox.information(self, "播放模組", f"模組 {module_name} 沒有有效腳本。")
            return
        self._play_module_chain([str(module_name)], slots, f"播放模組 {module_name}")

    def _launcher_autoplay_module_names(self) -> list[str]:
        return list(self._launcher_autoplay_modules)

    def _launcher_autoplay_enabled(self, module_names: list[str] | None = None) -> bool:
        active_modules = self._launcher_autoplay_modules
        if not active_modules:
            return False
        return module_names is None or active_modules == module_names

    def _update_launcher_autoplay_ui(self) -> None:
        if not hasattr(self, "launcher_autoplay_status_label"):
            return
        module_names = self._launcher_autoplay_modules
        expected_width, expected_height = self._launcher_autoplay_expected_client_size
        if module_names:
            self.launcher_autoplay_status_label.setText(
                f"目前已生效：新槽 {expected_width}x{expected_height} 穩定後等待 "
                f"{self._launcher_autoplay_delay_seconds:.1f} 秒，播放 "
                + " > ".join(module_names)
            )
            self.launcher_autoplay_status_label.setProperty("state", "enabled")
        else:
            self.launcher_autoplay_status_label.setText("目前已生效：關閉，不會在啟動或重啟遊戲後播放模組")
            self.launcher_autoplay_status_label.setProperty("state", "disabled")
        style = self.launcher_autoplay_status_label.style()
        style.unpolish(self.launcher_autoplay_status_label)
        style.polish(self.launcher_autoplay_status_label)
        for index, combo in enumerate(self.launcher_autoplay_step_combos):
            selected = module_names[index] if index < len(module_names) else ""
            selected_index = combo.findData(selected)
            combo.blockSignals(True)
            combo.setCurrentIndex(selected_index if selected_index >= 0 else 0)
            combo.blockSignals(False)

    def _launcher_autoplay_chain_changed(self, _index: int = 0) -> None:
        module_names = [
            str(combo.currentData())
            for combo in self.launcher_autoplay_step_combos
            if combo.currentData()
        ]
        self.set_launcher_autoplay_modules(module_names)

    def set_launcher_autoplay_module(self, module_name: str | None) -> None:
        self.set_launcher_autoplay_modules([module_name] if module_name else [])

    def set_launcher_autoplay_modules(self, module_names: list[str]) -> None:
        normalized = [
            str(module_name).strip()
            for module_name in module_names
            if str(module_name).strip()
        ][:10]
        invalid = [
            module_name
            for module_name in normalized
            if not module_script_paths(module_name)
        ]
        if invalid:
            QMessageBox.information(
                self,
                "啟動後自動播放",
                "以下模組沒有可播放腳本：" + ", ".join(invalid),
            )
            self._update_launcher_autoplay_ui()
            return
        if normalized == self._launcher_autoplay_modules:
            self._update_launcher_autoplay_ui()
            return
        self._launcher_autoplay_generation += 1
        self._launcher_autoplay_modules = normalized
        save_launcher_autoplay_settings(normalized, self._launcher_autoplay_delay_seconds)
        self._update_launcher_autoplay_ui()
        if normalized:
            self.log(
                f"Auto-play module chain applied immediately: modules={normalized} "
                f"delay={self._launcher_autoplay_delay_seconds:.1f}s"
            )
        else:
            self.log("Auto-play module chain cleared; pending launcher auto-play was cancelled")

    def _launcher_autoplay_delay_changed(self, value: float) -> None:
        delay_seconds = min(600.0, max(0.0, float(value)))
        if delay_seconds == self._launcher_autoplay_delay_seconds:
            return
        self._launcher_autoplay_generation += 1
        self._launcher_autoplay_delay_seconds = delay_seconds
        save_launcher_autoplay_settings(self._launcher_autoplay_modules, delay_seconds)
        self._update_launcher_autoplay_ui()
        self.log(f"Auto-play delay applied immediately: delay={delay_seconds:.1f}s")

    def schedule_launcher_autoplay(self, slots: list[int], module_names: list[str]) -> None:
        pending = set(int(slot) for slot in slots)
        if not pending or not module_names:
            return
        self._launcher_autoplay_generation += 1
        generation = self._launcher_autoplay_generation
        self._launcher_autoplay_expected_client_size = load_launcher_autoplay_expected_client_size()
        readiness = {slot: {} for slot in pending}
        deadline = time.monotonic() + AUTOPLAY_READY_TIMEOUT_SECONDS
        expected_width, expected_height = self._launcher_autoplay_expected_client_size
        self.log(
            f"Auto-play module chain queued after launcher: modules={module_names} "
            f"slots={sorted(pending)} expected_client={expected_width}x{expected_height}"
        )
        QTimer.singleShot(
            500,
            lambda: self._launcher_autoplay_attempt(
                module_names,
                pending,
                generation,
                readiness,
                deadline,
            ),
        )

    def _launcher_autoplay_attempt(
        self,
        module_names: list[str],
        pending: set[int],
        generation: int,
        readiness: dict[int, dict[str, Any]],
        deadline: float,
    ) -> None:
        if (
            generation != self._launcher_autoplay_generation
            or not self._launcher_autoplay_enabled(module_names)
        ):
            self.log(f"Auto-play module chain cancelled before start: modules={module_names}")
            return
        if not pending:
            return
        missing_modules = [
            module_name
            for module_name in module_names
            if not module_script_paths(module_name)
        ]
        if missing_modules:
            self.log(
                "Auto-play module chain skipped; no valid scripts: "
                + ", ".join(missing_modules)
            )
            return
        self.refresh_windows()
        now = time.monotonic()
        delay_seconds = float(self._launcher_autoplay_delay_seconds)
        ready: list[int] = []
        for slot in sorted(pending):
            state = readiness.setdefault(slot, {})
            previous_phase = str(state.get("phase") or "")
            target = self.validated_target_for_slot(slot)
            is_ready, reason = advance_launcher_autoplay_slot_readiness(
                target,
                state,
                now=now,
                delay_seconds=delay_seconds,
                expected_client_size=self._launcher_autoplay_expected_client_size,
            )
            phase = str(state.get("phase") or "")
            if phase != previous_phase:
                if phase == "delay":
                    width, height = self._launcher_autoplay_expected_client_size
                    self.log(
                        f"Auto-play slot {slot} stable at {width}x{height}; "
                        f"delay started: {delay_seconds:.1f}s"
                    )
                elif phase == "waiting-size":
                    self.log(f"Auto-play slot {slot} waiting for correct size: {reason}")
                elif phase == "waiting-window" and previous_phase:
                    self.log(f"Auto-play slot {slot} window lost; readiness reset")
            if is_ready:
                ready.append(slot)
        if ready:
            for slot in ready:
                pending.discard(slot)
            self.log(
                f"Auto-play module chain ready after stable-window delay: "
                f"modules={module_names} slots={ready}"
            )
            self._execute_launcher_autoplay(
                module_names,
                ready,
                generation,
            )
        if pending and now < deadline:
            QTimer.singleShot(
                AUTOPLAY_READY_POLL_MS,
                lambda: self._launcher_autoplay_attempt(
                    module_names,
                    pending,
                    generation,
                    readiness,
                    deadline,
                ),
            )
        elif pending:
            details = {
                slot: str(readiness.get(slot, {}).get("reason") or "not ready")
                for slot in sorted(pending)
            }
            self.log(
                f"Auto-play module chain timed out: modules={module_names} "
                f"missing={sorted(pending)} details={details}"
            )

    def _execute_launcher_autoplay(
        self,
        module_names: list[str],
        ready: list[int],
        generation: int,
    ) -> None:
        if (
            generation != self._launcher_autoplay_generation
            or not self._launcher_autoplay_enabled(module_names)
        ):
            self.log(
                f"Auto-play module chain cancelled during configured delay: modules={module_names}"
            )
            return
        self._play_module_chain(
            module_names,
            ready,
            f"Auto-play module chain {' > '.join(module_names)}",
            show_warnings=False,
        )

    def launcher_slots(self) -> list[int]:
        return normalize_slots(self.launcher_slots_edit.text())

    def launcher_all_action(self, action: str) -> None:
        self.launcher_slots_edit.setText(f"1-{MAX_SLOT}")
        self.launcher_action(action)

    def arrange_all_windows(self) -> None:
        self.launcher_slots_edit.setText(f"1-{MAX_SLOT}")
        self.window_layout_action("ensure")

    def set_launcher_busy(self, busy: bool) -> None:
        self._launcher_busy = busy
        if not busy:
            self._launcher_action = None
            self._launcher_slots.clear()
        for action, button in self.launcher_action_buttons.items():
            if action != "status":
                button.setEnabled(not busy)
        for button in self.window_layout_buttons:
            button.setEnabled(not busy and not self._window_layout_busy)

    def set_window_layout_busy(self, busy: bool) -> None:
        self._window_layout_busy = busy
        for button in self.window_layout_buttons:
            button.setEnabled(not busy and not self._launcher_busy)

    def window_layout_action(self, action: str) -> None:
        if self._launcher_busy or self._window_layout_busy:
            self.log("Window layout: ignored while another launcher or layout action is pending")
            return
        requested_slots = self.launcher_slots()
        self.refresh_windows()
        slots = [slot for slot in requested_slots if self.target_for_slot(slot)]
        skipped_slots = [slot for slot in requested_slots if slot not in slots]
        if not slots:
            QMessageBox.information(self, "視窗排列", "目前所選範圍內沒有正在運行的 GAME 視窗。")
            return
        if skipped_slots:
            self.log(f"Window layout: skipped non-running slots {skipped_slots}; active slots {slots}")
        self.set_window_layout_busy(True)

        def task() -> dict[str, Any]:
            result = run_window_layout_action(action, slots)
            result["requested_slots"] = requested_slots
            result["skipped_not_running"] = skipped_slots
            return result

        def done(result: Any) -> None:
            self.log(json.dumps(result, ensure_ascii=False, indent=2)[:6000])
            self.refresh_windows()
            self.set_window_layout_busy(False)
            if not result.get("ok"):
                message = str(result.get("error") or "Window layout is not ready")
                QMessageBox.warning(self, "視窗排列", message)

        self.run_task(
            f"Window layout {action}",
            task,
            done,
            on_failure=lambda _message: self.set_window_layout_busy(False),
        )

    def launcher_action(self, action: str, *, quiet: bool = False) -> None:
        slots = self.launcher_slots()
        is_control_action = action != "status"
        autoplay_actions = {"start", "restart", "start-missing", "repair-bad"}
        autoplay_modules: list[str] = []
        autoplay_slots = list(slots)
        if (
            action in autoplay_actions
            and self._launcher_autoplay_enabled()
        ):
            autoplay_modules = self._launcher_autoplay_module_names()
            if action in {"start", "start-missing", "repair-bad"}:
                self.refresh_windows()
                autoplay_slots = [slot for slot in slots if not self.target_for_slot(slot)]
        if is_control_action and self._launcher_busy:
            self.log(f"Launcher {action}: ignored because another launcher action is still pending")
            return
        if is_control_action:
            self._launcher_action = action
            if action in {"start", "start-missing"}:
                self._launcher_slots = {
                    slot for slot in slots if not self.target_for_slot(slot)
                }
            elif action == "stop":
                self._launcher_slots = {
                    slot for slot in slots if self.target_for_slot(slot)
                }
            else:
                self._launcher_slots = set(slots)
            self.set_launcher_busy(True)

        def task() -> dict[str, Any]:
            forcebind_mode = str(self.launcher_forcebind_combo.currentData() or "netbind")
            use_windows_users = bool(self.launcher_windows_users_cb.isChecked())
            if action in ELEVATED_LAUNCHER_ACTIONS:
                return run_launcher_action_elevated(
                    action,
                    slots,
                    forcebind_mode=forcebind_mode,
                    use_windows_users=use_windows_users,
                )
            return run_launcher_action(
                action,
                slots,
                forcebind_mode=forcebind_mode,
                use_windows_users=use_windows_users,
            )

        def done(result: Any) -> None:
            if action == "status":
                data = result.get("data") or {}
                if isinstance(data, list):
                    slots_data = data
                elif isinstance(data, dict):
                    slots_data = data.get("slots") or data.get("Slots") or []
                else:
                    slots_data = []
                self.render_slot_status(slots_data)
            self.log(json.dumps(result, ensure_ascii=False, indent=2)[:6000])
            self.refresh_windows()
            self.refresh_launcher_log()
            if result.get("ok") and autoplay_modules:
                self.schedule_launcher_autoplay(autoplay_slots, autoplay_modules)
            if result.get("elevated"):
                QTimer.singleShot(3000, self.refresh_all_local)
                QTimer.singleShot(15000, self.refresh_all_local)
                QTimer.singleShot(35000, self.refresh_all_local)

        self.run_task(
            f"Launcher {action}",
            task,
            done,
            quiet=quiet,
            on_failure=lambda _message: self.set_launcher_busy(False),
            on_finished=(
                (lambda: self.set_launcher_busy(False))
                if is_control_action
                else None
            ),
        )

    def render_slot_status(self, slots: list[dict[str, Any]]) -> None:
        self.slot_table.setRowCount(0)
        for slot in slots:
            row = self.slot_table.rowCount()
            self.slot_table.insertRow(row)
            values = [
                slot.get("Slot", slot.get("slot", "")),
                slot.get("Status", slot.get("status", "")),
                slot.get("Responding", slot.get("responding", "")),
                ",".join(str(x) for x in (slot.get("Pids") or slot.get("pids") or [])),
                slot.get("Title", slot.get("title", "")),
                slot.get("LoginData", slot.get("login_data", "")),
            ]
            for col, value in enumerate(values):
                self.slot_table.setItem(row, col, QTableWidgetItem(str(value)))
        self.slot_table.resizeColumnsToContents()

    def add_schedule_job(self) -> None:
        action = str(self.schedule_action_combo.currentData())
        slots = self.schedule_slots_edit.text()
        run_at_dt = self.schedule_time_edit.dateTime().toPyDateTime().astimezone()
        body: dict[str, Any] = {
            "action": action,
            "slots": slots,
            "run_at": run_at_dt.isoformat(timespec="seconds"),
        }
        if action in ("pc.shutdown", "pc.reboot"):
            if QMessageBox.question(self, "電腦電源排程", f"確認新增 {self.schedule_action_combo.currentText()} 排程？") != QMessageBox.Yes:
                return
            body["confirm"] = True
        try:
            job = create_schedule_job(body)
        except Exception as exc:
            QMessageBox.warning(self, "新增定時", str(exc))
            return
        self.log(f"已新增定時: {job.get('id')} {action}")
        self.refresh_jobs()

    def refresh_jobs(self) -> None:
        jobs = load_jobs()
        self.jobs_table.setRowCount(0)
        for job in reversed(jobs):
            row = self.jobs_table.rowCount()
            self.jobs_table.insertRow(row)
            values = [
                job.get("id", ""),
                job.get("action", ""),
                ",".join(str(x) for x in normalize_slots(job.get("slots"))),
                job.get("run_at_iso", ""),
                job.get("status", ""),
            ]
            for col, value in enumerate(values):
                self.jobs_table.setItem(row, col, QTableWidgetItem(str(value)))
        self.jobs_table.resizeColumnsToContents()

    def refresh_launcher_log(self) -> None:
        self.launcher_log_text.setPlainText("\n".join(read_launcher_log_tail(120)) or "沒有 launcher log")

    def _scheduler_tick(self) -> None:
        if self._scheduler_worker is not None:
            return

        def task() -> bool:
            run_due_jobs_once()
            return True

        worker = TaskWorker(task, "scheduler")
        self._scheduler_worker = worker

        def success(_result: object | None = None) -> None:
            self.refresh_jobs()
            self.refresh_windows()

        def fail(message: str) -> None:
            self.log(f"排程檢查失敗: {message}")

        def cleanup() -> None:
            if self._scheduler_worker is worker:
                self._scheduler_worker = None
            worker.deleteLater()

        worker.finished_ok.connect(success)
        worker.failed.connect(fail)
        worker.finished.connect(cleanup)
        worker.start()


def main() -> int:
    install_crash_logging()
    if not acquire_controller_instance_mutex():
        append_activity_log("GUI_TEST_PC duplicate start blocked: controller already running")
        return 0
    set_windows_app_id()
    app = QApplication(sys.argv)
    app.setApplicationName("GUI_TEST_PC")
    app_icon = build_app_icon()
    app.setWindowIcon(app_icon)
    window = GuiTestPcMainWindow()
    window.setWindowIcon(app_icon)
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
