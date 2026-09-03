from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
from typing import Any, BinaryIO

from PIL import Image

from .slot_limits import MAX_SLOT, MIN_SLOT


class WgcCaptureError(RuntimeError):
    pass


class WgcFrameProvider:
    """Own one exact-HWND WGC router and expose its latest BGRA frame."""

    def __init__(
        self,
        *,
        executable: str | Path,
        hwnd: int,
        slot: int,
        width: int,
        height: int,
        fps: int = 8,
        startup_timeout_sec: float = 5.0,
        frame_timeout_sec: float = 2.0,
        popen_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
    ) -> None:
        self.executable = Path(executable).resolve()
        self.hwnd = int(hwnd)
        self.slot = int(slot)
        self.width = int(width)
        self.height = int(height)
        self.fps = int(fps)
        self.startup_timeout_sec = float(startup_timeout_sec)
        self.frame_timeout_sec = float(frame_timeout_sec)
        self.frame_size = self.width * self.height * 4
        self._popen_factory = popen_factory
        self._process: subprocess.Popen[bytes] | None = None
        self._events: queue.Queue[dict[str, Any] | BaseException] = queue.Queue()
        self._frame_condition = threading.Condition()
        self._latest_frame: bytes | None = None
        self._frame_version = 0
        self._last_delivered_version = 0
        self._closed = False

        if not self.executable.is_file():
            raise FileNotFoundError(f"WGC capture router not found: {self.executable}")
        if self.hwnd <= 0 or self.slot < MIN_SLOT or self.slot > MAX_SLOT:
            raise ValueError(
                f"WGC capture requires SLOT {MIN_SLOT}-{MAX_SLOT} and a positive HWND"
            )
        if self.width <= 0 or self.height <= 0 or self.fps <= 0:
            raise ValueError("WGC capture geometry and fps must be positive")

    def start(self) -> None:
        if self._process is not None:
            raise WgcCaptureError("WGC capture provider is already started")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        process = self._popen_factory(
            [
                str(self.executable),
                "--width",
                str(self.width),
                "--height",
                str(self.height),
                "--fps",
                str(self.fps),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            creationflags=creationflags,
        )
        if process.stdin is None or process.stdout is None or process.stderr is None:
            process.kill()
            process.wait()
            raise WgcCaptureError("WGC capture router pipes were not created")
        self._process = process
        threading.Thread(
            target=self._read_frames,
            args=(process.stdout,),
            name=f"battle-wgc-frames-{process.pid}",
            daemon=True,
        ).start()
        threading.Thread(
            target=self._read_events,
            args=(process.stderr,),
            name=f"battle-wgc-events-{process.pid}",
            daemon=True,
        ).start()

        try:
            ready = self._wait_event("ready", self.startup_timeout_sec)
            if int(ready.get("width") or 0) != self.width or int(ready.get("height") or 0) != self.height:
                raise WgcCaptureError(f"WGC router ready geometry mismatch: {ready}")
            self._send(f"START width={self.width} height={self.height} fps={self.fps} format=bgra")
            self._wait_event("started", self.startup_timeout_sec)
            self._send(f"SWITCH generation=1 slot={self.slot} hwnd=0x{self.hwnd:016x}")
            self._wait_event(
                "switch_started",
                self.startup_timeout_sec,
                predicate=lambda event: int(event.get("generation") or 0) == 1,
            )
            self._wait_event(
                "first_frame",
                self.startup_timeout_sec,
                predicate=lambda event: int(event.get("generation") or 0) == 1,
            )
            self._wait_for_frame(after_version=0, timeout=self.startup_timeout_sec)
        except BaseException:
            self.close()
            raise

    def capture(self) -> Image.Image:
        if self._process is None or self._process.poll() is not None:
            raise WgcCaptureError("WGC capture router is not running")
        frame, version = self._wait_for_frame(
            after_version=self._last_delivered_version,
            timeout=self.frame_timeout_sec,
        )
        self._last_delivered_version = version
        return Image.frombytes(
            "RGBA",
            (self.width, self.height),
            frame,
            "raw",
            "BGRA",
        ).convert("RGB")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            try:
                self._send("STOP reason=battle_interrupt_done")
                process.wait(timeout=1.0)
            except Exception:
                process.terminate()
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1.0)
        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass

    def __enter__(self) -> "WgcFrameProvider":
        self.start()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def _send(self, command: str) -> None:
        process = self._process
        if process is None or process.stdin is None or process.poll() is not None:
            raise WgcCaptureError(f"cannot send to stopped WGC router: {command}")
        process.stdin.write((command + "\n").encode("ascii"))
        process.stdin.flush()

    def _read_events(self, stderr: BinaryIO) -> None:
        try:
            while True:
                raw = stderr.readline()
                if not raw:
                    return
                try:
                    value = json.loads(raw.decode("utf-8", errors="replace"))
                    if isinstance(value, dict):
                        self._events.put(value)
                except json.JSONDecodeError as exc:
                    self._events.put(WgcCaptureError(f"invalid WGC router event: {exc}"))
                    return
        except BaseException as exc:
            self._events.put(exc)

    def _read_frames(self, stdout: BinaryIO) -> None:
        pending = bytearray()
        try:
            while True:
                chunk = stdout.read(256 * 1024)
                if not chunk:
                    return
                pending.extend(chunk)
                while len(pending) >= self.frame_size:
                    frame = bytes(pending[: self.frame_size])
                    del pending[: self.frame_size]
                    with self._frame_condition:
                        self._latest_frame = frame
                        self._frame_version += 1
                        self._frame_condition.notify_all()
        except BaseException as exc:
            self._events.put(exc)
            with self._frame_condition:
                self._frame_condition.notify_all()

    def _wait_event(
        self,
        name: str,
        timeout: float,
        *,
        predicate: Callable[[dict[str, Any]], bool] | None = None,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + max(0.1, float(timeout))
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise WgcCaptureError(f"timed out waiting for WGC router event {name}")
            try:
                item = self._events.get(timeout=min(0.1, remaining))
            except queue.Empty:
                process = self._process
                if process is not None and process.poll() is not None:
                    raise WgcCaptureError(f"WGC router exited with code {process.returncode}")
                continue
            if isinstance(item, BaseException):
                raise WgcCaptureError(f"WGC router reader failed: {item}") from item
            if item.get("event") == "error":
                raise WgcCaptureError(
                    f"WGC router error {item.get('code')}: {item.get('message') or item}"
                )
            if item.get("event") == name and (predicate is None or predicate(item)):
                return item

    def _wait_for_frame(self, *, after_version: int, timeout: float) -> tuple[bytes, int]:
        deadline = time.monotonic() + max(0.1, float(timeout))
        with self._frame_condition:
            while self._latest_frame is None or self._frame_version <= after_version:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise WgcCaptureError("timed out waiting for a fresh WGC frame")
                process = self._process
                if process is not None and process.poll() is not None:
                    raise WgcCaptureError(f"WGC router exited with code {process.returncode}")
                self._frame_condition.wait(timeout=min(0.1, remaining))
            return self._latest_frame, self._frame_version
