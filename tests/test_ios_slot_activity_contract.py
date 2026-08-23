from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class IOSSlotActivityContractTests(unittest.TestCase):
    def test_heartbeat_decodes_structured_module_and_pico_slot(self) -> None:
        source = (ROOT / "ios/OPLINKStreamTest/GUIBridgeModels.swift").read_text(encoding="utf-8")

        self.assertIn('case slotCurrentModule = "slot_current_module"', source)
        self.assertIn('case picoActivitySlot = "pico_activity_slot"', source)

    def test_panel_pulses_one_pico_slot_and_fits_module_name(self) -> None:
        panel = (ROOT / "ios/OPLINKStreamTest/GUIControlPanelView.swift").read_text(encoding="utf-8")

        self.assertIn('let key = "oplink.pico.activity"', panel)
        self.assertIn("displayedModuleName(for: slot)", panel)
        self.assertIn("minimumScaleFactor = 0.35", panel)

    def test_fast_status_poll_only_runs_while_panel_is_visible(self) -> None:
        controller = (ROOT / "ios/OPLINKStreamTest/StreamViewController.swift").read_text(encoding="utf-8")

        self.assertIn("timeInterval: 0.5", controller)
        self.assertIn("guard !guiPanel.isHidden", controller)
        self.assertIn("refreshGUIBridgeHeartbeat(baseURL: baseURL)", controller)

    def test_deferred_asset_inventory_is_not_in_formal_build(self) -> None:
        sources = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "ios/OPLINKStreamTest").glob("*.swift")
        )

        self.assertNotIn("GUIAssetInventory", sources)
        self.assertNotIn("onRequestAssets", sources)


if __name__ == "__main__":
    unittest.main()
