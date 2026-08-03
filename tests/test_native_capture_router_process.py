from __future__ import annotations

import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


HOST = Path(__file__).resolve().parents[1] / "host"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "fake_native_capture_router.py"
if str(HOST) not in sys.path:
    sys.path.insert(0, str(HOST))

from native_capture_router_process import (  # noqa: E402
    NativeCaptureRouterProcess,
    RouterProcessExitedError,
    RouterProtocolError,
    RouterRemoteError,
    RouterTimeoutError,
    format_start_command,
    format_stop_command,
    format_switch_command,
    parse_router_event,
)


class NativeCaptureRouterProtocolTests(unittest.TestCase):
    def test_formats_exact_line_protocol(self) -> None:
        self.assertEqual(
            format_start_command(width=1920, height=1080, fps=30),
            "START width=1920 height=1080 fps=30 format=bgra",
        )
        self.assertEqual(
            format_switch_command(generation=42, slot=7, hwnd=0x1234567),
            "SWITCH generation=42 slot=7 hwnd=0x0000000001234567",
        )
        self.assertEqual(
            format_stop_command("viewer_idle"),
            "STOP reason=viewer_idle",
        )

    def test_rejects_malformed_event_json(self) -> None:
        with self.assertRaisesRegex(RouterProtocolError, "malformed JSON"):
            parse_router_event("{not-json")


class NativeCaptureRouterProcessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.supervisors: list[NativeCaptureRouterProcess] = []

    def tearDown(self) -> None:
        for supervisor in reversed(self.supervisors):
            supervisor.stop_timeout = 0.1
            supervisor.terminate_timeout = 0.2
            supervisor.kill_timeout = 0.2
            supervisor.stop(reason="test_teardown")

    def supervisor(
        self,
        mode: str = "normal",
        *,
        stdout_bytes: int = 0,
        stdout_consumer=None,
        ready_timeout: float = 3.0,
        switch_timeout: float = 1.0,
    ) -> NativeCaptureRouterProcess:
        interpreter = getattr(sys, "_base_executable", sys.executable)
        command = [
            interpreter,
            "-u",
            str(FIXTURE),
            "--mode",
            mode,
            "--stdout-bytes",
            str(stdout_bytes),
        ]
        supervisor = NativeCaptureRouterProcess(
            command,
            ready_timeout=ready_timeout,
            switch_timeout=switch_timeout,
            stop_timeout=0.2,
            terminate_timeout=0.5,
            kill_timeout=0.5,
            stdout_consumer=stdout_consumer,
        )
        self.supervisors.append(supervisor)
        return supervisor

    def test_stdout_is_drained_before_ready(self) -> None:
        byte_count = 0
        lock = threading.Lock()

        def consume(chunk: bytes) -> None:
            nonlocal byte_count
            with lock:
                byte_count += len(chunk)

        expected = 2 * 1024 * 1024
        supervisor = self.supervisor(
            stdout_bytes=expected,
            stdout_consumer=consume,
        )
        supervisor.start()

        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            with lock:
                if byte_count == expected:
                    break
            time.sleep(0.01)
        with lock:
            self.assertEqual(byte_count, expected)

    def test_stdout_can_stream_directly_to_an_owned_file(self) -> None:
        expected = 2 * 1024 * 1024
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "router.bgra"
            interpreter = getattr(sys, "_base_executable", sys.executable)
            supervisor = NativeCaptureRouterProcess(
                [
                    interpreter,
                    "-u",
                    str(FIXTURE),
                    "--mode",
                    "normal",
                    "--stdout-bytes",
                    str(expected),
                ],
                stdout_path=output,
                ready_timeout=3.0,
            )
            self.supervisors.append(supervisor)
            supervisor.start()
            supervisor.stop(reason="file_sink_complete")

            self.assertEqual(output.stat().st_size, expected)

    def test_stdout_can_use_a_non_owned_handle(self) -> None:
        expected = 2 * 1024 * 1024
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "downstream.bgra"
            interpreter = getattr(sys, "_base_executable", sys.executable)
            with output.open("wb", buffering=0) as sink:
                supervisor = NativeCaptureRouterProcess(
                    [
                        interpreter,
                        "-u",
                        str(FIXTURE),
                        "--mode",
                        "normal",
                        "--stdout-bytes",
                        str(expected),
                    ],
                    stdout_handle=sink,
                    ready_timeout=3.0,
                )
                self.supervisors.append(supervisor)
                supervisor.start()
                supervisor.stop(reason="external_sink_complete")

                self.assertFalse(sink.closed)
            self.assertEqual(output.stat().st_size, expected)

    def test_switches_use_monotonic_correlated_generations(self) -> None:
        supervisor = self.supervisor()
        supervisor.start()

        first = supervisor.switch(slot=1, hwnd=0x101)
        second = supervisor.switch(slot=7, hwnd=0x707)

        self.assertEqual(first.generation, 1)
        self.assertEqual(second.generation, 2)
        self.assertEqual(
            second.switch_started["command"],
            "SWITCH generation=2 slot=7 hwnd=0x0000000000000707",
        )
        self.assertEqual(second.first_frame["generation"], 2)
        self.assertEqual(supervisor.generation, 2)

    def test_stale_first_frame_cannot_complete_new_switch(self) -> None:
        supervisor = self.supervisor("stale")
        supervisor.start()
        supervisor.switch(slot=1, hwnd=0x101)

        result = supervisor.switch(slot=7, hwnd=0x707)

        self.assertEqual(result.generation, 2)
        self.assertEqual(result.first_frame["slot"], 7)
        generations = [
            event["generation"]
            for event in supervisor.event_history
            if event["event"] == "first_frame"
        ]
        self.assertEqual(generations, [1, 1, 2])

    def test_future_generation_is_a_protocol_error(self) -> None:
        supervisor = self.supervisor("future")
        supervisor.start()

        with self.assertRaisesRegex(RouterProtocolError, "future generation"):
            supervisor.switch(slot=1, hwnd=0x101)

    def test_first_frame_must_follow_switch_started(self) -> None:
        supervisor = self.supervisor("first-before-start")
        supervisor.start()

        with self.assertRaisesRegex(RouterProtocolError, "before switch_started"):
            supervisor.switch(slot=1, hwnd=0x101)

    def test_wrong_slot_is_a_protocol_error(self) -> None:
        supervisor = self.supervisor("wrong-slot")
        supervisor.start()

        with self.assertRaisesRegex(RouterProtocolError, "identity"):
            supervisor.switch(slot=1, hwnd=0x101)

    def test_malformed_stderr_is_a_protocol_error(self) -> None:
        supervisor = self.supervisor("malformed")
        supervisor.start()

        with self.assertRaisesRegex(RouterProtocolError, "malformed JSON"):
            supervisor.switch(slot=1, hwnd=0x101)

    def test_switch_timeout_does_not_accept_missing_first_frame(self) -> None:
        supervisor = self.supervisor("timeout", switch_timeout=0.15)
        supervisor.start()

        with self.assertRaisesRegex(RouterTimeoutError, "first_frame"):
            supervisor.switch(slot=1, hwnd=0x101)

    def test_ready_timeout_cleans_up_owned_child(self) -> None:
        supervisor = self.supervisor("no-ready", ready_timeout=0.1)

        with self.assertRaisesRegex(RouterTimeoutError, "ready"):
            supervisor.start()

        self.assertFalse(supervisor.running)
        self.assertEqual(supervisor.last_stop_stage, "terminate")

    def test_matching_remote_error_is_propagated(self) -> None:
        supervisor = self.supervisor("remote-error")
        supervisor.start()

        with self.assertRaises(RouterRemoteError) as raised:
            supervisor.switch(slot=1, hwnd=0x101)

        self.assertEqual(raised.exception.event["generation"], 1)
        self.assertEqual(raised.exception.event["code"], "HWND_DESTROYED")

    def test_child_crash_is_reported(self) -> None:
        supervisor = self.supervisor("crash")
        supervisor.start()

        with self.assertRaisesRegex(RouterProcessExitedError, "code 23"):
            supervisor.switch(slot=1, hwnd=0x101)

    def test_normal_cleanup_stops_only_owned_child(self) -> None:
        supervisor = self.supervisor()
        supervisor.start()
        owned_pid = supervisor.pid

        stage = supervisor.stop(reason="test_complete")

        self.assertEqual(stage, "stop")
        self.assertEqual(supervisor.pid, owned_pid)
        self.assertFalse(supervisor.running)

    def test_cleanup_terminates_child_that_ignores_stop(self) -> None:
        supervisor = self.supervisor("ignore-stop")
        supervisor.start()

        stage = supervisor.stop(reason="test_timeout")

        self.assertEqual(stage, "terminate")
        self.assertFalse(supervisor.running)

    def test_cleanup_escalates_to_kill_when_terminate_times_out(self) -> None:
        supervisor = NativeCaptureRouterProcess(
            ["fake-router"],
            stop_timeout=0.01,
            terminate_timeout=0.01,
            kill_timeout=0.01,
        )
        process = _KillEscalationProcess()
        supervisor._process = process  # type: ignore[assignment]
        supervisor._last_pid = process.pid

        stage = supervisor.stop(reason="forced_cleanup")

        self.assertEqual(stage, "kill")
        self.assertEqual(process.actions, ["terminate", "kill"])
        self.assertEqual(
            process.stdin.writes,
            [b"STOP reason=forced_cleanup\n"],
        )
        self.assertTrue(process.stdin.flushed)
        self.assertTrue(process.stdin.closed)


class _FakeStdin:
    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.flushed = False
        self.closed = False

    def write(self, value: bytes) -> None:
        self.writes.append(value)

    def flush(self) -> None:
        self.flushed = True

    def close(self) -> None:
        self.closed = True


class _KillEscalationProcess:
    def __init__(self) -> None:
        self.pid = 424242
        self.args = ["fake-router"]
        self.returncode = None
        self.stdin = _FakeStdin()
        self.stdout = None
        self.stderr = None
        self.actions: list[str] = []
        self.killed = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.killed:
            self.returncode = -9
            return self.returncode
        raise subprocess.TimeoutExpired(self.args, timeout)

    def terminate(self) -> None:
        self.actions.append("terminate")

    def kill(self) -> None:
        self.actions.append("kill")
        self.killed = True


if __name__ == "__main__":
    unittest.main()
