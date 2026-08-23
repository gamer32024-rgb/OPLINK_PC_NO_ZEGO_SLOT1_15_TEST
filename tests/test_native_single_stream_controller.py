from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


HOST = Path(__file__).resolve().parents[1] / "host"
if str(HOST) not in sys.path:
    sys.path.insert(0, str(HOST))

from native_capture_router_process import RouterSwitchResult  # noqa: E402
from native_single_stream_controller import (  # noqa: E402
    NativeSingleStreamController,
    NativeSingleStreamError,
)


class NativeSingleStreamControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.runtime_dir = Path(self.temporary_directory.name)
        self.encoder_process = FakeEncoderProcess(pid=2202)
        self.popen_factory = FakePopenFactory(self.encoder_process)
        self.router = FakeRouter(pid=1101)
        self.router_factory = FakeRouterFactory(self.router)
        self.path_probe = FakePathProbe(online=True)
        self.unrelated_process = FakeEncoderProcess(pid=9909)
        self.controller = NativeSingleStreamController(
            router_exe=self.runtime_dir / "oplink_capture_router.exe",
            ffmpeg=self.runtime_dir / "ffmpeg.exe",
            encoder="mf",
            width=1920,
            height=1080,
            fps=30,
            bitrate_kbps=6000,
            mediamtx_api="http://127.0.0.1:9997",
            identity_provider=self.identity,
            runtime_dir=self.runtime_dir,
            viewer_idle_timeout_seconds=120.0,
            pipeline_start_timeout=0.5,
            switch_timeout=0.25,
            path_probe=self.path_probe,
            popen_factory=self.popen_factory,
            router_factory=self.router_factory,
        )

    def tearDown(self) -> None:
        self.controller.stop()
        self.temporary_directory.cleanup()

    @staticmethod
    def identity(slot: int) -> dict[str, object]:
        return {
            "ok": True,
            "slot": slot,
            "hwnd": slot * 0x100 + slot,
            "aspect_is_16_9": True,
        }

    def test_ffmpeg_uses_fixed_oplink_active_path_and_router_pipe(self) -> None:
        self.controller.activate(1)

        self.assertEqual(len(self.popen_factory.calls), 1)
        args, kwargs = self.popen_factory.calls[0]
        self.assertIn("rawvideo", args)
        self.assertIn("pipe:0", args)
        self.assertEqual(
            args[-1],
            "rtsp://127.0.0.1:8554/oplink_active",
        )
        self.assertFalse(any("slot01" in argument for argument in args))
        self.assertIs(kwargs["stdin"], subprocess.PIPE)
        self.assertEqual(len(self.router_factory.calls), 1)
        self.assertIs(
            self.router_factory.calls[0]["stdout_handle"],
            self.encoder_process.stdin,
        )

    def test_mobile_profile_uses_720p_and_2500_kbps_contract(self) -> None:
        controller = NativeSingleStreamController(
            router_exe=self.runtime_dir / "oplink_capture_router.exe",
            ffmpeg=self.runtime_dir / "ffmpeg.exe",
            encoder="mf",
            width=1280,
            height=720,
            fps=30,
            bitrate_kbps=2500,
            mediamtx_api="http://127.0.0.1:9997",
            identity_provider=self.identity,
            runtime_dir=self.runtime_dir / "mobile",
            viewer_idle_timeout_seconds=120.0,
            pipeline_start_timeout=0.5,
            switch_timeout=0.25,
            path_probe=self.path_probe,
            popen_factory=self.popen_factory,
            router_factory=self.router_factory,
        )
        try:
            result = controller.activate(1)
            args, _kwargs = self.popen_factory.calls[-1]
            self.assertEqual(result["profile"], {"w": 1280, "h": 720, "fps": 30, "bitrate_kbps": 2500})
            self.assertIn("1280x720", args)
            self.assertIn("2500k", args)
        finally:
            controller.stop()

    def test_slots_1_7_20_reuse_one_router_and_encoder_pid(self) -> None:
        results = [self.controller.activate(slot) for slot in (1, 7, 20)]

        self.assertEqual(
            [result["router_pid"] for result in results],
            [1101, 1101, 1101],
        )
        self.assertEqual(
            [result["encoder_pid"] for result in results],
            [2202, 2202, 2202],
        )
        self.assertEqual(
            [result["reused_transport"] for result in results],
            [False, True, True],
        )
        self.assertEqual(len(self.popen_factory.calls), 1)
        self.assertEqual(len(self.router_factory.calls), 1)
        self.assertEqual(
            [(call["slot"], call["hwnd"]) for call in self.router.switch_calls],
            [(1, 0x101), (7, 0x707), (20, 0x1414)],
        )
        self.assertEqual(self.router.stop_reasons, [])

    def test_slot_above_twenty_is_rejected_before_pipeline_start(self) -> None:
        with self.assertRaisesRegex(NativeSingleStreamError, "between 1 and 20"):
            self.controller.activate(21)

        self.assertEqual(self.popen_factory.calls, [])
        self.assertEqual(self.router_factory.calls, [])

    def test_activate_returns_native_switch_contract(self) -> None:
        result = self.controller.activate(7)

        self.assertTrue(result["ok"])
        self.assertEqual(result["mode"], "native_single_stream")
        self.assertEqual(result["stream_mode"], "native_single")
        self.assertEqual(result["path"], "oplink_active")
        self.assertEqual(result["whep_path"], "oplink_active")
        self.assertEqual(result["active_slot"], 7)
        self.assertEqual(result["switch_generation"], 1)
        self.assertEqual(result["capture_first_frame_ms"], 17)
        self.assertEqual(result["router_pid"], 1101)
        self.assertEqual(result["encoder_pid"], 2202)
        self.assertEqual(result["publisher_pid"], 2202)
        self.assertEqual(
            result["router_executable"],
            str((self.runtime_dir / "oplink_capture_router.exe").resolve()),
        )
        self.assertEqual(
            result["publisher_executable"],
            str((self.runtime_dir / "ffmpeg.exe").resolve()),
        )
        self.assertFalse(result["reused_transport"])
        self.assertEqual(result["first_frame"]["generation"], 1)
        self.assertEqual(result["first_frame"]["slot"], 7)

    def test_prewarm_is_a_noop_for_the_existing_pipeline(self) -> None:
        activated = self.controller.activate(1)

        result = self.controller.prewarm([1, 7, 20, 7])

        self.assertFalse(result["prewarm_supported"])
        self.assertEqual(result["requested_slots"], [1, 7, 20])
        self.assertEqual(result["router_pid"], activated["router_pid"])
        self.assertEqual(result["encoder_pid"], activated["encoder_pid"])
        self.assertEqual(len(self.popen_factory.calls), 1)
        self.assertEqual(len(self.router_factory.calls), 1)
        self.assertEqual(len(self.router.switch_calls), 1)

    def test_stop_cleans_only_controller_owned_children(self) -> None:
        self.controller.activate(1)
        state_path = self.controller.state_path
        self.assertTrue(state_path.is_file())

        self.controller.stop()

        self.assertEqual(self.router.stop_reasons, ["native_single_stop"])
        self.assertEqual(self.encoder_process.actions, ["terminate"])
        self.assertEqual(self.encoder_process.returncode, 0)
        self.assertEqual(self.unrelated_process.actions, [])
        self.assertIsNone(self.unrelated_process.returncode)
        self.assertFalse(state_path.exists())


class FakePathProbe:
    def __init__(self, *, online: bool) -> None:
        self.online = online
        self.calls = 0

    def __call__(self) -> bool:
        self.calls += 1
        return self.online


class FakePipe:
    def __init__(self) -> None:
        self.close_count = 0

    @property
    def closed(self) -> bool:
        return self.close_count > 0

    def close(self) -> None:
        self.close_count += 1


class FakeEncoderProcess:
    def __init__(self, *, pid: int) -> None:
        self.pid = pid
        self.stdin = FakePipe()
        self.returncode: int | None = None
        self.actions: list[str] = []

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            raise subprocess.TimeoutExpired(["fake-ffmpeg"], timeout)
        return self.returncode

    def terminate(self) -> None:
        self.actions.append("terminate")
        self.returncode = 0

    def kill(self) -> None:
        self.actions.append("kill")
        self.returncode = -9


class FakePopenFactory:
    def __init__(self, process: FakeEncoderProcess) -> None:
        self.process = process
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, args, **kwargs):
        self.calls.append((list(args), dict(kwargs)))
        if len(self.calls) > 1:
            raise AssertionError("FFmpeg factory was called more than once")
        return self.process


class FakeRouter:
    def __init__(self, *, pid: int) -> None:
        self.pid = pid
        self.running = False
        self.generation = 0
        self.switch_calls: list[dict[str, int | float]] = []
        self.stop_reasons: list[str] = []

    def start(self) -> dict[str, object]:
        self.running = True
        return {
            "event": "ready",
            "pid": self.pid,
            "width": 1920,
            "height": 1080,
            "fps": 30,
        }

    def switch(
        self,
        *,
        slot: int,
        hwnd: int,
        timeout: float,
    ) -> RouterSwitchResult:
        self.generation += 1
        self.switch_calls.append(
            {
                "slot": slot,
                "hwnd": hwnd,
                "timeout": timeout,
            }
        )
        return RouterSwitchResult(
            generation=self.generation,
            slot=slot,
            hwnd=hwnd,
            switch_started={
                "event": "switch_started",
                "generation": self.generation,
                "slot": slot,
                "hwnd": f"0x{hwnd:x}",
                "at_ms": 1_000 + self.generation,
            },
            first_frame={
                "event": "first_frame",
                "generation": self.generation,
                "slot": slot,
                "source_w": 1920,
                "source_h": 1080,
                "output_w": 1920,
                "output_h": 1080,
                "elapsed_ms": 10 + slot,
                "stdout_frame_index": self.generation,
            },
        )

    def stop(self, *, reason: str) -> str:
        self.stop_reasons.append(reason)
        self.running = False
        return "stop"


class FakeRouterFactory:
    def __init__(self, router: FakeRouter) -> None:
        self.router = router
        self.calls: list[dict[str, object]] = []

    def __call__(self, command, **kwargs):
        self.calls.append({"command": list(command), **dict(kwargs)})
        if len(self.calls) > 1:
            raise AssertionError("router factory was called more than once")
        return self.router


if __name__ == "__main__":
    unittest.main()
