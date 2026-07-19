from __future__ import annotations

from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock


HOST = Path(__file__).resolve().parents[1] / "host"
if str(HOST) not in sys.path:
    sys.path.insert(0, str(HOST))

from stream_test_server import StreamPublisherController, StreamPublisherError  # noqa: E402


class StreamViewerStateTests(unittest.TestCase):
    def setUp(self) -> None:
        controller = StreamPublisherController.__new__(StreamPublisherController)
        controller.viewer_idle_timeout_seconds = 15.0
        controller._viewer_lock = threading.Lock()
        controller._viewer_state = "never_connected"
        controller._viewer_slot = None
        controller._last_viewer_heartbeat = None
        controller.retain_only = Mock()
        controller.status = Mock(return_value={"warm_slots": [1, 2, 15]})
        self.controller = controller

    def test_active_heartbeat_updates_slot_without_pruning_publishers(self) -> None:
        result = self.controller.viewer_update("active", 15)

        self.assertEqual(result["state"], "active")
        self.assertEqual(result["slot"], 15)
        self.assertEqual(result["warm_slots"], [1, 2, 15])
        self.controller.retain_only.assert_not_called()

    def test_background_retains_only_current_slot(self) -> None:
        result = self.controller.viewer_update("background", 15)

        self.assertEqual(result["state"], "background")
        self.controller.retain_only.assert_called_once_with(15)

    def test_invalid_state_and_missing_slot_are_rejected(self) -> None:
        with self.assertRaisesRegex(StreamPublisherError, "viewer state"):
            self.controller.viewer_update("paused", 1)
        with self.assertRaisesRegex(StreamPublisherError, "viewer slot"):
            self.controller.viewer_update("active", None)


class StreamPublisherPriorityTests(unittest.TestCase):
    def test_prewarm_yields_before_starting_when_activation_is_pending(self) -> None:
        controller = StreamPublisherController.__new__(StreamPublisherController)
        controller.cache_size = 3
        controller._lock = threading.RLock()
        controller._activation_pending = threading.Event()
        controller._activation_pending.set()
        controller._processes = {}
        controller._active_slot = None
        controller._validate_slot = Mock(side_effect=lambda slot: {"slot": slot, "ok": True})
        controller._start_slot_locked = Mock()
        controller._prune_locked = Mock()
        controller._write_state = Mock()
        controller.status = Mock(return_value={"warm_slots": []})

        result = controller.prewarm([1, 2, 3])

        controller._start_slot_locked.assert_not_called()
        self.assertTrue(result["interrupted_for_activation"])
        self.assertEqual(result["activation_ms_by_slot"], {})

    def test_prune_never_evicts_the_active_slot(self) -> None:
        controller = StreamPublisherController.__new__(StreamPublisherController)
        controller.cache_size = 3
        controller._processes = {1: object(), 2: object(), 3: object(), 9: object()}
        controller._active_slot = 9
        controller._last_used = {1: 1.0, 2: 2.0, 3: 3.0, 9: 0.0}

        def stop(slot: int) -> None:
            controller._processes.pop(slot)

        controller._stop_slot_locked = Mock(side_effect=stop)
        controller._prune_locked({1, 2, 3})

        self.assertIn(9, controller._processes)
        self.assertNotIn(1, controller._processes)


if __name__ == "__main__":
    unittest.main()
