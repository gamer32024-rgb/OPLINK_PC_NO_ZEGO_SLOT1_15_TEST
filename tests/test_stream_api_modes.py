from __future__ import annotations

import sys
import unittest
from http import HTTPStatus
from pathlib import Path


HOST = Path(__file__).resolve().parents[1] / "host"
if str(HOST) not in sys.path:
    sys.path.insert(0, str(HOST))

from native_single_stream_controller import NativeSingleStreamError  # noqa: E402
from stream_test_server import Handler  # noqa: E402


class StreamAPIModeTests(unittest.TestCase):
    def test_sources_metadata_reports_native_single_contract(self) -> None:
        handler = Handler.__new__(Handler)
        handler.publisher_controller = FakeController(
            {
                "ok": True,
                "mode": "native_single_stream",
                "stream_mode": "native_single",
                "whep_path": "oplink_active",
                "active_slot": 7,
                "switch_generation": 42,
            }
        )

        self.assertEqual(
            handler._stream_metadata(),
            {
                "stream_mode": "native_single",
                "whep_path": "oplink_active",
                "active_slot": 7,
                "switch_generation": 42,
            },
        )

    def test_sources_metadata_defaults_to_legacy_when_disabled(self) -> None:
        handler = Handler.__new__(Handler)
        handler.publisher_controller = None

        self.assertEqual(
            handler._stream_metadata(),
            {
                "stream_mode": "legacy_warm_cache",
                "whep_path": None,
                "active_slot": None,
                "switch_generation": None,
            },
        )

    def test_native_activate_failure_is_service_unavailable(self) -> None:
        handler = Handler.__new__(Handler)
        handler.path = "/api/v1/activate"
        handler.slots = list(range(1, 21))
        handler.publisher_controller = FailingNativeController()
        handler.overview_controller = None
        handler._read_json_body = lambda: {"slot": 7}
        responses: list[tuple[dict[str, object], HTTPStatus]] = []
        handler._json = lambda payload, status=HTTPStatus.OK: responses.append(
            (payload, status)
        )

        handler.do_POST()

        self.assertEqual(
            responses,
            [
                (
                    {"ok": False, "error": "router first-frame timeout"},
                    HTTPStatus.SERVICE_UNAVAILABLE,
                )
            ],
        )

    def test_default_source_slots_cover_one_through_twenty(self) -> None:
        self.assertEqual(Handler.slots, list(range(1, 21)))


class FakeController:
    def __init__(self, status: dict[str, object]) -> None:
        self._status = status

    def status(self) -> dict[str, object]:
        return dict(self._status)


class FailingNativeController(FakeController):
    def __init__(self) -> None:
        super().__init__({"stream_mode": "native_single"})

    def activate(self, slot: int) -> dict[str, object]:
        raise NativeSingleStreamError("router first-frame timeout")


if __name__ == "__main__":
    unittest.main()
