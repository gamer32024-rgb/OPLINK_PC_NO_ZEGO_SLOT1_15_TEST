from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import gui_test_pc_server as server  # noqa: E402


class ServerSlotOrderTests(unittest.TestCase):
    def test_list_order_is_preserved_and_duplicates_are_removed(self) -> None:
        self.assertEqual(
            [6, 2, 9, 1, 5, 8],
            server.normalize_slots([6, 2, 9, 1, 5, 8, 2]),
        )

    def test_ranges_expand_without_sorting_other_tokens(self) -> None:
        self.assertEqual([6, 2, 3, 4, 1], server.normalize_slots("6,2-4,1"))

    def test_slot_twenty_is_valid_and_twenty_one_is_rejected(self) -> None:
        self.assertEqual([20, 1], server.normalize_slots("20,21,1"))
        self.assertEqual(20, server.slot_from_title("20"))
        self.assertEqual(20, server.slot_from_title("[20] 星詠魔力"))
        self.assertIsNone(server.slot_from_title("21"))

    def test_elevated_launcher_waits_for_the_real_batch(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout="", stderr="")
        with (
            patch.object(server, "launcher_script_path", return_value=Path(__file__)),
            patch.object(server, "launcher_log_state", return_value={"tail": []}),
            patch.object(
                server,
                "wait_for_launcher_log_change",
                return_value=(True, {"tail": []}),
            ),
            patch.object(server, "log"),
            patch.object(server.subprocess, "run", return_value=completed) as run,
        ):
            result = server.run_launcher_action_elevated(
                "start-missing",
                [1, 2],
                forcebind_mode="netbind",
                use_windows_users=True,
            )

        command = run.call_args.args[0][-1]
        self.assertIn("-PassThru", command)
        self.assertIn("$process.WaitForExit()", command)
        self.assertEqual(
            server.ELEVATED_LAUNCHER_TIMEOUT_SEC,
            run.call_args.kwargs["timeout"],
        )
        self.assertTrue(result["ok"])


if __name__ == "__main__":
    unittest.main()
