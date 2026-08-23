from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from collections.abc import Callable
from http import HTTPStatus
from pathlib import Path
from typing import BinaryIO
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from native_capture_router_process import (
    NativeCaptureRouterError,
    NativeCaptureRouterProcess,
    RouterSwitchResult,
)
from slot_limits import MAX_SLOT, MIN_SLOT


class NativeSingleStreamError(RuntimeError):
    pass


class NativeSingleStreamController:
    """Own one persistent WGC router and one persistent FFmpeg publisher."""

    def __init__(
        self,
        *,
        router_exe: str | os.PathLike[str],
        ffmpeg: str | os.PathLike[str],
        encoder: str,
        width: int,
        height: int,
        fps: int,
        bitrate_kbps: int,
        mediamtx_api: str,
        identity_provider: Callable[[int], dict[str, object]],
        runtime_dir: str | os.PathLike[str],
        path_name: str = "oplink_active",
        viewer_idle_timeout_seconds: float = 15.0,
        pipeline_start_timeout: float = 5.0,
        switch_timeout: float = 1.0,
        state_path: str | os.PathLike[str] | None = None,
        path_probe: Callable[[], bool] | None = None,
        whep_session_counter: Callable[[], int | None] | None = None,
        popen_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        router_factory: Callable[..., NativeCaptureRouterProcess] = (
            NativeCaptureRouterProcess
        ),
    ) -> None:
        if encoder not in {"nvenc", "mf", "x264"}:
            raise ValueError("encoder must be nvenc, mf, or x264")
        if width <= 0 or height <= 0 or fps <= 0 or bitrate_kbps <= 0:
            raise ValueError("stream profile values must be positive")
        if not path_name or any(character in path_name for character in "/\\"):
            raise ValueError("path_name must be one MediaMTX path segment")
        if pipeline_start_timeout <= 0 or switch_timeout <= 0:
            raise ValueError("timeouts must be positive")

        self.router_exe = str(Path(router_exe).resolve())
        self.ffmpeg = str(Path(ffmpeg).resolve())
        self.encoder = encoder
        self.width = int(width)
        self.height = int(height)
        self.fps = int(fps)
        self.bitrate_kbps = int(bitrate_kbps)
        self.mediamtx_api = mediamtx_api.rstrip("/")
        self.identity_provider = identity_provider
        self.runtime_dir = Path(runtime_dir).resolve()
        self.path_name = path_name
        self.viewer_idle_timeout_seconds = max(
            5.0, float(viewer_idle_timeout_seconds)
        )
        self.pipeline_start_timeout = float(pipeline_start_timeout)
        self.switch_timeout = float(switch_timeout)
        self.state_path = (
            Path(state_path).resolve()
            if state_path is not None
            else self.runtime_dir / "native_single_publisher.json"
        )
        self._popen_factory = popen_factory
        self._router_factory = router_factory
        self._path_probe = path_probe
        self._whep_session_counter = whep_session_counter

        self._lock = threading.RLock()
        self._viewer_lock = threading.Lock()
        self._shutdown = threading.Event()
        self._router: NativeCaptureRouterProcess | None = None
        self._ffmpeg_process: subprocess.Popen[bytes] | None = None
        self._ffmpeg_stdout: BinaryIO | None = None
        self._ffmpeg_stderr: BinaryIO | None = None
        self._active_slot: int | None = None
        self._activated_at: str | None = None
        self._last_activation_ms: int | None = None
        self._last_switch: RouterSwitchResult | None = None
        self._viewer_state = "never_connected"
        self._viewer_slot: int | None = None
        self._last_viewer_heartbeat: float | None = None
        self._idle_thread = threading.Thread(
            target=self._idle_monitor,
            name="oplink-native-single-viewer-idle-monitor",
            daemon=True,
        )
        self._idle_thread.start()

    @property
    def whep_path(self) -> str:
        return self.path_name

    def _ffmpeg_args(self) -> list[str]:
        args = [
            self.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "info",
            "-f",
            "rawvideo",
            "-pixel_format",
            "bgra",
            "-video_size",
            f"{self.width}x{self.height}",
            "-framerate",
            str(self.fps),
            "-i",
            "pipe:0",
            "-an",
        ]
        if self.encoder == "nvenc":
            args += [
                "-vf",
                "format=nv12",
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
            args += [
                "-vf",
                "format=nv12",
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
                "format=yuv420p",
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
            args += [
                "-keyint_min",
                str(max(1, self.fps // 2)),
                "-sc_threshold",
                "0",
            ]
        args += [
            "-f",
            "rtsp",
            "-rtsp_transport",
            "tcp",
            f"rtsp://127.0.0.1:8554/{self.path_name}",
        ]
        return args

    def _path_online(self) -> bool:
        if self._path_probe is not None:
            return bool(self._path_probe())
        url = f"{self.mediamtx_api}/v3/paths/get/{self.path_name}"
        try:
            with urlopen(url, timeout=0.35) as response:
                if response.status != HTTPStatus.OK:
                    return False
                payload = json.load(response)
            return bool(
                isinstance(payload, dict)
                and payload.get("ready")
                and payload.get("online")
            )
        except (
            HTTPError,
            URLError,
            TimeoutError,
            OSError,
            json.JSONDecodeError,
        ):
            return False

    def _whep_session_count(self) -> int | None:
        if self._whep_session_counter is not None:
            return self._whep_session_counter()
        url = f"{self.mediamtx_api}/v3/webrtcsessions/list"
        try:
            with urlopen(url, timeout=0.5) as response:
                payload = json.load(response)
            items = payload.get("items") if isinstance(payload, dict) else None
            if not isinstance(items, list):
                return None
            return sum(
                1
                for item in items
                if isinstance(item, dict)
                and str(item.get("path") or item.get("pathName") or "")
                == self.path_name
            )
        except (
            HTTPError,
            URLError,
            TimeoutError,
            OSError,
            json.JSONDecodeError,
        ):
            return None

    def _ffmpeg_log_tail(self, lines: int = 30) -> str:
        path = self.runtime_dir / "native_single.ffmpeg.err.log"
        try:
            return "\n".join(
                path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
            )
        except OSError:
            return ""

    def _pipeline_alive_locked(self) -> bool:
        return bool(
            self._router is not None
            and self._router.running
            and self._ffmpeg_process is not None
            and self._ffmpeg_process.poll() is None
        )

    def _ensure_pipeline_locked(self) -> tuple[bool, int]:
        if self._pipeline_alive_locked() and self._path_online():
            return True, 0

        self._stop_pipeline_locked(remove_state=False)
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        self._ffmpeg_stdout = (
            self.runtime_dir / "native_single.ffmpeg.out.log"
        ).open("wb")
        self._ffmpeg_stderr = (
            self.runtime_dir / "native_single.ffmpeg.err.log"
        ).open("wb")
        creationflags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        )
        try:
            process = self._popen_factory(
                self._ffmpeg_args(),
                cwd=str(self.runtime_dir),
                stdin=subprocess.PIPE,
                stdout=self._ffmpeg_stdout,
                stderr=self._ffmpeg_stderr,
                bufsize=0,
                creationflags=creationflags,
            )
            if process.stdin is None:
                raise NativeSingleStreamError("FFmpeg stdin pipe was not created")
            self._ffmpeg_process = process
            router = self._router_factory(
                [self.router_exe],
                width=self.width,
                height=self.height,
                fps=self.fps,
                ready_timeout=self.pipeline_start_timeout,
                switch_timeout=self.switch_timeout,
                stop_timeout=2.0,
                terminate_timeout=1.0,
                kill_timeout=1.0,
                stdout_handle=process.stdin,
            )
            self._router = router
            router.start()
            process.stdin.close()

            deadline = time.perf_counter() + self.pipeline_start_timeout
            while time.perf_counter() < deadline:
                if process.poll() is not None:
                    tail = self._ffmpeg_log_tail()
                    raise NativeSingleStreamError(
                        "FFmpeg exited while starting the native pipeline"
                        + (f": {tail}" if tail else "")
                    )
                if not router.running:
                    raise NativeSingleStreamError(
                        "native capture router exited while starting the pipeline"
                    )
                if self._path_online():
                    return False, round((time.perf_counter() - started) * 1000)
                time.sleep(0.04)
            tail = self._ffmpeg_log_tail()
            raise NativeSingleStreamError(
                f"MediaMTX path {self.path_name} did not become ready within "
                f"{round(self.pipeline_start_timeout * 1000)} ms"
                + (f": {tail}" if tail else "")
            )
        except BaseException:
            self._stop_pipeline_locked(remove_state=False)
            raise

    @staticmethod
    def _stop_process(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        try:
            process.wait(timeout=2.0)
            return
        except subprocess.TimeoutExpired:
            pass
        process.terminate()
        try:
            process.wait(timeout=1.0)
            return
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1.0)

    def _stop_pipeline_locked(self, *, remove_state: bool = True) -> None:
        router = self._router
        self._router = None
        if router is not None:
            try:
                router.stop(reason="native_single_stop")
            except NativeCaptureRouterError:
                pass

        process = self._ffmpeg_process
        self._ffmpeg_process = None
        if process is not None:
            if process.stdin is not None:
                try:
                    process.stdin.close()
                except OSError:
                    pass
            try:
                self._stop_process(process)
            except (OSError, subprocess.TimeoutExpired):
                pass

        for attribute in ("_ffmpeg_stdout", "_ffmpeg_stderr"):
            handle = getattr(self, attribute)
            setattr(self, attribute, None)
            if handle is not None:
                try:
                    handle.close()
                except OSError:
                    pass

        self._active_slot = None
        self._activated_at = None
        self._last_switch = None
        if remove_state:
            self.state_path.unlink(missing_ok=True)

    def _validate_slot(self, slot: int) -> dict[str, object]:
        if slot < MIN_SLOT or slot > MAX_SLOT:
            raise NativeSingleStreamError(
                f"slot must be between {MIN_SLOT} and {MAX_SLOT}"
            )
        identity = self.identity_provider(slot)
        if not identity.get("ok"):
            raise NativeSingleStreamError(
                str(identity.get("error") or f"slot {slot} is unavailable")
            )
        if identity.get("aspect_is_16_9") is not True:
            raise NativeSingleStreamError(f"slot {slot} is not 16:9")
        hwnd = identity.get("hwnd")
        if isinstance(hwnd, bool) or not isinstance(hwnd, int) or hwnd <= 0:
            raise NativeSingleStreamError(f"slot {slot} has no valid HWND")
        return identity

    def _write_state_locked(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = self.status()
        temp_path = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
        temp_path.replace(self.state_path)

    def activate(self, slot: int) -> dict[str, object]:
        request_started = time.perf_counter()
        identity = self._validate_slot(slot)
        host_resolve_ms = round((time.perf_counter() - request_started) * 1000)
        with self._viewer_lock:
            if self._viewer_state != "background":
                self._viewer_state = "active"
                self._viewer_slot = slot
                self._last_viewer_heartbeat = time.monotonic()

        started = time.perf_counter()
        with self._lock:
            reused, pipeline_start_ms = self._ensure_pipeline_locked()
            router = self._router
            process = self._ffmpeg_process
            if router is None or process is None:
                raise NativeSingleStreamError("native pipeline did not start")
            try:
                result = router.switch(
                    slot=slot,
                    hwnd=int(identity["hwnd"]),
                    timeout=self.switch_timeout,
                )
            except NativeCaptureRouterError as exc:
                if not self._pipeline_alive_locked():
                    self._stop_pipeline_locked()
                raise NativeSingleStreamError(str(exc)) from exc
            if not self._pipeline_alive_locked():
                tail = self._ffmpeg_log_tail()
                self._stop_pipeline_locked()
                raise NativeSingleStreamError(
                    "native pipeline exited during switch"
                    + (f": {tail}" if tail else "")
                )

            self._active_slot = slot
            self._activated_at = time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
            )
            self._last_activation_ms = round(
                (time.perf_counter() - started) * 1000
            )
            self._last_switch = result
            self._write_state_locked()
            return {
                **self.status(),
                "reused": reused,
                "reused_transport": reused,
                "pipeline_start_ms": pipeline_start_ms,
                "activation_ms": self._last_activation_ms,
                "switch_generation": result.generation,
                "host_resolve_ms": host_resolve_ms,
                "capture_first_frame_ms": result.first_frame["elapsed_ms"],
                "encoder_pid": process.pid,
                "first_frame": result.first_frame,
            }

    def prewarm(self, slots: list[int]) -> dict[str, object]:
        ordered_slots = list(dict.fromkeys(slots))
        if not ordered_slots:
            raise NativeSingleStreamError("prewarm requires at least one slot")
        for slot in ordered_slots:
            self._validate_slot(slot)
        return {
            **self.status(),
            "requested_slots": ordered_slots,
            "prewarm_supported": False,
        }

    def retain_only(self, slot: int) -> None:
        if self._active_slot is not None and self._active_slot != slot:
            return

    def viewer_status(self) -> dict[str, object]:
        with self._viewer_lock:
            last_heartbeat = self._last_viewer_heartbeat
            age_ms = (
                round((time.monotonic() - last_heartbeat) * 1000)
                if last_heartbeat is not None
                else None
            )
            return {
                "state": self._viewer_state,
                "slot": self._viewer_slot,
                "heartbeat_age_ms": age_ms,
                "idle_timeout_ms": round(
                    self.viewer_idle_timeout_seconds * 1000
                ),
            }

    def viewer_update(self, state: str, slot: int | None) -> dict[str, object]:
        if state not in {"active", "background", "leave"}:
            raise NativeSingleStreamError(
                "viewer state must be active, background, or leave"
            )
        if state != "leave" and slot is None:
            raise NativeSingleStreamError("viewer slot is required")
        with self._viewer_lock:
            self._viewer_state = state
            self._viewer_slot = slot
            self._last_viewer_heartbeat = time.monotonic()
        if state == "leave":
            with self._lock:
                self._stop_pipeline_locked()
        return {"ok": True, **self.viewer_status()}

    def _idle_monitor(self) -> None:
        while not self._shutdown.wait(1.0):
            with self._viewer_lock:
                last_heartbeat = self._last_viewer_heartbeat
                viewer_state = self._viewer_state
            if (
                last_heartbeat is None
                or time.monotonic() - last_heartbeat
                < self.viewer_idle_timeout_seconds
            ):
                continue
            if viewer_state != "background":
                session_count = self._whep_session_count()
                if session_count is None or session_count > 0:
                    continue
            with self._viewer_lock:
                current_heartbeat = self._last_viewer_heartbeat
                if (
                    current_heartbeat is None
                    or time.monotonic() - current_heartbeat
                    < self.viewer_idle_timeout_seconds
                ):
                    continue
                self._viewer_state = "idle"
            with self._lock:
                self._stop_pipeline_locked()

    def status(self) -> dict[str, object]:
        with self._lock:
            alive = self._pipeline_alive_locked()
            process = self._ffmpeg_process
            router = self._router
            result = self._last_switch
            return {
                "ok": True,
                "mode": "native_single_stream",
                "stream_mode": "native_single",
                "encoder": self.encoder,
                "path": self.path_name,
                "whep_path": self.whep_path,
                "whep_endpoint_path": f"{self.path_name}/whep",
                "profile": {
                    "w": self.width,
                    "h": self.height,
                    "fps": self.fps,
                    "bitrate_kbps": self.bitrate_kbps,
                },
                "active_slot": self._active_slot if alive else None,
                "publisher_pid": process.pid if alive and process else None,
                "encoder_pid": process.pid if alive and process else None,
                "router_pid": router.pid if alive and router else None,
                "publisher_executable": self.ffmpeg,
                "router_executable": self.router_exe,
                "publisher_alive": alive,
                "encoder_alive": bool(
                    process is not None and process.poll() is None
                ),
                "router_alive": bool(router is not None and router.running),
                "generation": router.generation if alive and router else 0,
                "switch_generation": router.generation if alive and router else 0,
                "publishers": (
                    [
                        {
                            "role": "ffmpeg",
                            "pid": process.pid,
                            "executable": self.ffmpeg,
                        },
                        {
                            "role": "router",
                            "pid": router.pid,
                            "executable": self.router_exe,
                        },
                    ]
                    if alive and process and router
                    else []
                ),
                "warm_slots": (
                    [self._active_slot]
                    if alive and self._active_slot is not None
                    else []
                ),
                "activated_at": self._activated_at if alive else None,
                "last_activation_ms": self._last_activation_ms,
                "last_first_frame": result.first_frame if result else None,
                "viewer": self.viewer_status(),
            }

    def stop(self) -> None:
        self._shutdown.set()
        if (
            self._idle_thread.is_alive()
            and threading.current_thread() is not self._idle_thread
        ):
            self._idle_thread.join(timeout=2.0)
        with self._lock:
            self._stop_pipeline_locked()
