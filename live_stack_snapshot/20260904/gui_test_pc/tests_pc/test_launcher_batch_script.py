from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "launcher" / "starcg_15_control_gui_test_pc.ps1"


class LauncherBatchScriptTests(unittest.TestCase):
    def test_batch_arranges_once_after_all_slot_launch_attempts(self) -> None:
        source = LAUNCHER.read_text(encoding="utf-8-sig")
        match = re.search(
            r"(?s)function Start-SlotsWithBypass \{.*?(?=\r?\nfunction Ensure-RunningWindowLayout)",
            source,
        )
        self.assertIsNotNone(match)
        batch = match.group(0)

        self.assertEqual(1, batch.count("Ensure-RunningWindowLayout"))
        self.assertIn("batch final layout attempt", batch)
        self.assertIn("slot $slot start failed", batch)
        self.assertLess(
            batch.index("foreach ($slot in $SlotNumbers)"),
            batch.index("Ensure-RunningWindowLayout"),
        )


if __name__ == "__main__":
    unittest.main()
