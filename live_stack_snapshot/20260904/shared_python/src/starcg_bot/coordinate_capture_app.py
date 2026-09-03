from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime
import os
from pathlib import Path
import random
import re
import subprocess
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

from PIL import Image

from .json_io import json_text, read_json_file


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ADB = r"D:\LDPlayer4.0\LDPlayer\adb.exe"
DEFAULT_SERIAL = "127.0.0.1:5573"
DEFAULT_MAP_NAME = "芙蕾雅"
DEFAULT_OUTPUT_DIR = "datasets/ocr/world"
DEFAULT_HOTKEY = "+"
BATCH_CAPTURE_COUNT = 20
BATCH_CAPTURE_INTERVAL_SEC = 0.1
SETTINGS_PATH = PROJECT_ROOT / "runs" / "coordinate_capture_settings.json"
LOG_PATH = PROJECT_ROOT / "runs" / "coordinate_capture_app.log"
LABEL_FIELDS = ["filename", "map_name", "east", "south", "index", "width", "height"]
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
WORLD_FILENAME_RE = re.compile(
    r"^world_(?P<map>.+)_東方(?P<east>\d{1,3})_南方(?P<south>\d{1,3})_(?P<idx>\d{3})\.png$"
)
LDPLAYER_SERIALS = [f"127.0.0.1:{5555 + index * 2}" for index in range(10)]

MODIFIERS = {
    "alt": 0x0001,
    "ctrl": 0x0002,
    "control": 0x0002,
    "shift": 0x0004,
    "win": 0x0008,
}

VIRTUAL_KEYS = {
    **{f"f{i}": 0x6F + i for i in range(1, 25)},
    **{str(i): 0x30 + i for i in range(0, 10)},
    **{chr(code + 97): 0x41 + code for code in range(26)},
    "add": 0x6B,
    "plus": 0x6B,
}


@dataclass(frozen=True)
class HotkeySpec:
    modifiers: int
    vk: int
    normalized: str
    bindings: tuple[tuple[int, int], ...] = ()


@dataclass
class CaptureSettings:
    adb_path: str = DEFAULT_ADB
    serial: str = DEFAULT_SERIAL
    serial_history: list[str] = field(default_factory=list)
    map_name: str = DEFAULT_MAP_NAME
    map_names: list[str] = field(default_factory=lambda: [DEFAULT_MAP_NAME])
    output_dir: str = DEFAULT_OUTPUT_DIR
    hotkey: str = DEFAULT_HOTKEY
    generic_mode: bool = False
    generic_name: str = "world"
    batch_mode: bool = False
    last_screenshot: str | None = None
    last_east: int | None = None
    last_south: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> CaptureSettings:
        settings = cls()
        settings.adb_path = _string_value(data.get("adb_path"), DEFAULT_ADB)
        settings.serial = _string_value(data.get("serial"), DEFAULT_SERIAL)
        settings.serial_history = unique_nonempty(_string_list(data.get("serial_history")))
        settings.map_name = _string_value(data.get("map_name"), DEFAULT_MAP_NAME)
        settings.map_names = unique_nonempty([settings.map_name, *_string_list(data.get("map_names"))])
        settings.output_dir = _string_value(data.get("output_dir"), DEFAULT_OUTPUT_DIR)
        settings.hotkey = _string_value(data.get("hotkey"), DEFAULT_HOTKEY)
        settings.generic_mode = bool(data.get("generic_mode", False))
        settings.generic_name = _string_value(data.get("generic_name"), "world")
        settings.batch_mode = bool(data.get("batch_mode", False))
        last_screenshot = data.get("last_screenshot")
        settings.last_screenshot = str(last_screenshot) if last_screenshot else None
        settings.last_east = _optional_coord(data.get("last_east"))
        settings.last_south = _optional_coord(data.get("last_south"))
        return settings

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "adb_path": self.adb_path,
            "serial": self.serial,
            "serial_history": unique_nonempty(self.serial_history),
            "map_name": self.map_name,
            "map_names": unique_nonempty([self.map_name, *self.map_names]),
            "output_dir": self.output_dir,
            "hotkey": self.hotkey,
            "generic_mode": self.generic_mode,
            "generic_name": self.generic_name,
            "batch_mode": self.batch_mode,
        }
        if self.last_screenshot:
            data["last_screenshot"] = self.last_screenshot
        if self.last_east is not None:
            data["last_east"] = self.last_east
        if self.last_south is not None:
            data["last_south"] = self.last_south
        return data


@dataclass(frozen=True)
class CaptureRequest:
    adb_path: Path
    serial: str
    output_dir: Path


@dataclass(frozen=True)
class SaveResult:
    target: Path
    map_name: str
    east: int | None = None
    south: int | None = None


@dataclass(frozen=True)
class DirectCapturePlan:
    request: CaptureRequest
    generic_name: str | None = None
    east: int | None = None
    south: int | None = None


def _string_value(value: object, default: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _optional_coord(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if 1 <= number <= 999:
        return number
    return None


def unique_nonempty(values: list[str] | tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = value.strip()
        if cleaned and cleaned not in seen:
            result.append(cleaned)
            seen.add(cleaned)
    return result


def parse_hotkey(value: str) -> HotkeySpec:
    raw_value = value.strip().lower()
    if raw_value in {"+", "plus", "add", "numpad+"}:
        # Support both numpad + and the main keyboard + (Shift + =).
        return HotkeySpec(
            modifiers=0,
            vk=0x6B,
            normalized="+",
            bindings=((0, 0x6B), (MODIFIERS["shift"], 0xBB)),
        )

    parts = [part.strip().lower() for part in re.split(r"\s*\+\s*", value) if part.strip()]
    if not parts:
        raise ValueError("快捷鍵不可為空")

    modifiers = 0
    key: str | None = None
    normalized_parts: list[str] = []
    for part in parts:
        if part in MODIFIERS:
            modifiers |= MODIFIERS[part]
            normalized_parts.append("ctrl" if part == "control" else part)
        elif part in VIRTUAL_KEYS:
            if key is not None:
                raise ValueError("快捷鍵只能有一個主鍵")
            key = part
            normalized_parts.append(part)
        else:
            raise ValueError(f"不支援的快捷鍵片段: {part}")

    if key is None:
        raise ValueError("快捷鍵需要一個主鍵，例如 F10")
    return HotkeySpec(modifiers=modifiers, vk=VIRTUAL_KEYS[key], normalized="+".join(normalized_parts))


def sanitize_filename_part(value: str) -> str:
    value = value.strip()
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = re.sub(r"\s+", "", value)
    return value or "unknown"


def normalize_generic_capture_name(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("一般截圖名稱不可為空")
    if cleaned.lower().endswith(".png"):
        cleaned = cleaned[:-4]
    if not cleaned:
        raise ValueError("一般截圖名稱不可為空")
    cleaned = sanitize_filename_part(cleaned)
    return cleaned


def normalize_coord(value: str, label: str) -> int:
    value = value.strip()
    if not re.fullmatch(r"\d{1,3}", value):
        raise ValueError(f"{label} 必須是 1 到 999 的數字")
    number = int(value)
    if not 1 <= number <= 999:
        raise ValueError(f"{label} 必須在 1 到 999 之間")
    return number


def normalize_coord_triplet(value: str, label: str) -> int:
    value = value.strip()
    if not re.fullmatch(r"\d{3}", value):
        raise ValueError(f"{label} 必須輸入 3 位數字，例如 014")
    return normalize_coord(value, label)


def resolve_workspace_path(value: str) -> Path:
    path = Path(value.strip())
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def next_index(output_dir: Path, map_name: str) -> int:
    safe_map = re.escape(sanitize_filename_part(map_name))
    pattern = re.compile(rf"^world_{safe_map}_東方\d{{1,3}}_南方\d{{1,3}}_(\d{{3}})\.png$")
    highest = 0
    if output_dir.exists():
        for path in output_dir.glob("*.png"):
            match = pattern.match(path.name)
            if match:
                highest = max(highest, int(match.group(1)))
    return highest + 1


def next_generic_index(output_dir: Path, capture_name: str) -> int:
    safe_name = re.escape(normalize_generic_capture_name(capture_name))
    pattern = re.compile(rf"^{safe_name}_(\d{{3}})\.png$")
    highest = 0
    if output_dir.exists():
        for path in output_dir.glob("*.png"):
            match = pattern.match(path.name)
            if match:
                highest = max(highest, int(match.group(1)))
    return highest + 1


def generic_target_path(output_dir: Path, capture_name: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    normalized_name = normalize_generic_capture_name(capture_name)
    index = next_generic_index(output_dir, normalized_name)
    return output_dir / f"{normalized_name}_{index:03d}.png"


def image_files_for_random_rename(output_dir: Path) -> list[Path]:
    if not output_dir.exists():
        raise FileNotFoundError(f"找不到輸出資料夾: {output_dir}")
    if not output_dir.is_dir():
        raise NotADirectoryError(f"不是資料夾: {output_dir}")
    return [
        path
        for path in sorted(output_dir.iterdir())
        if path.is_file()
        and path.suffix.lower() in IMAGE_EXTENSIONS
        and not path.name.startswith("_")
        and not path.name.startswith(".__rename_tmp_")
    ]


def random_rename_images_by_folder(output_dir: Path, rng: random.Random | None = None) -> list[Path]:
    images = image_files_for_random_rename(output_dir)
    if not images:
        return []

    folder_name = sanitize_filename_part(output_dir.name)
    shuffled = list(images)
    (rng or random).shuffle(shuffled)

    final_paths = [
        output_dir / f"{folder_name}_{index:03d}{path.suffix.lower()}"
        for index, path in enumerate(shuffled, start=1)
    ]
    image_set = {path.resolve() for path in images}
    for target in final_paths:
        if target.exists() and target.resolve() not in image_set:
            raise FileExistsError(f"目標檔名已存在且不在本次圖片清單中: {target.name}")

    token = f".__rename_tmp_{time.time_ns()}"
    temp_entries: list[tuple[Path, Path]] = []
    try:
        for index, path in enumerate(shuffled, start=1):
            temp_path = output_dir / f"{token}_{index:03d}{path.suffix.lower()}"
            path.replace(temp_path)
            temp_entries.append((temp_path, path))

        renamed_paths: list[Path] = []
        for (temp_path, _original_path), final_path in zip(temp_entries, final_paths, strict=True):
            temp_path.replace(final_path)
            renamed_paths.append(final_path)
        return renamed_paths
    except Exception:
        for temp_path, original_path in temp_entries:
            if temp_path.exists() and not original_path.exists():
                temp_path.replace(original_path)
        raise


def load_settings(path: Path = SETTINGS_PATH) -> CaptureSettings:
    if not path.exists():
        return CaptureSettings()
    data = read_json_file(path)
    if not isinstance(data, dict):
        raise ValueError("設定檔格式不是 JSON object")
    return CaptureSettings.from_dict(data)


def save_settings_file(settings: CaptureSettings, path: Path = SETTINGS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".json.tmp")
    temp_path.write_text(json_text(settings.to_dict()) + "\n", encoding="utf-8-sig")
    temp_path.replace(path)


def discover_map_names(output_dir: Path, settings: CaptureSettings | None = None) -> list[str]:
    names: list[str] = []
    if settings:
        names.extend([settings.map_name, *settings.map_names])

    labels_path = output_dir / "labels.csv"
    if labels_path.exists():
        with labels_path.open("r", newline="", encoding="utf-8-sig") as file:
            for row in csv.DictReader(file):
                names.append(row.get("map_name", ""))

    if output_dir.exists():
        for path in sorted(output_dir.glob("world_*.png")):
            match = WORLD_FILENAME_RE.match(path.name)
            if match:
                names.append(match.group("map"))

    names.append(DEFAULT_MAP_NAME)
    return unique_nonempty(names)


def update_labels_csv(
    labels_path: Path,
    *,
    filename: str,
    map_name: str,
    east: int,
    south: int,
    index: int,
    width: int,
    height: int,
) -> None:
    rows: list[dict[str, str]] = []
    image_dir = labels_path.parent
    if labels_path.exists():
        with labels_path.open("r", newline="", encoding="utf-8-sig") as file:
            for row in csv.DictReader(file):
                row_filename = row.get("filename", "")
                if row_filename != filename and (image_dir / row_filename).exists():
                    rows.append({field: str(row.get(field, "")) for field in LABEL_FIELDS})

    rows.append(
        {
            "filename": filename,
            "map_name": map_name,
            "east": str(east),
            "south": str(south),
            "index": str(index),
            "width": str(width),
            "height": str(height),
        }
    )
    rows.sort(key=lambda row: row["filename"])

    labels_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = labels_path.with_suffix(".csv.tmp")
    with temp_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=LABEL_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    temp_path.replace(labels_path)


def capture_png(adb_path: Path, serial: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    command = [str(adb_path), "-s", serial, "exec-out", "screencap", "-p"]
    result = subprocess.run(command, capture_output=True, timeout=15)
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(stderr or f"ADB screencap failed with code {result.returncode}")

    data = result.stdout
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        # Some ADB builds can newline-mangle PNG streams. Repair the common form.
        repaired = data.replace(b"\r\r\n", b"\r\n")
        if repaired.startswith(b"\x89PNG\r\n\x1a\n"):
            data = repaired
        else:
            raise RuntimeError("ADB did not return valid PNG data")
    target.write_bytes(data)


def list_connected_serials(adb_path: Path) -> list[str]:
    result = subprocess.run(
        [str(adb_path), "devices"],
        capture_output=True,
        timeout=10,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        message = result.stderr.strip() or f"adb devices failed with code {result.returncode}"
        raise RuntimeError(message)

    serials: list[str] = []
    for line in result.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            serials.append(parts[0])
    return unique_nonempty(serials)


def write_log(message: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    encoding = "utf-8-sig" if not LOG_PATH.exists() or LOG_PATH.stat().st_size == 0 else "utf-8"
    with LOG_PATH.open("a", encoding=encoding) as file:
        file.write(f"[{timestamp}] {message}\n")


class GlobalHotkey:
    def __init__(self, callback: Callable[[], None]) -> None:
        self.callback = callback
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._registered = threading.Event()
        self._error: Exception | None = None
        self._hotkey_id = 1
        self._registered_ids: list[int] = []
        self._native_thread_id: int | None = None

    def start(self, spec: HotkeySpec) -> None:
        self.stop()
        self._stop_event.clear()
        self._registered.clear()
        self._error = None
        self._registered_ids = []
        self._thread = threading.Thread(target=self._run, args=(spec,), daemon=True)
        self._thread.start()
        if not self._registered.wait(timeout=2):
            if self._error:
                raise self._error
            raise RuntimeError("快捷鍵註冊逾時")
        if self._error:
            raise self._error

    def stop(self) -> None:
        if self._thread and self._thread.is_alive():
            self._stop_event.set()
            try:
                import ctypes

                if self._native_thread_id is not None:
                    ctypes.windll.user32.PostThreadMessageW(self._native_thread_id, 0x0012, 0, 0)
            except Exception:
                pass
            self._thread.join(timeout=1)
        self._thread = None

    def _run(self, spec: HotkeySpec) -> None:
        import ctypes
        from ctypes import wintypes

        self._native_thread_id = threading.get_native_id()
        user32 = ctypes.windll.user32
        bindings = spec.bindings or ((spec.modifiers, spec.vk),)
        for offset, (modifiers, vk) in enumerate(bindings):
            hotkey_id = self._hotkey_id + offset
            if user32.RegisterHotKey(None, hotkey_id, modifiers, vk):
                self._registered_ids.append(hotkey_id)
        if not self._registered_ids:
            self._error = RuntimeError("快捷鍵註冊失敗，可能已被其他程式占用")
            self._registered.set()
            return
        self._registered.set()

        msg = wintypes.MSG()
        try:
            while not self._stop_event.is_set():
                ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret == 0:
                    break
                if msg.message == 0x0312 and int(msg.wParam) in self._registered_ids:
                    self.callback()
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            for hotkey_id in self._registered_ids:
                user32.UnregisterHotKey(None, hotkey_id)
            self._registered_ids = []
            self._native_thread_id = None


class CoordinateCaptureApp:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("星詠魔力座標截圖工具")
        self.root.geometry("1000x520")
        self.root.resizable(False, False)

        startup_messages: list[str] = []
        try:
            settings = load_settings()
        except Exception as exc:
            settings = CaptureSettings()
            startup_messages.append(f"設定檔讀取失敗，已使用預設值：{exc}")

        output_dir = resolve_workspace_path(settings.output_dir)
        self.adb_path_var = tk.StringVar(value=settings.adb_path)
        self.serial_var = tk.StringVar(value=settings.serial)
        self.map_name_var = tk.StringVar(value=settings.map_name)
        self.output_dir_var = tk.StringVar(value=settings.output_dir)
        self.hotkey_var = tk.StringVar(value=settings.hotkey)
        self.generic_mode_var = tk.BooleanVar(value=settings.generic_mode)
        self.generic_name_var = tk.StringVar(value=settings.generic_name)
        self.batch_mode_var = tk.BooleanVar(value=settings.batch_mode)
        self.status_var = tk.StringVar(value="尚未註冊快捷鍵")
        self.east_var = tk.StringVar(value="" if settings.last_east is None else str(settings.last_east))
        self.south_var = tk.StringVar(value="" if settings.last_south is None else str(settings.last_south))
        self.coord_status_var = tk.StringVar(value="尚無待儲存截圖")

        self._busy = False
        self._saving_pending = False
        self._suppress_coord_auto_save = False
        self._pending_capture_path: Path | None = None
        self._last_screenshot = Path(settings.last_screenshot) if settings.last_screenshot else None
        self._last_east = settings.last_east
        self._last_south = settings.last_south
        self._hotkey = GlobalHotkey(lambda: self.root.after(0, self.capture_from_hotkey))
        self._serial_values = unique_nonempty(
            [settings.serial, DEFAULT_SERIAL, *settings.serial_history, *LDPLAYER_SERIALS]
        )
        try:
            self._map_values = discover_map_names(output_dir, settings)
        except Exception as exc:
            self._map_values = unique_nonempty([settings.map_name, DEFAULT_MAP_NAME])
            startup_messages.append(f"地圖名稱讀取失敗，已使用設定值：{exc}")

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        for message in startup_messages:
            self._log(message, error=True)
        self.register_hotkey(save=False, show_errors=False)

    def run(self) -> None:
        self.root.mainloop()

    def close(self) -> None:
        self._hotkey.stop()
        self.root.destroy()

    def _build_ui(self) -> None:
        frame = ttk.Frame(self.root, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(3, weight=0)

        self._entry_row(frame, 0, "ADB 路徑", self.adb_path_var, 60)

        ttk.Label(frame, text="模擬器 serial").grid(row=1, column=0, sticky="e", padx=(0, 8), pady=5)
        self.serial_combo = ttk.Combobox(frame, textvariable=self.serial_var, values=self._serial_values, width=28)
        self.serial_combo.grid(row=1, column=1, sticky="w", pady=5)
        ttk.Button(frame, text="刷新已連線", command=self.refresh_serials).grid(row=1, column=2, sticky="w", padx=(8, 0))

        ttk.Label(frame, text="地圖名稱").grid(row=2, column=0, sticky="e", padx=(0, 8), pady=5)
        self.map_combo = ttk.Combobox(frame, textvariable=self.map_name_var, values=self._map_values, width=28)
        self.map_combo.grid(row=2, column=1, sticky="w", pady=5)
        ttk.Button(frame, text="記住地圖", command=self.remember_map_name).grid(row=2, column=2, sticky="w", padx=(8, 0))

        ttk.Label(frame, text="一般截圖名稱").grid(row=3, column=0, sticky="e", padx=(0, 8), pady=5)
        ttk.Entry(frame, textvariable=self.generic_name_var, width=28).grid(row=3, column=1, sticky="w", pady=5)
        ttk.Checkbutton(
            frame,
            text="不用座標命名",
            variable=self.generic_mode_var,
            command=self._on_generic_mode_changed,
        ).grid(row=3, column=2, sticky="w", padx=(8, 0))

        ttk.Checkbutton(
            frame,
            text="批量截圖 20 張 / 0.1 秒",
            variable=self.batch_mode_var,
            command=self._on_batch_mode_changed,
        ).grid(row=4, column=1, columnspan=2, sticky="w", pady=5)

        ttk.Label(frame, text="輸出資料夾").grid(row=5, column=0, sticky="e", padx=(0, 8), pady=5)
        ttk.Entry(frame, textvariable=self.output_dir_var, width=48).grid(row=5, column=1, sticky="w", pady=5)
        ttk.Button(frame, text="隨機重命名", command=self.random_rename_output_images).grid(
            row=5, column=2, sticky="w", padx=(8, 0)
        )

        ttk.Label(frame, text="快捷鍵").grid(row=6, column=0, sticky="e", padx=(0, 8), pady=5)
        ttk.Entry(frame, textvariable=self.hotkey_var, width=28).grid(row=6, column=1, sticky="w", pady=5)
        ttk.Button(frame, text="套用並保存", command=self.register_hotkey).grid(row=6, column=2, sticky="w", padx=(8, 0))

        buttons = ttk.Frame(frame)
        buttons.grid(row=7, column=1, columnspan=2, sticky="w", pady=(12, 4))
        ttk.Button(buttons, text="立即截圖", command=self.capture_from_hotkey).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="保存設定", command=self.save_current_settings).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="打開輸出資料夾", command=self.open_output_dir).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="打開最近截圖", command=self.open_last_screenshot).pack(side="left")

        info = ttk.Label(
            frame,
            text=(
                "按快捷鍵後會先截圖，再使用右側座標欄。東方/南方各輸入 3 位數字，例如 014、008。"
                "開啟一般截圖模式時，名稱 world 會自動存成 world_001.png。"
            ),
            foreground="#444",
        )
        info.grid(row=8, column=0, columnspan=3, sticky="w", pady=(14, 4))

        ttk.Label(frame, textvariable=self.status_var, foreground="#064f8a").grid(
            row=9, column=0, columnspan=3, sticky="w", pady=(8, 0)
        )

        ttk.Label(frame, text=f"Log: {LOG_PATH}").grid(row=10, column=0, columnspan=3, sticky="w", pady=(12, 2))
        self.log_text = tk.Text(frame, height=10, width=92, state="disabled", wrap="word")
        self.log_text.grid(row=11, column=0, columnspan=3, sticky="nsew")
        self._build_coordinate_panel(frame)

    def _build_coordinate_panel(self, parent: ttk.Frame) -> None:
        panel = ttk.LabelFrame(parent, text="座標輸入", padding=12)
        panel.grid(row=0, column=3, rowspan=12, sticky="n", padx=(18, 0))

        ttk.Label(panel, text="截圖後可直接輸入 6 位").grid(row=0, column=0, columnspan=4, sticky="w")
        ttk.Label(panel, text="例：006014 -> 東方6 南方14", foreground="#555").grid(
            row=1, column=0, columnspan=4, sticky="w", pady=(2, 12)
        )

        validate_command = (self.root.register(self._validate_coord_input), "%P")
        ttk.Label(panel, text="東方").grid(row=2, column=0, sticky="e", padx=(0, 8), pady=8)
        self.east_entry = ttk.Entry(
            panel,
            textvariable=self.east_var,
            width=8,
            validate="key",
            validatecommand=validate_command,
        )
        self.east_entry.grid(row=2, column=1, sticky="w", pady=8)
        ttk.Button(panel, text="+", width=3, command=lambda: self._adjust_coord(self.east_var, 1)).grid(
            row=2, column=2, padx=(8, 4)
        )
        ttk.Button(panel, text="-", width=3, command=lambda: self._adjust_coord(self.east_var, -1)).grid(
            row=2, column=3
        )

        ttk.Label(panel, text="南方").grid(row=3, column=0, sticky="e", padx=(0, 8), pady=8)
        self.south_entry = ttk.Entry(
            panel,
            textvariable=self.south_var,
            width=8,
            validate="key",
            validatecommand=validate_command,
        )
        self.south_entry.grid(row=3, column=1, sticky="w", pady=8)
        ttk.Button(panel, text="+", width=3, command=lambda: self._adjust_coord(self.south_var, 1)).grid(
            row=3, column=2, padx=(8, 4)
        )
        ttk.Button(panel, text="-", width=3, command=lambda: self._adjust_coord(self.south_var, -1)).grid(
            row=3, column=3
        )

        ttk.Button(panel, text="截圖並儲存", command=self.finish_capture_now).grid(
            row=4, column=2, columnspan=2, sticky="ew", pady=(4, 0)
        )
        ttk.Button(panel, text="取消本次", command=self.cancel_pending_capture).grid(
            row=5, column=2, columnspan=2, sticky="ew", pady=(8, 0)
        )
        ttk.Label(panel, textvariable=self.coord_status_var, foreground="#064f8a", wraplength=210).grid(
            row=6, column=0, columnspan=4, sticky="w", pady=(14, 0)
        )

        self.east_var.trace_add("write", lambda *_args: self._on_east_input_changed())
        self.south_var.trace_add("write", lambda *_args: self._on_south_input_changed())
        self.east_entry.bind("<FocusIn>", self._select_entry_text)
        self.south_entry.bind("<FocusIn>", self._select_entry_text)

    def _entry_row(self, parent: ttk.Frame, row: int, label: str, var: tk.StringVar, width: int) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="e", padx=(0, 8), pady=5)
        ttk.Entry(parent, textvariable=var, width=width).grid(row=row, column=1, columnspan=2, sticky="w", pady=5)

    def _validate_coord_input(self, proposed: str) -> bool:
        return proposed == "" or (proposed.isdecimal() and len(proposed) <= 3)

    def _adjust_coord(self, var: tk.StringVar, delta: int) -> None:
        raw_value = var.get().strip()
        try:
            value = normalize_coord(raw_value, "座標") if raw_value else 1
        except ValueError:
            value = 1
        value = max(1, min(999, value + delta))
        self._suppress_coord_auto_save = True
        try:
            var.set(str(value))
        finally:
            self._suppress_coord_auto_save = False

    def _select_entry_text(self, event: tk.Event) -> None:
        widget = event.widget
        if isinstance(widget, ttk.Entry):
            widget.after(0, lambda: widget.selection_range(0, "end"))

    def _set_combo_values(self, combo: ttk.Combobox, values: list[str]) -> None:
        combo["values"] = unique_nonempty(values)

    def _log(self, message: str, *, error: bool = False) -> None:
        self.status_var.set(message)
        try:
            write_log(("ERROR " if error else "INFO ") + message)
        except Exception:
            pass

        timestamp = datetime.now().strftime("%H:%M:%S")
        if hasattr(self, "log_text"):
            self.log_text.configure(state="normal")
            self.log_text.insert("end", f"[{timestamp}] {message}\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")

    def _current_settings(self) -> CaptureSettings:
        last_screenshot = str(self._last_screenshot) if self._last_screenshot else None
        return CaptureSettings(
            adb_path=self.adb_path_var.get().strip() or DEFAULT_ADB,
            serial=self.serial_var.get().strip() or DEFAULT_SERIAL,
            serial_history=unique_nonempty([self.serial_var.get(), *list(self.serial_combo["values"])]),
            map_name=sanitize_filename_part(self.map_name_var.get()),
            map_names=unique_nonempty([self.map_name_var.get(), *list(self.map_combo["values"])]),
            output_dir=self.output_dir_var.get().strip() or DEFAULT_OUTPUT_DIR,
            hotkey=self.hotkey_var.get().strip() or DEFAULT_HOTKEY,
            generic_mode=bool(self.generic_mode_var.get()),
            generic_name=self.generic_name_var.get().strip() or "world",
            batch_mode=bool(self.batch_mode_var.get()),
            last_screenshot=last_screenshot,
            last_east=self._last_east,
            last_south=self._last_south,
        )

    def save_current_settings(self) -> None:
        try:
            save_settings_file(self._current_settings())
        except Exception as exc:
            self._log(f"保存設定失敗：{exc}", error=True)
            messagebox.showerror("保存設定失敗", str(exc))
            return
        self._log(f"設定已保存：{SETTINGS_PATH}")

    def _on_generic_mode_changed(self) -> None:
        if self.generic_mode_var.get():
            self.coord_status_var.set("一般截圖模式：輸入名稱後會自動加 _001")
            self._log("已切換到一般截圖模式，不使用座標命名")
        else:
            self.coord_status_var.set("座標模式：使用地圖名稱與東方/南方命名")
            self._log("已切換到座標截圖模式")

    def _on_batch_mode_changed(self) -> None:
        if self.batch_mode_var.get():
            self.coord_status_var.set("批量截圖模式：快捷鍵會連拍 20 張")
            self._log("已開啟批量截圖：每 0.1 秒一張，共 20 張")
        else:
            self.coord_status_var.set("已關閉批量截圖")
            self._log("已關閉批量截圖")

    def register_hotkey(self, *, save: bool = True, show_errors: bool = True) -> None:
        try:
            spec = parse_hotkey(self.hotkey_var.get())
            self._hotkey.start(spec)
            self.hotkey_var.set(spec.normalized)
            if save:
                save_settings_file(self._current_settings())
            self._log(f"快捷鍵已註冊：{spec.normalized}")
        except Exception as exc:
            self._log(f"快捷鍵註冊失敗：{exc}", error=True)
            if show_errors:
                messagebox.showerror("快捷鍵錯誤", str(exc))

    def remember_map_name(self) -> None:
        map_name = sanitize_filename_part(self.map_name_var.get())
        self.map_name_var.set(map_name)
        self._set_combo_values(self.map_combo, [map_name, *list(self.map_combo["values"])])
        self.save_current_settings()

    def refresh_serials(self) -> None:
        adb_path = resolve_workspace_path(self.adb_path_var.get())
        if not adb_path.exists():
            messagebox.showerror("ADB 錯誤", f"找不到 ADB: {adb_path}")
            self._log(f"刷新 serial 失敗，找不到 ADB：{adb_path}", error=True)
            return
        self._log("正在刷新已連線 serial...")
        threading.Thread(target=self._refresh_serials_worker, args=(adb_path,), daemon=True).start()

    def _refresh_serials_worker(self, adb_path: Path) -> None:
        try:
            serials = list_connected_serials(adb_path)
            self.root.after(0, lambda: self._refresh_serials_done(serials))
        except Exception as exc:
            self.root.after(0, lambda: self._refresh_serials_failed(exc))

    def _refresh_serials_done(self, serials: list[str]) -> None:
        self._set_combo_values(self.serial_combo, [self.serial_var.get(), *serials, *list(self.serial_combo["values"])])
        if serials:
            self._log(f"已刷新 serial：{', '.join(serials)}")
        else:
            self._log("已刷新 serial，但目前沒有狀態為 device 的裝置")

    def _refresh_serials_failed(self, exc: Exception) -> None:
        self._log(f"刷新 serial 失敗：{exc}", error=True)
        messagebox.showerror("刷新 serial 失敗", str(exc))

    def _current_capture_request(self) -> CaptureRequest:
        request = CaptureRequest(
            adb_path=resolve_workspace_path(self.adb_path_var.get()),
            serial=self.serial_var.get().strip(),
            output_dir=resolve_workspace_path(self.output_dir_var.get()),
        )
        if not request.adb_path.exists():
            raise FileNotFoundError(f"找不到 ADB: {request.adb_path}")
        if not request.serial:
            raise ValueError("模擬器 serial 不可為空")
        return request

    def _current_coordinates(self) -> tuple[int, int]:
        return normalize_coord(self.east_var.get(), "東方"), normalize_coord(self.south_var.get(), "南方")

    def capture_from_hotkey(self) -> None:
        if self.batch_mode_var.get():
            self.start_batch_capture()
            return
        if self.generic_mode_var.get():
            self.finish_capture_now()
            return
        if self._busy:
            self._log("已有截圖流程進行中，略過本次快捷鍵")
            return

        try:
            request = self._current_capture_request()
        except Exception as exc:
            self._capture_failed(exc)
            return

        self._busy = True
        self._log(f"截圖中：serial={request.serial}")
        threading.Thread(target=self._capture_worker, args=(request,), daemon=True).start()

    def _capture_worker(self, request: CaptureRequest) -> None:
        try:
            temp_path = request.output_dir / "_pending_capture.png"
            capture_png(request.adb_path, request.serial, temp_path)
            self.root.after(0, lambda: self._capture_ready(temp_path))
        except Exception as exc:
            self.root.after(0, lambda: self._capture_failed(exc))

    def _direct_capture_worker(self, plan: DirectCapturePlan) -> None:
        try:
            temp_path = plan.request.output_dir / "_direct_capture.png"
            capture_png(plan.request.adb_path, plan.request.serial, temp_path)
            self.root.after(0, lambda: self._direct_capture_ready(temp_path, plan))
        except Exception as exc:
            self.root.after(0, lambda: self._capture_failed(exc))

    def _capture_ready(self, temp_path: Path) -> None:
        self._pending_capture_path = temp_path
        self._saving_pending = False
        self.coord_status_var.set("已截圖，請輸入 6 位座標或按右側按鈕儲存")
        self._log("截圖完成，等待右側座標欄輸入")
        self._activate_coordinate_panel()

    def _direct_capture_ready(self, temp_path: Path, plan: DirectCapturePlan) -> None:
        try:
            result = self._save_capture_file_for_plan(temp_path, plan)
        except Exception as exc:
            self._busy = False
            self._saving_pending = False
            self.coord_status_var.set(f"儲存失敗：{exc}")
            self._log(f"儲存失敗：{exc}", error=True)
            messagebox.showerror("儲存失敗", str(exc))
            return
        self._finish_saved_capture(result)

    def _capture_failed(self, exc: Exception) -> None:
        self._busy = False
        self._pending_capture_path = None
        self._saving_pending = False
        self.coord_status_var.set("截圖失敗")
        self._log(f"截圖失敗：{exc}", error=True)
        messagebox.showerror("截圖失敗", str(exc))

    def _on_east_input_changed(self) -> None:
        if (
            self._pending_capture_path is not None
            and len(self.east_var.get()) == 3
            and not self._suppress_coord_auto_save
        ):
            self.south_entry.focus_force()
            self.south_entry.selection_range(0, "end")

    def _on_south_input_changed(self) -> None:
        if (
            self._pending_capture_path is not None
            and len(self.south_var.get()) == 3
            and not self._saving_pending
            and not self._suppress_coord_auto_save
            and not self.generic_mode_var.get()
        ):
            self.root.after(60, self.save_pending_capture)

    def _activate_coordinate_panel(self) -> None:
        def activate() -> None:
            try:
                self.root.lift()
                self.root.attributes("-topmost", True)
                self.root.focus_force()
                self.east_entry.focus_force()
                self.east_entry.selection_range(0, "end")
                self.root.after(300, lambda: self.root.attributes("-topmost", False))
            except tk.TclError:
                pass

        self.root.after(0, activate)

    def start_batch_capture(self) -> None:
        if self._busy:
            self._log("已有截圖流程進行中，略過本次批量截圖")
            return

        try:
            request = self._current_capture_request()
            generic_name = normalize_generic_capture_name(self.generic_name_var.get())
        except Exception as exc:
            self.coord_status_var.set(f"無法批量截圖：{exc}")
            self._log(f"無法批量截圖：{exc}", error=True)
            messagebox.showerror("批量截圖失敗", str(exc))
            return

        self._busy = True
        self._saving_pending = True
        self.coord_status_var.set(f"批量截圖中：0/{BATCH_CAPTURE_COUNT}")
        self._log(
            f"批量截圖開始：{generic_name}，每 {BATCH_CAPTURE_INTERVAL_SEC:.1f} 秒一張，共 {BATCH_CAPTURE_COUNT} 張"
        )
        threading.Thread(target=self._batch_capture_worker, args=(request, generic_name), daemon=True).start()

    def _batch_capture_worker(self, request: CaptureRequest, generic_name: str) -> None:
        saved_paths: list[Path] = []
        temp_path: Path | None = None
        try:
            for index in range(BATCH_CAPTURE_COUNT):
                temp_path = request.output_dir / f"_batch_capture_{threading.get_ident()}_{index:03d}.png"
                capture_png(request.adb_path, request.serial, temp_path)
                target = generic_target_path(request.output_dir, generic_name)
                temp_path.replace(target)
                saved_paths.append(target)
                count = index + 1
                self.root.after(0, lambda count=count, target=target: self._batch_capture_progress(count, target))
                if count < BATCH_CAPTURE_COUNT:
                    time.sleep(BATCH_CAPTURE_INTERVAL_SEC)
        except Exception as exc:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            self.root.after(0, lambda exc=exc, saved_paths=saved_paths: self._batch_capture_failed(exc, saved_paths))
            return

        self.root.after(0, lambda saved_paths=saved_paths: self._batch_capture_done(saved_paths))

    def _batch_capture_progress(self, count: int, target: Path) -> None:
        message = f"批量截圖中：{count}/{BATCH_CAPTURE_COUNT}，最近：{target.name}"
        self.status_var.set(message)
        self.coord_status_var.set(message)

    def _batch_capture_done(self, saved_paths: list[Path]) -> None:
        self._busy = False
        self._saving_pending = False
        if saved_paths:
            self._last_screenshot = saved_paths[-1]
        self.coord_status_var.set(f"批量截圖完成：{len(saved_paths)} 張")
        try:
            save_settings_file(self._current_settings())
        except Exception as exc:
            self._log(f"批量截圖完成，但設定保存失敗：{exc}", error=True)
        self._log(f"批量截圖完成：{len(saved_paths)} 張")

    def _batch_capture_failed(self, exc: Exception, saved_paths: list[Path]) -> None:
        self._busy = False
        self._saving_pending = False
        if saved_paths:
            self._last_screenshot = saved_paths[-1]
        self.coord_status_var.set(f"批量截圖失敗：已儲存 {len(saved_paths)} 張")
        self._log(f"批量截圖失敗：{exc}；已儲存 {len(saved_paths)} 張", error=True)
        messagebox.showerror("批量截圖失敗", f"{exc}\n已儲存 {len(saved_paths)} 張")

    def finish_capture_now(self) -> None:
        if self._pending_capture_path is not None:
            if self.generic_mode_var.get():
                self.save_pending_generic_capture()
            else:
                self.save_pending_capture()
            return
        if self.batch_mode_var.get():
            self.start_batch_capture()
            return
        if self._busy:
            self._log("已有截圖流程進行中，略過本次完成鍵")
            return

        try:
            request = self._current_capture_request()
            if self.generic_mode_var.get():
                generic_name = normalize_generic_capture_name(self.generic_name_var.get())
                plan = DirectCapturePlan(request=request, generic_name=generic_name)
                summary = f"{generic_name}"
            else:
                east, south = self._current_coordinates()
                plan = DirectCapturePlan(request=request, east=east, south=south)
                summary = f"東方{east} 南方{south}"
        except Exception as exc:
            self.coord_status_var.set(f"無法截圖儲存：{exc}")
            self._log(f"無法截圖儲存：{exc}", error=True)
            messagebox.showerror("截圖儲存失敗", str(exc))
            return

        self._busy = True
        self._saving_pending = True
        self.coord_status_var.set("截圖並儲存中...")
        self._log(f"截圖並儲存：{summary} serial={request.serial}")
        threading.Thread(target=self._direct_capture_worker, args=(plan,), daemon=True).start()

    def save_pending_capture(self) -> None:
        if self._saving_pending:
            return
        temp_path = self._pending_capture_path
        if temp_path is None:
            self.coord_status_var.set("目前沒有待儲存截圖")
            self._log("目前沒有待儲存截圖", error=True)
            return

        self._saving_pending = True
        try:
            east, south = self._current_coordinates()
            self.coord_status_var.set("儲存中...")
            result = self._save_capture_file(temp_path, east, south)
        except Exception as exc:
            self._saving_pending = False
            self.coord_status_var.set(f"儲存失敗：{exc}")
            self._log(f"儲存失敗：{exc}", error=True)
            messagebox.showerror("座標錯誤", str(exc))
            if not self.east_var.get().strip():
                self.east_entry.focus_force()
            else:
                self.south_entry.focus_force()
            return

        self._finish_saved_capture(result)

    def save_pending_generic_capture(self) -> None:
        if self._saving_pending:
            return
        temp_path = self._pending_capture_path
        if temp_path is None:
            self.coord_status_var.set("目前沒有待儲存截圖")
            self._log("目前沒有待儲存截圖", error=True)
            return

        self._saving_pending = True
        try:
            generic_name = normalize_generic_capture_name(self.generic_name_var.get())
            self.coord_status_var.set("儲存中...")
            result = self._save_generic_capture_file(temp_path, generic_name)
        except Exception as exc:
            self._saving_pending = False
            self.coord_status_var.set(f"儲存失敗：{exc}")
            self._log(f"儲存失敗：{exc}", error=True)
            messagebox.showerror("儲存失敗", str(exc))
            return

        self._finish_saved_capture(result)

    def _save_capture_file_for_plan(self, temp_path: Path, plan: DirectCapturePlan) -> SaveResult:
        if plan.generic_name is not None:
            return self._save_generic_capture_file(temp_path, plan.generic_name)
        if plan.east is None or plan.south is None:
            raise ValueError("座標模式缺少東方/南方座標")
        return self._save_coordinate_capture_file(temp_path, plan.east, plan.south)

    def _save_capture_file(self, temp_path: Path, east: int, south: int) -> SaveResult:
        return self._save_coordinate_capture_file(temp_path, east, south)

    def _save_coordinate_capture_file(self, temp_path: Path, east: int, south: int) -> SaveResult:
        target, map_name, index = self._target_path(east, south)
        self._log(f"儲存截圖中：{target.name}")
        temp_path.replace(target)
        with Image.open(target) as image:
            width, height = image.size
        update_labels_csv(
            target.parent / "labels.csv",
            filename=target.name,
            map_name=map_name,
            east=east,
            south=south,
            index=index,
            width=width,
            height=height,
        )
        return SaveResult(target=target, map_name=map_name, east=east, south=south)

    def _save_generic_capture_file(self, temp_path: Path, generic_name: str) -> SaveResult:
        target = self._generic_target_path(generic_name)
        self._log(f"儲存一般截圖中：{target.name}")
        temp_path.replace(target)
        return SaveResult(target=target, map_name=generic_name)

    def _finish_saved_capture(self, result: SaveResult) -> None:
        self._busy = False
        self._saving_pending = False
        self._pending_capture_path = None
        self._last_screenshot = result.target
        if result.east is not None and result.south is not None:
            self._last_east = result.east
            self._last_south = result.south
            self.east_var.set(str(result.east))
            self.south_var.set(str(result.south))
            self.coord_status_var.set(f"已儲存：東方{result.east} 南方{result.south}")
        else:
            self.coord_status_var.set(f"已儲存：{result.target.name}")
        if result.east is not None and result.south is not None:
            self._set_combo_values(self.map_combo, [result.map_name, *list(self.map_combo["values"])])
        try:
            save_settings_file(self._current_settings())
        except Exception as exc:
            self._log(f"截圖已儲存，但設定保存失敗：{exc}", error=True)
        self._log(f"已儲存並更新 labels.csv：{result.target.name}")

    def cancel_pending_capture(self) -> None:
        temp_path = self._pending_capture_path
        if temp_path is None:
            self.coord_status_var.set("目前沒有待取消截圖")
            return
        try:
            temp_path.unlink(missing_ok=True)
        finally:
            self._busy = False
            self._saving_pending = False
            self._pending_capture_path = None
            self.coord_status_var.set("已取消本次截圖")
            self._log("已取消本次截圖")

    def _target_path(self, east: int, south: int) -> tuple[Path, str, int]:
        output_dir = resolve_workspace_path(self.output_dir_var.get())
        output_dir.mkdir(parents=True, exist_ok=True)
        map_name = sanitize_filename_part(self.map_name_var.get())
        index = next_index(output_dir, map_name)
        name = f"world_{map_name}_東方{east}_南方{south}_{index:03d}.png"
        return output_dir / name, map_name, index

    def _generic_target_path(self, generic_name: str) -> Path:
        return generic_target_path(resolve_workspace_path(self.output_dir_var.get()), generic_name)

    def random_rename_output_images(self) -> None:
        if self._busy:
            messagebox.showinfo("隨機重命名", "目前截圖流程進行中，請完成後再重命名。")
            self._log("截圖流程進行中，略過隨機重命名", error=True)
            return

        output_dir = resolve_workspace_path(self.output_dir_var.get())
        try:
            images = image_files_for_random_rename(output_dir)
        except Exception as exc:
            self._log(f"讀取輸出資料夾失敗：{exc}", error=True)
            messagebox.showerror("隨機重命名失敗", str(exc))
            return

        if not images:
            self._log(f"輸出資料夾沒有可重命名圖片：{output_dir}")
            messagebox.showinfo("隨機重命名", "輸出資料夾沒有可重命名圖片。")
            return

        folder_name = sanitize_filename_part(output_dir.name)
        warning = (
            f"將把 {len(images)} 張圖片隨機重命名為：\n"
            f"{folder_name}_001.png 到 {folder_name}_{len(images):03d}.png\n\n"
            "此操作會更改原檔名。"
        )
        if (output_dir / "labels.csv").exists():
            warning += "\n\n注意：此功能不會更新 labels.csv。"
        if not messagebox.askyesno("確認隨機重命名", warning):
            self._log("已取消隨機重命名")
            return

        try:
            renamed_paths = random_rename_images_by_folder(output_dir)
        except Exception as exc:
            self._log(f"隨機重命名失敗：{exc}", error=True)
            messagebox.showerror("隨機重命名失敗", str(exc))
            return

        if renamed_paths:
            self._last_screenshot = renamed_paths[-1]
        self._log(f"已隨機重命名 {len(renamed_paths)} 張圖片：{folder_name}_001 到 {folder_name}_{len(renamed_paths):03d}")
        messagebox.showinfo("隨機重命名完成", f"已重命名 {len(renamed_paths)} 張圖片。")

    def open_output_dir(self) -> None:
        output_dir = resolve_workspace_path(self.output_dir_var.get())
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            subprocess.Popen(["explorer", str(output_dir)])
            self._log(f"已打開輸出資料夾：{output_dir}")
        except Exception as exc:
            self._log(f"打開輸出資料夾失敗：{exc}", error=True)
            messagebox.showerror("打開輸出資料夾失敗", str(exc))

    def open_last_screenshot(self) -> None:
        path = self._last_screenshot
        if path is None:
            self._log("尚無最近截圖可打開", error=True)
            messagebox.showinfo("最近截圖", "尚無最近截圖")
            return
        if not path.exists():
            self._log(f"最近截圖不存在：{path}", error=True)
            messagebox.showerror("最近截圖不存在", str(path))
            return

        try:
            startfile = getattr(os, "startfile", None)
            if startfile:
                startfile(path)
            else:
                subprocess.Popen(["explorer", f"/select,{path}"])
            self._log(f"已打開最近截圖：{path.name}")
        except Exception as exc:
            self._log(f"打開最近截圖失敗：{exc}", error=True)
            messagebox.showerror("打開最近截圖失敗", str(exc))


def main() -> int:
    app = CoordinateCaptureApp()
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
