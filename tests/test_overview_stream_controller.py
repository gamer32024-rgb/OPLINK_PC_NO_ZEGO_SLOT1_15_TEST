from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "host"
sys.path.insert(0, str(HOST))

from overview_stream_controller import OverviewStreamController, OverviewStreamError  # noqa: E402


class OverviewStreamControllerTests(unittest.TestCase):
    @staticmethod
    def identity(slot: int) -> dict[str, object]:
        return {
            "ok": True,
            "slot": slot,
            "hwnd": 1000 + slot,
            "aspect_is_16_9": True,
        }

    def test_builds_15_input_4x4_overview(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = OverviewStreamController(
                ffmpeg=Path(directory) / "ffmpeg.exe",
                encoder="mf",
                width=1280,
                height=720,
                fps=10,
                bitrate_kbps=1800,
                mediamtx_api="http://127.0.0.1:9997",
                identity_provider=self.identity,
                runtime_dir=directory,
            )
            args = controller._ffmpeg_args([self.identity(slot) for slot in range(1, 16)])

        self.assertEqual(args.count("lavfi"), 15)
        filter_graph = args[args.index("-filter_complex") + 1]
        self.assertIn("xstack=inputs=15", filter_graph)
        self.assertIn("scale=320:180", filter_graph)
        self.assertIn("pad=1280:720", filter_graph)
        self.assertIn("640_540", filter_graph)
        self.assertNotIn("|960_540", filter_graph)
        self.assertEqual(args[-1], "rtsp://127.0.0.1:8554/oplink_overview")

    def test_rejects_overview_when_one_slot_is_unavailable(self) -> None:
        def identity(slot: int) -> dict[str, object]:
            if slot == 8:
                return {"ok": False, "slot": slot, "error": "missing"}
            return self.identity(slot)

        with tempfile.TemporaryDirectory() as directory:
            controller = OverviewStreamController(
                ffmpeg=Path(directory) / "ffmpeg.exe",
                encoder="x264",
                width=1280,
                height=720,
                fps=10,
                bitrate_kbps=1800,
                mediamtx_api="http://127.0.0.1:9997",
                identity_provider=identity,
                runtime_dir=directory,
            )
            with self.assertRaisesRegex(OverviewStreamError, "slot': 8"):
                controller._source_identities()


if __name__ == "__main__":
    unittest.main()
