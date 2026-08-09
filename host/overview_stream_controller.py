from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from http import HTTPStatus
from pathlib import Path
from typing import BinaryIO, Callable
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


class OverviewStreamError(RuntimeError):
    pass


class OverviewStreamController:
    """Starts the 15-window overview only while Slot 16 is selected."""

    def __init__(
        self,
        *,
        ffmpeg: str | os.PathLike[str],
        encoder: str,
        width: int,
        height: int,
        fps: int,
        bitrate_kbps: int,
        mediamtx_api: str,
        identity_provider: Callable[[int], dict[str, object]],
        runtime_dir: str | os.PathLike[str],
        path_name: str = "oplink_overview",
        startup_timeout_seconds: float = 5.0,
    ) -> None:
        if width % 4 != 0 or height % 4 != 0:
            raise ValueError("overview output dimensions must be divisible by four")
        self.ffmpeg = str(Path(ffmpeg).resolve())
        self.encoder = encoder
        self.width = int(width)
        self.height = int(height)
        self.fps = max(1, int(fps))
        self.bitrate_kbps = max(250, int(bitrate_kbps))
        self.mediamtx_api = mediamtx_api.rstrip("/")
        self.identity_provider = identity_provider
        self.runtime_dir = Path(runtime_dir).resolve()
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.path_name = path_name
        self.startup_timeout_seconds = max(1.0, float(startup_timeout_seconds))
        self._lock = threading.RLock()
        self._process: subprocess.Popen[bytes] | None = None
        self._stdout: BinaryIO | None = None
        self._stderr: BinaryIO | None = None
        self._started_at: str | None = None
        self._last_activation_ms: int | None = None
        self._last_viewer_heartbeat: float | None = None
        self._shutdown = threading.Event()
        self._idle_thread = threading.Thread(
            target=self._idle_monitor,
            name="oplink-overview-viewer-idle-monitor",
            daemon=True,
        )
        self._idle_thread.start()

    @property
    def whep_path(self) -> str:
        return self.path_name

    def _path_online(self) -> bool:
        url = f"{self.mediamtx_api}/v3/paths/get/{self.path_name}"
        try:
            with urlopen(url, timeout=0.35) as response:
                return response.status == HTTPStatus.OK
        except (HTTPError, URLError, TimeoutError, OSError):
            return False

    def _source_identities(self) -> list[dict[str, object]]:
        identities = [self.identity_provider(slot) for slot in range(1, 16)]
        unavailable = [
            {
                "slot": slot,
                "error": identity.get("error") or "source is not ready",
            }
            for slot, identity in enumerate(identities, start=1)
            if not identity.get("ok") or identity.get("aspect_is_16_9") is not True
        ]
        if unavailable:
            raise OverviewStreamError(f"overview sources are not ready: {unavailable}")
        return identities

    def viewer_update(self, state: str, slot: int | None) -> None:
        if state == "active" and slot == 16:
            with self._lock:
                self._last_viewer_heartbeat = time.monotonic()
            return
        if state in {"active", "background", "leave"}:
            self.stop()

    def _idle_monitor(self) -> None:
        while not self._shutdown.wait(1.0):
            with self._lock:
                process_alive = self._process is not None and self._process.poll() is None
                heartbeat = self._last_viewer_heartbeat
            if process_alive and heartbeat is not None and time.monotonic() - heartbeat > 15.0:
                self.stop()

    def _ffmpeg_args(self, identities: list[dict[str, object]]) -> list[str]:
        args = [self.ffmpeg, "-hide_banner", "-loglevel", "info"]
        for identity in identities:
            capture = (
                f"gfxcapture=hwnd={int(identity['hwnd'])}:capture_cursor=0:"
                f"capture_border=0:max_framerate={self.fps}:resize_mode=scale"
            )
            args += ["-f", "lavfi", "-i", capture]

        tile_width = self.width // 4
        tile_height = self.height // 4
        filters = []
        labels = []
        layout = []
        for index in range(15):
            label = f"v{index}"
            labels.append(f"[{label}]")
            x = (index % 4) * tile_width
            y = (index // 4) * tile_height
            layout.append(f"{x}_{y}")
            filters.append(
                f"[{index}:v]hwdownload,format=bgra,"
                f"scale={tile_width}:{tile_height}:flags=bilinear[{label}]"
            )
        pixel_format = "nv12" if self.encoder in {"nvenc", "mf"} else "yuv420p"
        filters.append(
            f"{''.join(labels)}xstack=inputs=15:layout={'|'.join(layout)}:fill=black[grid]"
        )
        filters.append(
            f"[grid]pad={self.width}:{self.height}:0:0:black,format={pixel_format}[out]"
        )
        args += ["-filter_complex", ";".join(filters), "-map", "[out]", "-an"]

        if self.encoder == "nvenc":
            args += [
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
            f"{max(250, self.bitrate_kbps // 4)}k",
            "-g",
            str(max(1, self.fps)),
        ]
        if self.encoder != "mf":
            args += ["-keyint_min", str(max(1, self.fps)), "-sc_threshold", "0"]
        args += [
            "-f",
            "rtsp",
            "-rtsp_transport",
            "tcp",
            f"rtsp://127.0.0.1:8554/{self.path_name}",
        ]
        return args

    def _log_tail(self, lines: int = 30) -> str:
        path = self.runtime_dir / "overview.ffmpeg.err.log"
        try:
            return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
        except OSError:
            return ""

    def activate(self) -> dict[str, object]:
        with self._lock:
            if self._process is not None and self._process.poll() is None and self._path_online():
                self._last_activation_ms = 0
                return {**self.status(), "reused": True, "activation_ms": 0}

            self._stop_locked()
            identities = self._source_identities()
            started = time.perf_counter()
            self._stdout = (self.runtime_dir / "overview.ffmpeg.out.log").open("wb")
            self._stderr = (self.runtime_dir / "overview.ffmpeg.err.log").open("wb")
            self._process = subprocess.Popen(
                self._ffmpeg_args(identities),
                cwd=str(self.runtime_dir),
                stdout=self._stdout,
                stderr=self._stderr,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self._last_viewer_heartbeat = time.monotonic()
            deadline = time.perf_counter() + self.startup_timeout_seconds
            while time.perf_counter() < deadline:
                if self._process.poll() is not None:
                    tail = self._log_tail()
                    self._stop_locked()
                    raise OverviewStreamError(
                        "overview publisher exited during activation"
                        + (f": {tail}" if tail else "")
                    )
                if self._path_online():
                    elapsed_ms = round((time.perf_counter() - started) * 1000)
                    self._started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    self._last_activation_ms = elapsed_ms
                    self._write_state()
                    return {**self.status(), "reused": False, "activation_ms": elapsed_ms}
                time.sleep(0.05)

            tail = self._log_tail()
            self._stop_locked()
            raise OverviewStreamError(
                f"overview publisher did not reach MediaMTX within {self.startup_timeout_seconds:.1f}s"
                + (f": {tail}" if tail else "")
            )

    def _write_state(self) -> None:
        path = self.runtime_dir / "overview_publisher.json"
        temp_path = path.with_suffix(".tmp")
        temp_path.write_text(json.dumps(self.status(), ensure_ascii=False), encoding="utf-8")
        temp_path.replace(path)

    def status(self) -> dict[str, object]:
        with self._lock:
            alive = self._process is not None and self._process.poll() is None
            return {
                "ok": True,
                "mode": "overview_stream",
                "stream_mode": "overview",
                "active_slot": 16 if alive else None,
                "publisher_pid": self._process.pid if alive and self._process else None,
                "publisher_executable": self.ffmpeg,
                "publisher_alive": alive,
                "whep_path": self.path_name,
                "whep_endpoint_path": f"{self.path_name}/whep",
                "profile": {
                    "encoded": {"w": self.width, "h": self.height},
                    "fps": self.fps,
                    "bitrate_kbps": self.bitrate_kbps,
                },
                "started_at": self._started_at if alive else None,
                "last_activation_ms": self._last_activation_ms,
                "viewer_heartbeat_age_ms": (
                    round((time.monotonic() - self._last_viewer_heartbeat) * 1000)
                    if self._last_viewer_heartbeat is not None
                    else None
                ),
            }

    def _stop_locked(self) -> None:
        process = self._process
        self._process = None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)
        for name in ("_stdout", "_stderr"):
            handle = getattr(self, name)
            if handle is not None:
                handle.close()
                setattr(self, name, None)
        self._started_at = None
        self._last_viewer_heartbeat = None

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def close(self) -> None:
        self._shutdown.set()
        self.stop()
        if self._idle_thread.is_alive() and threading.current_thread() is not self._idle_thread:
            self._idle_thread.join(timeout=2.0)
