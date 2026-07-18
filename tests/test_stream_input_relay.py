from __future__ import annotations

from pathlib import Path
import sys
import unittest


HOST = Path(__file__).resolve().parents[1] / "host"
if str(HOST) not in sys.path:
    sys.path.insert(0, str(HOST))

from stream_test_server import GuiTestPcInputError, GuiTestPcInputRelay  # noqa: E402


class GuiTestPcInputRelayTests(unittest.TestCase):
    def test_relay_accepts_only_loopback(self) -> None:
        with self.assertRaisesRegex(ValueError, "loopback"):
            GuiTestPcInputRelay("http://192.0.2.10:5111")

    def test_health_requires_gui_test_pc_identity(self) -> None:
        relay = GuiTestPcInputRelay("http://127.0.0.1:5111")
        relay._request = lambda *args, **kwargs: {  # type: ignore[method-assign]
            "enabled": True,
            "execution_owner": "GUI_TEST_PC",
            "relayed_to": "GUI_TEST_PC",
        }
        self.assertTrue(relay.health()["token_required"])

        relay._request = lambda *args, **kwargs: {  # type: ignore[method-assign]
            "enabled": True,
            "execution_owner": "GUI_TEST",
            "relayed_to": "GUI_TEST",
        }
        with self.assertRaises(GuiTestPcInputError):
            relay.health()

    def test_input_adds_host_timestamp_and_checks_owner(self) -> None:
        relay = GuiTestPcInputRelay("http://127.0.0.1:5111")
        captured = {}

        def request(_path, *, payload, **_kwargs):
            captured.update(payload)
            return {
                "ok": True,
                "execution_owner": "GUI_TEST_PC",
                "relayed_to": "GUI_TEST_PC",
            }

        relay._request = request  # type: ignore[method-assign]
        result = relay.handle({"slot": 15, "action": "down", "x": 0.5, "y": 0.5})
        self.assertGreater(captured["host_received_at_ms"], 0)
        self.assertEqual(result["relayed_to"], "GUI_TEST_PC")


if __name__ == "__main__":
    unittest.main()
