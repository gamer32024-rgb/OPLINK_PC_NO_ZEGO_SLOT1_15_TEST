from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import BinaryIO

from slot_limits import MAX_SLOT, MIN_SLOT


class NativeCaptureRouterError(RuntimeError):
    pass


class RouterProtocolError(NativeCaptureRouterError):
    pass


class RouterTimeoutError(NativeCaptureRouterError):
    pass


class RouterProcessExitedError(NativeCaptureRouterError):
    pass


class RouterRemoteError(NativeCaptureRouterError):
    def __init__(self, event: Mapping[str, object]) -> None:
        self.event = dict(event)
        code = str(event.get("code") or "UNKNOWN")
        super().__init__(f"native capture router reported {code}")


@dataclass(frozen=True)
class RouterSwitchResult:
    generation: int
    slot: int
    hwnd: int
    switch_started: dict[str, object]
    first_frame: dict[str, object]


@dataclass(frozen=True)
class _ReaderFailure:
    error: RouterProtocolError


_EVENT_STREAM_EOF = object()
_STOP_REASON = re.compile(r"^[A-Za-z0-9_.:-]+$")


def _require_int(
    event: Mapping[str, object],
    field: str,
    *,
    minimum: int = 0,
) -> int:
    value = event.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise RouterProtocolError(
            f"router event field {field!r} must be an integer >= {minimum}"
        )
    return value


def _require_number(
    event: Mapping[str, object],
    field: str,
    *,
    minimum: float = 0.0,
) -> float:
    value = event.get(field)
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or float(value) < minimum
    ):
        raise RouterProtocolError(
            f"router event field {field!r} must be a number >= {minimum}"
        )
    return float(value)


def _parse_hwnd(value: object) -> int:
    if isinstance(value, bool):
        raise RouterProtocolError("router HWND must be a positive integer")
    if isinstance(value, int):
        hwnd = value
    elif isinstance(value, str):
        try:
            hwnd = int(value, 0)
        except ValueError as exc:
            raise RouterProtocolError("router HWND is not a valid integer") from exc
    else:
        raise RouterProtocolError("router HWND must be a positive integer")
    if hwnd <= 0:
        raise RouterProtocolError("router HWND must be a positive integer")
    return hwnd


def parse_router_event(line: str) -> dict[str, object]:
    text = line.strip()
    if not text:
        raise RouterProtocolError("router emitted an empty stderr line")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RouterProtocolError("router emitted malformed JSON") from exc
    if not isinstance(payload, dict):
        raise RouterProtocolError("router event must be a JSON object")

    event = payload.get("event")
    if not isinstance(event, str) or not event:
        raise RouterProtocolError("router event name must be a non-empty string")

    if event == "ready":
        for field in ("pid", "width", "height", "fps"):
            _require_int(payload, field, minimum=1)
    elif event == "switch_started":
        _require_int(payload, "generation", minimum=1)
        slot = _require_int(payload, "slot", minimum=1)
        if slot > MAX_SLOT:
            raise RouterProtocolError(
                f"router event slot must be between {MIN_SLOT} and {MAX_SLOT}"
            )
        _parse_hwnd(payload.get("hwnd"))
        _require_int(payload, "at_ms", minimum=1)
    elif event == "first_frame":
        _require_int(payload, "generation", minimum=1)
        slot = _require_int(payload, "slot", minimum=1)
        if slot > MAX_SLOT:
            raise RouterProtocolError(
                f"router event slot must be between {MIN_SLOT} and {MAX_SLOT}"
            )
        for field in ("source_w", "source_h", "output_w", "output_h"):
            _require_int(payload, field, minimum=1)
        _require_number(payload, "elapsed_ms", minimum=0.0)
        _require_int(payload, "stdout_frame_index", minimum=1)
    elif event == "started":
        for field in ("width", "height", "fps"):
            _require_int(payload, field, minimum=1)
        if payload.get("format") != "bgra":
            raise RouterProtocolError("router started event format must be bgra")
    elif event == "frame_stats":
        _require_int(payload, "generation", minimum=1)
        slot = _require_int(payload, "slot", minimum=1)
        if slot > MAX_SLOT:
            raise RouterProtocolError(
                f"router event slot must be between {MIN_SLOT} and {MAX_SLOT}"
            )
        _require_number(payload, "fps", minimum=0.0)
        _require_int(payload, "dropped", minimum=0)
    elif event == "error":
        if "generation" in payload:
            _require_int(payload, "generation", minimum=0)
        if "slot" in payload:
            slot = _require_int(payload, "slot", minimum=0)
            if slot > MAX_SLOT:
                raise RouterProtocolError(
                    f"router event slot must be between {MIN_SLOT} and {MAX_SLOT}"
                )
        if not isinstance(payload.get("code"), str) or not payload["code"]:
            raise RouterProtocolError("router error event requires a code")
        if not isinstance(payload.get("recoverable"), bool):
            raise RouterProtocolError(
                "router error event requires a boolean recoverable field"
            )

    return payload


def format_start_command(*, width: int, height: int, fps: int) -> str:
    for value, name in ((width, "width"), (height, "height"), (fps, "fps")):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
    return f"START width={width} height={height} fps={fps} format=bgra"


def format_switch_command(*, generation: int, slot: int, hwnd: int) -> str:
    if (
        isinstance(generation, bool)
        or not isinstance(generation, int)
        or generation < 1
    ):
        raise ValueError("generation must be a positive integer")
    if (
        isinstance(slot, bool)
        or not isinstance(slot, int)
        or slot < MIN_SLOT
        or slot > MAX_SLOT
    ):
        raise ValueError(f"slot must be between {MIN_SLOT} and {MAX_SLOT}")
    if isinstance(hwnd, bool) or not isinstance(hwnd, int) or hwnd <= 0:
        raise ValueError("HWND must be a positive integer")
    return (
        f"SWITCH generation={generation} slot={slot} "
        f"hwnd=0x{hwnd:016x}"
    )


def format_stop_command(reason: str) -> str:
    if not _STOP_REASON.fullmatch(reason):
        raise ValueError(
            "stop reason may contain only letters, digits, dot, colon, underscore, and dash"
        )
    return f"STOP reason={reason}"


class NativeCaptureRouterProcess:
    """Own and supervise one native capture router child process."""

    def __init__(
        self,
        command: Sequence[str | os.PathLike[str]],
        *,
        width: int = 1920,
        height: int = 1080,
        fps: int = 30,
        ready_timeout: float = 5.0,
        switch_timeout: float = 1.0,
        stop_timeout: float = 1.0,
        terminate_timeout: float = 1.0,
        kill_timeout: float = 1.0,
        cwd: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
        stdout_consumer: Callable[[bytes], None] | None = None,
        stdout_path: str | os.PathLike[str] | None = None,
        stdout_handle: BinaryIO | None = None,
        popen_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
    ) -> None:
        if not command:
            raise ValueError("router command must not be empty")
        configured_stdout_targets = sum(
            value is not None
            for value in (stdout_consumer, stdout_path, stdout_handle)
        )
        if configured_stdout_targets > 1:
            raise ValueError(
                "stdout_consumer, stdout_path, and stdout_handle are mutually exclusive"
            )
        if width <= 0 or height <= 0 or fps <= 0:
            raise ValueError("router geometry must be positive")
        for value, name in (
            (ready_timeout, "ready_timeout"),
            (switch_timeout, "switch_timeout"),
            (stop_timeout, "stop_timeout"),
            (terminate_timeout, "terminate_timeout"),
            (kill_timeout, "kill_timeout"),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be positive")

        self.command = tuple(os.fspath(value) for value in command)
        self.width = int(width)
        self.height = int(height)
        self.fps = int(fps)
        self.ready_timeout = float(ready_timeout)
        self.switch_timeout = float(switch_timeout)
        self.stop_timeout = float(stop_timeout)
        self.terminate_timeout = float(terminate_timeout)
        self.kill_timeout = float(kill_timeout)
        self.cwd = os.fspath(cwd) if cwd is not None else None
        self.env = dict(env) if env is not None else None
        self.stdout_consumer = stdout_consumer
        self.stdout_path = os.fspath(stdout_path) if stdout_path is not None else None
        self.stdout_handle = stdout_handle
        self._popen_factory = popen_factory

        self._process: subprocess.Popen[bytes] | None = None
        self._stdout_file: BinaryIO | None = None
        self._last_pid: int | None = None
        self._generation = 0
        self._event_queue: queue.Queue[
            dict[str, object] | _ReaderFailure | object
        ] = queue.Queue()
        self._event_history: deque[dict[str, object]] = deque(maxlen=512)
        self._history_lock = threading.Lock()
        self._reader_threads: list[threading.Thread] = []
        self._lifecycle_lock = threading.RLock()
        self._switch_lock = threading.Lock()
        self._stdin_lock = threading.Lock()
        self.ready_event: dict[str, object] | None = None
        self.started_event: dict[str, object] | None = None
        self.last_stop_stage: str | None = None

    @property
    def pid(self) -> int | None:
        process = self._process
        return process.pid if process is not None else self._last_pid

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def running(self) -> bool:
        process = self._process
        return process is not None and process.poll() is None

    @property
    def event_history(self) -> tuple[dict[str, object], ...]:
        with self._history_lock:
            return tuple(dict(event) for event in self._event_history)

    def start(self) -> dict[str, object]:
        with self._lifecycle_lock:
            if self.running:
                raise NativeCaptureRouterError("native capture router is already running")
            if self._process is not None:
                raise NativeCaptureRouterError(
                    "clean up the previous router child before restarting"
                )

            self._event_queue = queue.Queue()
            self._reader_threads = []
            self._generation = 0
            self.ready_event = None
            self.started_event = None
            self.last_stop_stage = None
            with self._history_lock:
                self._event_history.clear()

            command = [
                *self.command,
                "--width",
                str(self.width),
                "--height",
                str(self.height),
                "--fps",
                str(self.fps),
            ]
            creationflags = (
                getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            )
            stdout_target: int | BinaryIO = subprocess.PIPE
            if self.stdout_path is not None:
                self._stdout_file = open(self.stdout_path, "wb", buffering=0)
                stdout_target = self._stdout_file
            elif self.stdout_handle is not None:
                stdout_target = self.stdout_handle
            try:
                process = self._popen_factory(
                    command,
                    cwd=self.cwd,
                    env=self.env,
                    stdin=subprocess.PIPE,
                    stdout=stdout_target,
                    stderr=subprocess.PIPE,
                    bufsize=0,
                    creationflags=creationflags,
                )
            except BaseException:
                self._close_stdout_file()
                raise
            if (
                process.stdin is None
                or process.stderr is None
                or (
                    self.stdout_path is None
                    and self.stdout_handle is None
                    and process.stdout is None
                )
            ):
                process.kill()
                process.wait()
                self._close_stdout_file()
                raise NativeCaptureRouterError(
                    "native capture router pipes were not created"
                )

            self._process = process
            self._last_pid = process.pid
            self._reader_threads = []
            if process.stdout is not None:
                self._reader_threads.append(
                    threading.Thread(
                    target=self._drain_stdout,
                    args=(process.stdout,),
                    name=f"native-router-stdout-{process.pid}",
                    daemon=True,
                    )
                )
            self._reader_threads.append(
                threading.Thread(
                    target=self._read_stderr,
                    args=(process.stderr,),
                    name=f"native-router-stderr-{process.pid}",
                    daemon=True,
                )
            )
            for thread in self._reader_threads:
                thread.start()

            try:
                ready = self._wait_for_ready(self.ready_timeout)
                if _require_int(ready, "pid", minimum=1) != process.pid:
                    raise RouterProtocolError(
                        "router ready PID does not match the supervised child"
                    )
                for field, expected in (
                    ("width", self.width),
                    ("height", self.height),
                    ("fps", self.fps),
                ):
                    if _require_int(ready, field, minimum=1) != expected:
                        raise RouterProtocolError(
                            f"router ready {field} does not match the launch geometry"
                        )
                self._send_command(
                    format_start_command(
                        width=self.width,
                        height=self.height,
                        fps=self.fps,
                    )
                )
                started = self._wait_for_started(self.ready_timeout)
                for field, expected in (
                    ("width", self.width),
                    ("height", self.height),
                    ("fps", self.fps),
                ):
                    if _require_int(started, field, minimum=1) != expected:
                        raise RouterProtocolError(
                            f"router started {field} does not match the launch geometry"
                        )
                self.ready_event = dict(ready)
                self.started_event = dict(started)
                return dict(ready)
            except BaseException:
                self.stop(reason="start_failed")
                raise

    def switch(
        self,
        *,
        slot: int,
        hwnd: int,
        timeout: float | None = None,
    ) -> RouterSwitchResult:
        wait_timeout = self.switch_timeout if timeout is None else float(timeout)
        if wait_timeout <= 0:
            raise ValueError("switch timeout must be positive")

        with self._switch_lock:
            if (
                not self.running
                or self.ready_event is None
                or self.started_event is None
            ):
                raise NativeCaptureRouterError("native capture router is not ready")
            generation = self._generation + 1
            command = format_switch_command(
                generation=generation,
                slot=slot,
                hwnd=hwnd,
            )
            self._send_command(command)
            self._generation = generation
            return self._wait_for_switch(
                generation=generation,
                slot=slot,
                hwnd=hwnd,
                timeout=wait_timeout,
            )

    def stop(self, *, reason: str = "supervisor_stop") -> str:
        command = format_stop_command(reason)
        with self._lifecycle_lock:
            process = self._process
            if process is None:
                self.last_stop_stage = "not_started"
                return self.last_stop_stage

            stage = "already_exited"
            if process.poll() is None:
                try:
                    self._send_command(command)
                except RouterProcessExitedError:
                    pass
                self._close_stream(process.stdin)

                if self._wait_for_exit(process, self.stop_timeout):
                    stage = "stop"
                else:
                    try:
                        process.terminate()
                    except OSError:
                        pass
                    if self._wait_for_exit(process, self.terminate_timeout):
                        stage = "terminate"
                    else:
                        try:
                            process.kill()
                        except OSError:
                            pass
                        if not self._wait_for_exit(process, self.kill_timeout):
                            raise NativeCaptureRouterError(
                                f"native capture router PID {process.pid} did not exit after kill"
                            )
                        stage = "kill"
            else:
                self._close_stream(process.stdin)

            self._join_reader_threads()
            self._close_stream(process.stdout)
            self._close_stream(process.stderr)
            self._close_stdout_file()
            self._process = None
            self.ready_event = None
            self.started_event = None
            self.last_stop_stage = stage
            return stage

    def __enter__(self) -> NativeCaptureRouterProcess:
        self.start()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.stop()

    def _drain_stdout(self, stdout: BinaryIO) -> None:
        consumer = self.stdout_consumer
        consumer_failed = False
        while True:
            try:
                chunk = stdout.read(64 * 1024)
            except OSError:
                return
            if not chunk:
                return
            if consumer is not None and not consumer_failed:
                try:
                    consumer(chunk)
                except Exception as exc:
                    consumer_failed = True
                    self._event_queue.put(
                        _ReaderFailure(
                            RouterProtocolError(
                                f"router stdout consumer failed: {exc}"
                            )
                        )
                    )

    def _read_stderr(self, stderr: BinaryIO) -> None:
        try:
            while True:
                try:
                    raw_line = stderr.readline()
                except OSError:
                    return
                if not raw_line:
                    return
                try:
                    line = raw_line.decode("utf-8")
                except UnicodeDecodeError as exc:
                    self._event_queue.put(
                        _ReaderFailure(
                            RouterProtocolError(
                                f"router stderr is not valid UTF-8: {exc}"
                            )
                        )
                    )
                    continue
                if not line.strip():
                    continue
                try:
                    event = parse_router_event(line)
                except RouterProtocolError as exc:
                    self._event_queue.put(_ReaderFailure(exc))
                    continue
                with self._history_lock:
                    self._event_history.append(dict(event))
                self._event_queue.put(event)
        finally:
            self._event_queue.put(_EVENT_STREAM_EOF)

    def _wait_for_ready(self, timeout: float) -> dict[str, object]:
        deadline = time.monotonic() + timeout
        event = self._next_event(deadline, "ready")
        if event.get("event") != "ready":
            raise RouterProtocolError("router must emit ready before other events")
        return event

    def _wait_for_started(self, timeout: float) -> dict[str, object]:
        deadline = time.monotonic() + timeout
        event = self._next_event(deadline, "started")
        if event.get("event") == "error":
            raise RouterRemoteError(event)
        if event.get("event") != "started":
            raise RouterProtocolError("router must emit started after START")
        return event

    def _wait_for_switch(
        self,
        *,
        generation: int,
        slot: int,
        hwnd: int,
        timeout: float,
    ) -> RouterSwitchResult:
        deadline = time.monotonic() + timeout
        started: dict[str, object] | None = None

        while True:
            event = self._next_event(deadline, f"generation {generation} first_frame")
            event_name = str(event["event"])
            event_generation = event.get("generation")

            if isinstance(event_generation, int):
                if event_generation < generation:
                    continue
                if event_generation > generation:
                    raise RouterProtocolError(
                        "router emitted a future generation while a switch was pending"
                    )

            if event_name == "error":
                if event_generation is None or event_generation == generation:
                    raise RouterRemoteError(event)
                continue
            if event_name == "switch_started":
                if int(event["slot"]) != slot or _parse_hwnd(event["hwnd"]) != hwnd:
                    raise RouterProtocolError(
                        "switch_started identity does not match the pending switch"
                    )
                if started is not None:
                    raise RouterProtocolError(
                        "router emitted duplicate switch_started events"
                    )
                started = dict(event)
                continue
            if event_name == "first_frame":
                if int(event["slot"]) != slot:
                    raise RouterProtocolError(
                        "first_frame slot does not match the pending switch"
                    )
                if started is None:
                    raise RouterProtocolError(
                        "router emitted first_frame before switch_started"
                    )
                return RouterSwitchResult(
                    generation=generation,
                    slot=slot,
                    hwnd=hwnd,
                    switch_started=started,
                    first_frame=dict(event),
                )
            if event_name == "ready":
                raise RouterProtocolError("router emitted a duplicate ready event")
            if event_name == "stopped":
                raise RouterProcessExitedError(
                    "router stopped while a switch was pending"
                )

    def _next_event(
        self,
        deadline: float,
        waiting_for: str,
    ) -> dict[str, object]:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RouterTimeoutError(f"timed out waiting for router {waiting_for}")
            try:
                item = self._event_queue.get(timeout=min(remaining, 0.05))
            except queue.Empty:
                process = self._process
                if process is not None and process.poll() is not None:
                    raise RouterProcessExitedError(
                        f"router PID {process.pid} exited with code {process.returncode} "
                        f"while waiting for {waiting_for}"
                    )
                continue

            if isinstance(item, _ReaderFailure):
                raise item.error
            if item is _EVENT_STREAM_EOF:
                process = self._process
                if process is not None and process.poll() is not None:
                    raise RouterProcessExitedError(
                        f"router PID {process.pid} exited with code {process.returncode} "
                        f"while waiting for {waiting_for}"
                    )
                continue
            if isinstance(item, dict):
                return item
            raise RouterProtocolError("router event reader returned an invalid item")

    def _send_command(self, command: str) -> None:
        process = self._process
        if process is None or process.poll() is not None or process.stdin is None:
            returncode = process.returncode if process is not None else None
            raise RouterProcessExitedError(
                f"native capture router is not writable (exit code {returncode})"
            )
        payload = (command + "\n").encode("ascii")
        with self._stdin_lock:
            try:
                process.stdin.write(payload)
                process.stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as exc:
                raise RouterProcessExitedError(
                    "native capture router command pipe is closed"
                ) from exc

    @staticmethod
    def _wait_for_exit(process: subprocess.Popen[bytes], timeout: float) -> bool:
        try:
            process.wait(timeout=timeout)
            return True
        except subprocess.TimeoutExpired:
            return False

    def _join_reader_threads(self) -> None:
        for thread in self._reader_threads:
            thread.join(timeout=1.0)
        self._reader_threads = []

    def _close_stdout_file(self) -> None:
        stream = self._stdout_file
        self._stdout_file = None
        self._close_stream(stream)

    @staticmethod
    def _close_stream(stream: object | None) -> None:
        if stream is None:
            return
        try:
            stream.close()  # type: ignore[attr-defined]
        except (OSError, ValueError):
            pass
