from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import gui_test_pc as gui  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402


class LauncherAutoPlayReadinessTests(unittest.TestCase):
    @staticmethod
    def target(*, pid: int = 101, hwnd: int = 202, width: int = 1280, height: int = 720) -> dict:
        return {"pid": pid, "hwnd": hwnd, "width": width, "height": height}

    def test_delay_starts_only_after_three_stable_size_checks(self) -> None:
        state: dict = {}
        for now in (0.0, 1.0):
            ready, _reason = gui.advance_launcher_autoplay_slot_readiness(
                self.target(),
                state,
                now=now,
                delay_seconds=10.0,
                expected_client_size=(1280, 720),
            )
            self.assertFalse(ready)
            self.assertIsNone(state.get("delay_started_at"))

        ready, _reason = gui.advance_launcher_autoplay_slot_readiness(
            self.target(),
            state,
            now=2.0,
            delay_seconds=10.0,
            expected_client_size=(1280, 720),
        )
        self.assertFalse(ready)
        self.assertEqual(2.0, state["delay_started_at"])

        ready, _reason = gui.advance_launcher_autoplay_slot_readiness(
            self.target(),
            state,
            now=12.0,
            delay_seconds=10.0,
            expected_client_size=(1280, 720),
        )
        self.assertTrue(ready)

    def test_wrong_size_or_replaced_window_restarts_readiness(self) -> None:
        state: dict = {}
        for now in (0.0, 1.0, 2.0):
            gui.advance_launcher_autoplay_slot_readiness(
                self.target(),
                state,
                now=now,
                delay_seconds=5.0,
                expected_client_size=(1280, 720),
            )
        self.assertEqual(2.0, state["delay_started_at"])

        ready, reason = gui.advance_launcher_autoplay_slot_readiness(
            self.target(width=1270),
            state,
            now=3.0,
            delay_seconds=5.0,
            expected_client_size=(1280, 720),
        )
        self.assertFalse(ready)
        self.assertIn("expected 1280x720", reason)
        self.assertIsNone(state["delay_started_at"])

        for now in (4.0, 5.0, 6.0):
            gui.advance_launcher_autoplay_slot_readiness(
                self.target(hwnd=303),
                state,
                now=now,
                delay_seconds=5.0,
                expected_client_size=(1280, 720),
            )
        self.assertEqual(6.0, state["delay_started_at"])

    def test_slots_start_their_delays_independently(self) -> None:
        slot_1_state: dict = {}
        slot_2_state: dict = {}
        for now in (0.0, 1.0, 2.0):
            gui.advance_launcher_autoplay_slot_readiness(
                self.target(hwnd=201),
                slot_1_state,
                now=now,
                delay_seconds=10.0,
                expected_client_size=(1280, 720),
            )
        for now in (5.0, 6.0, 7.0):
            gui.advance_launcher_autoplay_slot_readiness(
                self.target(hwnd=202),
                slot_2_state,
                now=now,
                delay_seconds=10.0,
                expected_client_size=(1280, 720),
            )

        slot_1_ready, _reason = gui.advance_launcher_autoplay_slot_readiness(
            self.target(hwnd=201),
            slot_1_state,
            now=12.0,
            delay_seconds=10.0,
            expected_client_size=(1280, 720),
        )
        slot_2_ready, _reason = gui.advance_launcher_autoplay_slot_readiness(
            self.target(hwnd=202),
            slot_2_state,
            now=12.0,
            delay_seconds=10.0,
            expected_client_size=(1280, 720),
        )
        self.assertTrue(slot_1_ready)
        self.assertFalse(slot_2_ready)

        slot_2_ready, _reason = gui.advance_launcher_autoplay_slot_readiness(
            self.target(hwnd=202),
            slot_2_state,
            now=17.0,
            delay_seconds=10.0,
            expected_client_size=(1280, 720),
        )
        self.assertTrue(slot_2_ready)

    def test_expected_client_size_comes_from_locked_layout_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "window_layout.json"
            path.write_text(
                '{"expected_client":{"width":1280,"height":720}}',
                encoding="utf-8",
            )
            self.assertEqual(
                (1280, 720),
                gui.load_launcher_autoplay_expected_client_size(path),
            )


class CompactGuiTestPcTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.original_refresh = gui.GuiTestPcMainWindow.refresh_all_local
        self.original_live_touch = gui.GuiTestPcMainWindow._start_live_touch_server
        self.original_warning = gui.QMessageBox.warning
        self.original_crash_log = gui.append_crash_log
        self.original_activity_log = gui.append_activity_log
        gui.GuiTestPcMainWindow.refresh_all_local = lambda _self: None
        gui.GuiTestPcMainWindow._start_live_touch_server = lambda _self: None
        gui.QMessageBox.warning = lambda *_args, **_kwargs: gui.QMessageBox.Ok
        gui.append_crash_log = lambda _message: None
        gui.append_activity_log = lambda _message: None
        self.window = gui.GuiTestPcMainWindow()
        self.window.resize(1830, 1230)
        self.window.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        for timer_name in (
            "scheduler_timer",
            "window_status_timer",
            "pwa_bridge_timer",
            "record_elapsed_timer",
        ):
            timer = getattr(self.window, timer_name, None)
            if timer is not None:
                timer.stop()
        self.window.hide()
        self.window.deleteLater()
        self.app.processEvents()
        gui.GuiTestPcMainWindow.refresh_all_local = self.original_refresh
        gui.GuiTestPcMainWindow._start_live_touch_server = self.original_live_touch
        gui.QMessageBox.warning = self.original_warning
        gui.append_crash_log = self.original_crash_log
        gui.append_activity_log = self.original_activity_log

    def _wait_for_workers(self, timeout: float = 3.0) -> None:
        deadline = time.monotonic() + timeout
        while self.window._workers and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.app.processEvents()
        self.assertFalse(self.window._workers)

    def test_chain_tab_shows_all_slots_without_duplicate_indicators(self) -> None:
        self.window.tabs.setCurrentIndex(0)
        self.app.processEvents()

        self.assertEqual(20, len(self.window.slot_buttons))
        self.assertTrue(all(button.isVisibleTo(self.window) for button in self.window.slot_buttons.values()))
        x_positions = {
            button.parentWidget().mapTo(self.window, button.pos()).x()
            for button in self.window.slot_buttons.values()
        }
        self.assertEqual(20, len(x_positions))
        self.assertFalse(self.window.findChildren(gui.QLabel, "SlotIndicator"))
        self.assertLessEqual(max(button.height() for button in self.window.slot_buttons.values()), 32)

    def test_selected_slots_preserve_click_order(self) -> None:
        for slot in (6, 2, 9, 1, 5, 8):
            button = self.window.slot_buttons[slot]
            button.setEnabled(True)
            button.click()
            self.app.processEvents()

        self.assertEqual([6, 2, 9, 1, 5, 8], self.window.selected_slots())

        self.window.slot_buttons[2].click()
        self.window.slot_buttons[2].click()
        self.app.processEvents()
        self.assertEqual([6, 9, 1, 5, 8, 2], self.window.selected_slots())

    def test_playback_speed_is_fixed_to_recorded_timing(self) -> None:
        self.assertEqual(1.0, self.window.speed_spin.value())
        self.assertFalse(self.window.speed_spin.isEnabled())
        self.assertFalse(self.window.speed_spin.isVisible())

    def test_launcher_uses_fixed_hidden_network_controls_and_compact_chain(self) -> None:
        self.window.tabs.setCurrentIndex(3)
        self.app.processEvents()

        self.assertFalse(self.window.launcher_slots_edit.isVisible())
        self.assertEqual("1-20", self.window.launcher_slots_edit.text())
        self.assertFalse(self.window.launcher_forcebind_combo.isVisible())
        self.assertEqual("netbind", self.window.launcher_forcebind_combo.currentData())
        self.assertFalse(self.window.launcher_windows_users_cb.isVisible())
        self.assertTrue(self.window.launcher_windows_users_cb.isChecked())
        self.assertEqual("Launch ALL", self.window.launcher_action_buttons["start-missing"].text())
        self.assertEqual(10, len(self.window.launcher_autoplay_step_combos))
        self.assertTrue(
            all(combo.isVisibleTo(self.window) for combo in self.window.launcher_autoplay_step_combos)
        )
        self.assertTrue(
            all(combo.height() <= 36 for combo in self.window.launcher_autoplay_step_combos)
        )

    def test_launcher_failure_always_releases_launch_all(self) -> None:
        original_elevated = gui.run_launcher_action_elevated
        original_local = gui.run_launcher_action

        def fail(*_args: object, **_kwargs: object) -> dict[str, object]:
            raise RuntimeError("simulated VPN launch failure")

        try:
            gui.run_launcher_action_elevated = fail
            gui.run_launcher_action = fail
            self.window.refresh_windows = lambda: None
            self.window.launcher_all_action("start-missing")
            self._wait_for_workers()
            self.assertFalse(self.window._launcher_busy)
            self.assertTrue(self.window.launcher_action_buttons["start-missing"].isEnabled())
        finally:
            gui.run_launcher_action_elevated = original_elevated
            gui.run_launcher_action = original_local

    def test_launcher_callback_error_always_releases_launch_all(self) -> None:
        original_elevated = gui.run_launcher_action_elevated
        original_local = gui.run_launcher_action

        def succeed(*_args: object, **_kwargs: object) -> dict[str, object]:
            return {"ok": True, "elevated": True}

        try:
            gui.run_launcher_action_elevated = succeed
            gui.run_launcher_action = succeed
            self.window.refresh_windows = lambda: None
            self.window.refresh_launcher_log = lambda: (_ for _ in ()).throw(
                RuntimeError("simulated callback error")
            )
            self.window.launcher_all_action("start-missing")
            self._wait_for_workers()
            self.assertFalse(self.window._launcher_busy)
            self.assertTrue(self.window.launcher_action_buttons["start-missing"].isEnabled())
        finally:
            gui.run_launcher_action_elevated = original_elevated
            gui.run_launcher_action = original_local

    def test_autoplay_settings_round_trip_module_chain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "launcher_autoplay.json"
            gui.save_launcher_autoplay_settings(["LOGIN", "MP"], 12.5, path)
            loaded = gui.load_launcher_autoplay_settings(path, include_modules=True)

        self.assertEqual(["LOGIN", "MP"], loaded["modules"])
        self.assertEqual("LOGIN", loaded["module"])
        self.assertEqual(12.5, loaded["delay_seconds"])

    def test_module_manager_uses_bulk_category_action(self) -> None:
        button_texts = {
            button.text()
            for button in self.window.findChildren(gui.QPushButton)
        }
        self.assertIn("多選模組加入", button_texts)
        self.assertNotIn("套用分類", button_texts)


class ModuleGroupMembershipTests(unittest.TestCase):
    def test_bulk_membership_replaces_only_target_group(self) -> None:
        groups, assignments = gui.update_module_group_membership(
            ["採集", "戰鬥"],
            {"A": "採集", "B": "採集", "C": "戰鬥"},
            "採集",
            {"B", "D"},
            ["A", "B", "C", "D"],
        )

        self.assertEqual(["採集", "戰鬥"], groups)
        self.assertEqual({"B": "採集", "C": "戰鬥", "D": "採集"}, assignments)

    def test_bulk_membership_creates_group_and_preserves_other_groups(self) -> None:
        groups, assignments = gui.update_module_group_membership(
            ["採集"],
            {"A": "採集"},
            "登入",
            {"B", "C"},
            ["A", "B", "C"],
        )

        self.assertEqual(["採集", "登入"], groups)
        self.assertEqual({"A": "採集", "B": "登入", "C": "登入"}, assignments)

    def test_ungroup_selection_only_removes_checked_assignments(self) -> None:
        groups, assignments = gui.update_module_group_membership(
            ["採集", "戰鬥"],
            {"A": "採集", "B": "戰鬥"},
            "未分組",
            {"A"},
            ["A", "B", "C"],
        )

        self.assertEqual(["採集", "戰鬥"], groups)
        self.assertEqual({"B": "戰鬥"}, assignments)


if __name__ == "__main__":
    unittest.main()
