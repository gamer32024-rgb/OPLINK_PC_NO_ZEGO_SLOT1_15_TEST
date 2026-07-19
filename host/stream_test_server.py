from __future__ import annotations

import argparse
import atexit
import ctypes
import json
import os
import re
import subprocess
import threading
import time
from ctypes import wintypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from pico_stream_input import PicoStreamInputController, PicoStreamInputError


ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "runtime"
SLOT_TITLE = re.compile(r"^\[(\d{2})\](?:\s.*)?$")
SLOT_PID_MAP_PATH = Path(
    os.environ.get("GUI_TEST_PC_SLOT_PID_MAP", r"D:\15game\gui_test_pc_slot_pids.json")
)
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _window_text(user32: ctypes.WinDLL, hwnd: int) -> str:
    length = int(user32.GetWindowTextLengthW(hwnd))
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, len(buffer))
    return buffer.value.strip()


def _process_path(pid: int) -> str | None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
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
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        capacity = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(capacity.value)
        if not kernel32.QueryFullProcessImageNameW(
            handle, 0, buffer, ctypes.byref(capacity)
        ):
            return None
        return buffer.value
    finally:
        kernel32.CloseHandle(handle)


def _slot_pid_map() -> dict[int, dict[str, object]]:
    if not SLOT_PID_MAP_PATH.exists():
        return {}
    try:
        payload = json.loads(SLOT_PID_MAP_PATH.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    result: dict[int, dict[str, object]] = {}
    if not isinstance(payload, dict):
        return result
    for slot_text, value in payload.items():
        try:
            slot = int(slot_text)
            pid = int((value or {}).get("Pid") or (value or {}).get("pid") or 0)
        except (TypeError, ValueError, AttributeError):
            continue
        if 1 <= slot <= 15 and pid > 0:
            result[pid] = {
                "slot": slot,
                "expected_exe": str((value or {}).get("Exe") or ""),
            }
    return result


def _numbered_title(slot: int, title: str) -> str:
    base = re.sub(r"^\[\d{1,2}\]\s*", "", title).strip() or "StarCG"
    return f"[{slot:02d}] {base}"


def _window_candidates() -> list[dict[str, object]]:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    pid_map = _slot_pid_map()
    candidates: list[dict[str, object]] = []

    enum_proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @enum_proc_type
    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        title = _window_text(user32, hwnd)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        pid_value = int(pid.value)
        mapped = pid_map.get(pid_value)
        title_match = SLOT_TITLE.match(title)
        if mapped:
            slot = int(mapped["slot"])
            identity_source = "gui_test_pc_pid_map"
        elif title_match:
            slot = int(title_match.group(1))
            identity_source = "window_title"
        else:
            return True

        process_path = _process_path(pid_value)
        expected_exe = str((mapped or {}).get("expected_exe") or "")
        if process_path and Path(process_path).name.casefold() != "starcg.exe":
            return True
        if expected_exe and process_path:
            expected_normalized = os.path.normcase(os.path.abspath(expected_exe))
            actual_normalized = os.path.normcase(os.path.abspath(process_path))
            if expected_normalized != actual_normalized:
                return True

        desired_title = _numbered_title(slot, title)
        title_rename_ok = title == desired_title
        if not title_rename_ok:
            title_rename_ok = bool(user32.SetWindowTextW(hwnd, desired_title))
            if title_rename_ok:
                title = desired_title

        rect = wintypes.RECT()
        if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
            return True
        width = int(rect.right - rect.left)
        height = int(rect.bottom - rect.top)
        if width <= 0 or height <= 0:
            return True

        try:
            dpi = int(user32.GetDpiForWindow(hwnd)) or 96
        except AttributeError:
            dpi = 96
        candidates.append(
            {
                "slot": slot,
                "hwnd": int(hwnd),
                "pid": pid_value,
                "title": title,
                "title_rename_ok": title_rename_ok,
                "identity_source": identity_source,
                "process_path": process_path,
                "slot_pid_map_path": str(SLOT_PID_MAP_PATH) if mapped else None,
                "client_logical": {"w": width, "h": height},
                "window_dpi": dpi,
                "capture_physical_expected": {
                    "w": round(width * dpi / 96),
                    "h": round(height * dpi / 96),
                },
                "aspect": width / height,
            }
        )
        return True

    if not user32.EnumWindows(callback, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    return candidates


def source_identity(slot: int) -> dict[str, object]:
    matches = [item for item in _window_candidates() if item["slot"] == slot]
    if not matches:
        return {
            "ok": False,
            "slot": slot,
            "error": (
                f"No visible slot {slot} game window was found by GUI_TEST_PC PID map "
                f"or [{slot:02d}] title"
            ),
            "captured_at_ms": int(time.time() * 1000),
        }
    if len(matches) > 1:
        return {
            "ok": False,
            "slot": slot,
            "error": f"More than one visible window matches [{slot:02d}]",
            "matches": matches,
            "captured_at_ms": int(time.time() * 1000),
        }
    result = dict(matches[0])
    result.update(
        {
            "ok": True,
            "captured_at_ms": int(time.time() * 1000),
            "aspect_is_16_9": abs(float(result["aspect"]) - (16 / 9)) <= 0.001,
        }
    )
    return result


def load_runtime_state() -> dict[str, object]:
    state_path = RUNTIME / "state.json"
    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def sources_payload(slots: list[int]) -> dict[str, object]:
    state = load_runtime_state()
    return {
        "ok": True,
        "generated_at_ms": int(time.time() * 1000),
        "profile": state.get(
            "profile",
            {"encoded": {"w": 1920, "h": 1080}, "fps": 30, "bitrate_kbps": 6000},
        ),
        "encoder": state.get("encoder", "unknown"),
        "network_underlay": state.get("network_underlay", {}),
        "input": state.get("input", {"enabled": False}),
        "sources": [source_identity(slot) for slot in slots],
    }


class StreamPublisherError(RuntimeError):
    pass


class GuiTestPcInputError(RuntimeError):
    def __init__(self, message: str, status: int = HTTPStatus.BAD_GATEWAY) -> None:
        super().__init__(message)
        self.status = int(status)


class GuiTestPcInputRelay:
    """Relays authenticated stream input to GUI_TEST_PC over loopback only."""

    def __init__(self, base_url: str) -> None:
        parsed = urlparse(str(base_url).rstrip("/"))
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("GUI_TEST_PC live input URL must be a loopback HTTP URL")
        if not parsed.port:
            raise ValueError("GUI_TEST_PC live input URL must include a port")
        self.base_url = str(base_url).rstrip("/")

    def health(self) -> dict[str, object]:
        payload = self._request("/health", method="GET", timeout=1.0)
        if (
            payload.get("enabled") is not True
            or payload.get("execution_owner") != "GUI_TEST_PC"
            or payload.get("relayed_to") != "GUI_TEST_PC"
        ):
            raise GuiTestPcInputError("GUI_TEST_PC live input health identity is invalid")
        result = dict(payload)
        result["token_required"] = True
        return result

    def handle(self, payload: dict[str, object]) -> dict[str, object]:
        forwarded = dict(payload)
        forwarded["host_received_at_ms"] = int(time.time() * 1000)
        result = self._request("/input", method="POST", payload=forwarded, timeout=5.0)
        if (
            result.get("ok") is not True
            or result.get("execution_owner") != "GUI_TEST_PC"
            or result.get("relayed_to") != "GUI_TEST_PC"
        ):
            raise GuiTestPcInputError("GUI_TEST_PC live input response identity is invalid")
        return result

    def _request(
        self,
        path: str,
        *,
        method: str,
        payload: dict[str, object] | None = None,
        timeout: float,
    ) -> dict[str, object]:
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(
            self.base_url + path,
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                error_payload = json.loads(raw)
                message = str(error_payload.get("error") or f"HTTP {exc.code}")
            except json.JSONDecodeError:
                message = f"GUI_TEST_PC live input failed: HTTP {exc.code}"
            raise GuiTestPcInputError(message, status=exc.code) from exc
        except (OSError, URLError) as exc:
            raise GuiTestPcInputError(f"GUI_TEST_PC live input is unavailable: {exc}") from exc
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GuiTestPcInputError("GUI_TEST_PC live input returned invalid JSON") from exc
        if not isinstance(decoded, dict):
            raise GuiTestPcInputError("GUI_TEST_PC live input response must be a JSON object")
        return decoded


class StreamPublisherController:
    def __init__(
        self,
        *,
        ffmpeg: str,
        encoder: str,
        width: int,
        height: int,
        fps: int,
        bitrate_kbps: int,
        mediamtx_api: str,
        cache_size: int = 3,
    ) -> None:
        self.ffmpeg = str(Path(ffmpeg).resolve())
        self.encoder = encoder
        self.width = int(width)
        self.height = int(height)
        self.fps = int(fps)
        self.bitrate_kbps = int(bitrate_kbps)
        self.mediamtx_api = mediamtx_api.rstrip("/")
        self.cache_size = max(1, int(cache_size))
        self._lock = threading.Lock()
        self._processes: dict[int, subprocess.Popen[bytes]] = {}
        self._stdout_handles: dict[int, object] = {}
        self._stderr_handles: dict[int, object] = {}
        self._last_used: dict[int, float] = {}
        self._active_slot: int | None = None
        self._activated_at: str | None = None
        self._last_activation_ms: int | None = None
        self._state_path = RUNTIME / "active_publisher.json"

    def _path_name(self, slot: int) -> str:
        return f"slot{slot:02d}"

    def _path_online(self, slot: int) -> bool:
        url = f"{self.mediamtx_api}/v3/paths/get/{self._path_name(slot)}"
        try:
            with urlopen(url, timeout=0.25) as response:
                return response.status == HTTPStatus.OK
        except (HTTPError, URLError, TimeoutError, OSError):
            return False

    def _publisher_args(self, identity: dict[str, object]) -> list[str]:
        hwnd = int(identity["hwnd"])
        capture = (
            f"gfxcapture=hwnd={hwnd}:capture_cursor=0:capture_border=0:"
            f"max_framerate={self.fps}:resize_mode=scale"
        )
        expected = identity.get("capture_physical_expected") or {}
        expected_width = int(expected.get("w") or 0) if isinstance(expected, dict) else 0
        expected_height = int(expected.get("h") or 0) if isinstance(expected, dict) else 0
        pixel_format = "nv12" if self.encoder in {"nvenc", "mf"} else "yuv420p"
        video_filter = (
            f"hwdownload,format=bgra,scale={self.width}:{self.height}:"
            f"flags=bilinear,format={pixel_format}"
        )
        args = [
            self.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "info",
            "-f",
            "lavfi",
            "-i",
            capture,
            "-an",
        ]
        if self.encoder == "nvenc":
            args += [
                "-vf",
                video_filter,
                "-c:v",
                "h264_nvenc",
                "-preset",
                "p1",
                "-tune",
                "ull",
                "-rc",
                "cbr",
            ]
        elif self.encoder == "mf":
            if expected_width != self.width or expected_height != self.height:
                args += ["-vf", video_filter]
            args += [
                "-c:v",
                "h264_mf",
                "-hw_encoding",
                "1",
                "-rate_control",
                "cbr",
                "-scenario",
                "display_remoting",
                "-bf",
                "0",
            ]
        else:
            args += [
                "-vf",
                video_filter,
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-tune",
                "zerolatency",
            ]
        args += [
            "-fps_mode",
            "cfr",
            "-r",
            str(self.fps),
            "-b:v",
            f"{self.bitrate_kbps}k",
            "-maxrate",
            f"{self.bitrate_kbps}k",
            "-bufsize",
            f"{max(250, self.bitrate_kbps // 5)}k",
            "-g",
            str(max(1, self.fps // 2)),
        ]
        if self.encoder != "mf":
            args += ["-keyint_min", str(max(1, self.fps // 2)), "-sc_threshold", "0"]
        args += [
            "-f",
            "rtsp",
            "-rtsp_transport",
            "tcp",
            f"rtsp://127.0.0.1:8554/{self._path_name(int(identity['slot']))}",
        ]
        return args

    def _write_state(self) -> None:
        payload = self.status()
        temp_path = self._state_path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temp_path.replace(self._state_path)

    def _log_tail(self, slot: int, lines: int = 20) -> str:
        path = RUNTIME / f"{self._path_name(slot)}.ffmpeg.err.log"
        try:
            return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
        except OSError:
            return ""

    def _stop_slot_locked(self, slot: int) -> None:
        process = self._processes.pop(slot, None)
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1.0)
        for handles in (self._stdout_handles, self._stderr_handles):
            handle = handles.pop(slot, None)
            if handle:
                handle.close()
        self._last_used.pop(slot, None)
        if self._active_slot == slot:
            self._active_slot = None
            self._activated_at = None

    def _stop_locked(self) -> None:
        for slot in list(self._processes):
            self._stop_slot_locked(slot)
        self._active_slot = None
        self._activated_at = None
        self._state_path.unlink(missing_ok=True)

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def status(self) -> dict[str, object]:
        warm_slots = sorted(
            slot for slot, process in self._processes.items() if process.poll() is None
        )
        active_process = self._processes.get(self._active_slot or 0)
        alive = active_process is not None and active_process.poll() is None
        return {
            "ok": True,
            "mode": "warm_publisher_cache",
            "encoder": self.encoder,
            "cache_size": self.cache_size,
            "warm_slots": warm_slots,
            "active_slot": self._active_slot if alive else None,
            "publisher_pid": active_process.pid if alive and active_process else None,
            "publisher_alive": alive,
            "publishers": [
                {"slot": slot, "pid": self._processes[slot].pid}
                for slot in warm_slots
            ],
            "activated_at": self._activated_at if alive else None,
            "last_activation_ms": self._last_activation_ms,
        }

    def _validate_slot(self, slot: int) -> dict[str, object]:
        identity = source_identity(slot)
        if not identity.get("ok"):
            raise StreamPublisherError(str(identity.get("error") or f"slot {slot} is unavailable"))
        if identity.get("aspect_is_16_9") is not True:
            raise StreamPublisherError(f"slot {slot} is not 16:9")
        return identity

    def _start_slot_locked(
        self,
        slot: int,
        identity: dict[str, object],
    ) -> tuple[bool, int]:
        process = self._processes.get(slot)
        if process is not None and process.poll() is None and self._path_online(slot):
            self._last_used[slot] = time.monotonic()
            return True, 0
        if process is not None:
            self._stop_slot_locked(slot)

        started = time.perf_counter()
        path_name = self._path_name(slot)
        stdout_handle = (RUNTIME / f"{path_name}.ffmpeg.out.log").open("wb")
        stderr_handle = (RUNTIME / f"{path_name}.ffmpeg.err.log").open("wb")
        process = subprocess.Popen(
            self._publisher_args(identity),
            cwd=str(ROOT),
            stdout=stdout_handle,
            stderr=stderr_handle,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self._processes[slot] = process
        self._stdout_handles[slot] = stdout_handle
        self._stderr_handles[slot] = stderr_handle
        self._last_used[slot] = time.monotonic()

        deadline = time.perf_counter() + 3.0
        while time.perf_counter() < deadline:
            if process.poll() is not None:
                tail = self._log_tail(slot)
                self._stop_slot_locked(slot)
                raise StreamPublisherError(
                    f"slot {slot} publisher exited during activation"
                    + (f": {tail}" if tail else "")
                )
            if self._path_online(slot):
                return False, round((time.perf_counter() - started) * 1000)
            time.sleep(0.04)

        tail = self._log_tail(slot)
        self._stop_slot_locked(slot)
        raise StreamPublisherError(
            f"slot {slot} publisher did not reach MediaMTX within 3000 ms"
            + (f": {tail}" if tail else "")
        )

    def _prune_locked(self, keep: set[int]) -> None:
        while len(self._processes) > self.cache_size:
            candidates = [slot for slot in self._processes if slot not in keep]
            if not candidates:
                candidates = [slot for slot in self._processes if slot != self._active_slot]
            if not candidates:
                break
            victim = min(candidates, key=lambda slot: self._last_used.get(slot, 0.0))
            self._stop_slot_locked(victim)

    def activate(self, slot: int) -> dict[str, object]:
        identity = self._validate_slot(slot)

        with self._lock:
            reused, elapsed_ms = self._start_slot_locked(slot, identity)
            self._active_slot = slot
            self._last_used[slot] = time.monotonic()
            self._activated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self._last_activation_ms = elapsed_ms
            self._prune_locked({slot})
            self._write_state()
            return {**self.status(), "reused": reused, "activation_ms": elapsed_ms}

    def prewarm(self, slots: list[int]) -> dict[str, object]:
        ordered_slots = list(dict.fromkeys(slots))
        if not ordered_slots:
            raise StreamPublisherError("prewarm requires at least one slot")
        if len(ordered_slots) > self.cache_size:
            raise StreamPublisherError(
                f"prewarm accepts at most {self.cache_size} slots"
            )
        identities = {slot: self._validate_slot(slot) for slot in ordered_slots}
        timings: dict[str, int] = {}
        reused_slots: list[int] = []
        with self._lock:
            keep = set(ordered_slots)
            for existing in list(self._processes):
                if existing not in keep and existing != self._active_slot:
                    self._stop_slot_locked(existing)
            for slot in ordered_slots:
                reused, elapsed_ms = self._start_slot_locked(slot, identities[slot])
                timings[str(slot)] = elapsed_ms
                if reused:
                    reused_slots.append(slot)
            self._prune_locked(keep)
            self._write_state()
            return {
                **self.status(),
                "requested_slots": ordered_slots,
                "reused_slots": reused_slots,
                "activation_ms_by_slot": timings,
            }


class Handler(BaseHTTPRequestHandler):
    slots = list(range(1, 16))
    input_token = ""
    input_controller: PicoStreamInputController | None = None
    input_relay: GuiTestPcInputRelay | None = None
    publisher_controller: StreamPublisherController | None = None

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _json(self, payload: dict[str, object], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/oplink-test":
            path = "/"
        elif path.startswith("/oplink-test/"):
            path = path.removeprefix("/oplink-test")

        if path in ("/", "/api/v1/health"):
            payload = sources_payload(self.slots)
            payload["input"] = self._input_status()
            payload["service"] = "oplink-pc-no-zego-slots-1-15"
            payload["all_sources_ready"] = all(item["ok"] for item in payload["sources"])
            self._json(payload)
            return
        if path == "/api/v1/sources":
            payload = sources_payload(self.slots)
            payload["input"] = self._input_status()
            self._json(payload)
            return
        if path == "/api/v1/active":
            if not self.publisher_controller:
                self._json(
                    {"ok": False, "error": "stream publisher controller is disabled"},
                    HTTPStatus.SERVICE_UNAVAILABLE,
                )
            else:
                self._json(self.publisher_controller.status())
            return
        self.send_error(HTTPStatus.NOT_FOUND, "not found")

    def _input_status(self) -> dict[str, object]:
        if self.input_relay is not None:
            try:
                return self.input_relay.health()
            except GuiTestPcInputError as exc:
                return {
                    "enabled": False,
                    "token_required": True,
                    "report_mode": "gui_test_pc_unavailable",
                    "measurement": "disabled",
                    "execution_owner": "GUI_TEST_PC",
                    "relayed_to": "GUI_TEST_PC",
                    "error": str(exc),
                }
        return dict(load_runtime_state().get("input", {"enabled": False}))

    def _read_json_body(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 16_384:
            raise ValueError("invalid request body length")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return payload

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path.startswith("/oplink-test/"):
            path = path.removeprefix("/oplink-test")
        if path == "/api/v1/activate":
            if not self.publisher_controller:
                self._json(
                    {"ok": False, "error": "stream publisher controller is disabled"},
                    HTTPStatus.SERVICE_UNAVAILABLE,
                )
                return
            try:
                payload = self._read_json_body()
                slot = int(payload.get("slot") or 0)
                if slot not in self.slots:
                    raise ValueError(f"slot must be one of {self.slots}")
                self._json(self.publisher_controller.activate(slot))
            except (ValueError, json.JSONDecodeError, StreamPublisherError) as exc:
                self._json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if path == "/api/v1/prewarm":
            if not self.publisher_controller:
                self._json(
                    {"ok": False, "error": "stream publisher controller is disabled"},
                    HTTPStatus.SERVICE_UNAVAILABLE,
                )
                return
            try:
                payload = self._read_json_body()
                requested = payload.get("slots")
                if not isinstance(requested, list):
                    raise ValueError("slots must be an array")
                slots = [int(slot) for slot in requested]
                if any(slot not in self.slots for slot in slots):
                    raise ValueError(f"slots must be selected from {self.slots}")
                self._json(self.publisher_controller.prewarm(slots))
            except (ValueError, json.JSONDecodeError, StreamPublisherError) as exc:
                self._json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if path != "/api/v1/input":
            self.send_error(HTTPStatus.NOT_FOUND, "not found")
            return
        if (not self.input_controller and not self.input_relay) or not self.input_token:
            self._json({"ok": False, "error": "Pico input is disabled"}, HTTPStatus.SERVICE_UNAVAILABLE)
            return
        authorization = self.headers.get("Authorization", "")
        if authorization != f"Bearer {self.input_token}":
            self._json({"ok": False, "error": "invalid pairing token"}, HTTPStatus.UNAUTHORIZED)
            return
        try:
            payload = self._read_json_body()
            slot = int(payload.get("slot") or 0)
            if self.input_relay is not None:
                result = self.input_relay.handle(payload)
            elif self.input_controller is not None:
                result = self.input_controller.handle(payload, source_identity(slot))
            else:
                raise PicoStreamInputError("Pico input is disabled")
            self._json(result)
        except GuiTestPcInputError as exc:
            self._json({"ok": False, "error": str(exc)}, HTTPStatus(exc.status))
        except (ValueError, json.JSONDecodeError, PicoStreamInputError) as exc:
            self._json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)


def main() -> int:
    parser = argparse.ArgumentParser(description="OPLINK_PC slots 1-15 stream metadata service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5110)
    parser.add_argument("--slots", default=",".join(str(slot) for slot in range(1, 16)))
    parser.add_argument("--probe", type=int)
    parser.add_argument("--pico-config")
    parser.add_argument("--input-token-file")
    parser.add_argument("--gui-input-url")
    parser.add_argument("--pico-health", action="store_true")
    parser.add_argument("--ffmpeg")
    parser.add_argument("--encoder", choices=("nvenc", "mf", "x264"), default="x264")
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--bitrate-kbps", type=int, default=6000)
    parser.add_argument("--publisher-cache-size", type=int, default=3)
    parser.add_argument("--mediamtx-api", default="http://127.0.0.1:9997")
    args = parser.parse_args()
    slots = [int(value.strip()) for value in args.slots.split(",") if value.strip()]
    if args.probe is not None:
        print(json.dumps(source_identity(args.probe), separators=(",", ":")))
        return 0
    controller = PicoStreamInputController(args.pico_config) if args.pico_config else None
    if args.pico_health:
        if controller is None:
            raise SystemExit("--pico-health requires --pico-config")
        print(json.dumps(controller.health(), separators=(",", ":")))
        return 0
    Handler.slots = slots
    Handler.input_controller = controller
    Handler.input_relay = GuiTestPcInputRelay(args.gui_input_url) if args.gui_input_url else None
    if args.input_token_file:
        Handler.input_token = Path(args.input_token_file).read_text(encoding="utf-8").strip()
    publisher_controller = None
    if args.ffmpeg:
        publisher_controller = StreamPublisherController(
            ffmpeg=args.ffmpeg,
            encoder=args.encoder,
            width=args.width,
            height=args.height,
            fps=args.fps,
            bitrate_kbps=args.bitrate_kbps,
            mediamtx_api=args.mediamtx_api,
            cache_size=args.publisher_cache_size,
        )
        Handler.publisher_controller = publisher_controller
        atexit.register(publisher_controller.stop)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Metadata service listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        if publisher_controller:
            publisher_controller.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
