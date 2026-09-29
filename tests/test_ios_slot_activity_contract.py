from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class IOSSlotActivityContractTests(unittest.TestCase):
    def test_heartbeat_decodes_structured_module_and_pico_slot(self) -> None:
        source = (ROOT / "ios/OPLINKStreamTest/GUIBridgeModels.swift").read_text(encoding="utf-8")

        self.assertIn('case slotCurrentModule = "slot_current_module"', source)
        self.assertIn('case picoActivitySlot = "pico_activity_slot"', source)
        self.assertIn('case queuedPlaybackSlots = "queued_playback_slots"', source)

    def test_panel_pulses_one_pico_slot_and_fits_module_name(self) -> None:
        panel = (ROOT / "ios/OPLINKStreamTest/GUIControlPanelView.swift").read_text(encoding="utf-8")

        self.assertIn('let key = "oplink.pico.activity"', panel)
        self.assertIn("active: picoActivitySlot == slot", panel)
        self.assertNotIn("active: playing || opening", panel)
        self.assertIn("displayedModuleName(for: slot)", panel)
        self.assertIn("minimumScaleFactor = 0.35", panel)

    def test_fast_status_poll_only_runs_while_panel_is_visible(self) -> None:
        controller = (ROOT / "ios/OPLINKStreamTest/StreamViewController.swift").read_text(encoding="utf-8")

        self.assertIn("timeInterval: 0.5", controller)
        self.assertIn("guard !guiPanel.isHidden", controller)
        self.assertIn("refreshGUIBridgeHeartbeat(baseURL: baseURL)", controller)

    def test_asset_inventory_is_available_from_the_fixed_right_rail(self) -> None:
        rail = (ROOT / "ios/OPLINKStreamTest/LegacyStreamControlsView.swift").read_text(
            encoding="utf-8"
        )
        controller = (ROOT / "ios/OPLINKStreamTest/StreamViewController.swift").read_text(
            encoding="utf-8"
        )
        api = (ROOT / "ios/OPLINKStreamTest/GUIBridgeAPI.swift").read_text(encoding="utf-8")
        models = (ROOT / "ios/OPLINKStreamTest/GUIBridgeModels.swift").read_text(
            encoding="utf-8"
        )
        panel = (ROOT / "ios/OPLINKStreamTest/AssetInventoryPanelView.swift").read_text(
            encoding="utf-8"
        )

        self.assertIn('iconButton("dollarsign.circle.fill"', rail)
        self.assertIn("arrangedSubviews: [assets, keyboard, grid]", rail)
        self.assertIn("fixedRightRail.onAssets", controller)
        self.assertIn("fetchAssetInventory(baseURL: baseURL, slots: OPLINKSlots.all)", controller)
        self.assertIn("func fetchAssetInventory", api)
        self.assertIn("/gui-test-pc/api/assets/latest", models)
        self.assertIn("struct GUIAssetInventoryItem", models)
        self.assertIn("for slot in OPLINKSlots.range", panel)

    def test_asset_summary_and_equipment_light_follow_confirmed_rules(self) -> None:
        panel = (ROOT / "ios/OPLINKStreamTest/AssetInventoryPanelView.swift").read_text(
            encoding="utf-8"
        )

        self.assertIn("for character in 1...5", panel)
        self.assertIn("guard let value = readings[character]?[keyPath: keyPath]", panel)
        self.assertIn("truncateThousands ? (value / 1_000) * 1_000 : value", panel)
        self.assertIn("Self.amount(item?.coins, truncateThousands: true)", panel)
        self.assertIn("count == 5", panel)
        self.assertIn("(2...5).allSatisfy", panel)
        self.assertIn("readings[$0]?.weaponEmpty == false", panel)
        self.assertIn('if character == 1 {\n                weapon = "不檢測"', panel)
        self.assertIn("self.expandedSlot == selectedSlot ? nil : selectedSlot", panel)

    def test_launcher_progress_decodes_and_displays_all_action_states(self) -> None:
        models = (ROOT / "ios/OPLINKStreamTest/GUIBridgeModels.swift").read_text(encoding="utf-8")
        panel = (ROOT / "ios/OPLINKStreamTest/GUIControlPanelView.swift").read_text(encoding="utf-8")
        controller = (ROOT / "ios/OPLINKStreamTest/StreamViewController.swift").read_text(encoding="utf-8")

        self.assertIn('case launcherAction = "launcher_action"', models)
        self.assertIn('case launcherSlots = "launcher_slots"', models)
        for label in ("排隊", "開啟", "關閉", "重啟"):
            self.assertIn(label, panel)
        self.assertIn("reportsOpening", controller)
        self.assertIn("reportsClosing", controller)
        self.assertIn("reportsRestarting", controller)

    def test_module_layout_and_quick_buttons_match_updated_panel(self) -> None:
        panel = (ROOT / "ios/OPLINKStreamTest/GUIControlPanelView.swift").read_text(encoding="utf-8")

        self.assertIn("static let compactScale: CGFloat = 0.8", panel)
        self.assertIn("UIStackView(arrangedSubviews: [modulesScroll, chainRow])", panel)
        self.assertIn("multiplier: ModulePanelMetrics.compactScale", panel)
        self.assertIn("buildPresetGrid(),\n            automationRow", panel)
        self.assertIn("columns: 5", panel)
        self.assertIn("buttonHeight: ModulePanelMetrics.quickButtonHeight", panel)
        self.assertIn("fontSize: ModulePanelMetrics.quickFontSize", panel)

    def test_preset_1_to_10_plays_and_11_to_20_only_loads(self) -> None:
        panel = (ROOT / "ios/OPLINKStreamTest/GUIControlPanelView.swift").read_text(encoding="utf-8")

        self.assertIn("private func mapPresetToEditableCells", panel)
        self.assertIn("moduleChain[offset + 1] = name", panel)
        self.assertIn("let values = Array(preset.modules.prefix(8))", panel)
        tapped = panel.split("@objc private func presetTapped(_ sender: UIButton) {", 1)[1].split(
            "@objc private func presetLongPressed", 1
        )[0]
        self.assertLess(tapped.index("if index > 10 {"), tapped.index("onPlay?(slots, values)"))
        self.assertIn("mapPresetToEditableCells(values)", tapped)
        self.assertIn("beginPlaybackSubmission(slots: slots, feedbackButton: sender)", tapped)

    def test_daily_restart_is_one_button_after_existing_controls(self) -> None:
        panel = (ROOT / "ios/OPLINKStreamTest/GUIControlPanelView.swift").read_text(encoding="utf-8")
        api = (ROOT / "ios/OPLINKStreamTest/GUIBridgeAPI.swift").read_text(encoding="utf-8")
        models = (ROOT / "ios/OPLINKStreamTest/GUIBridgeModels.swift").read_text(encoding="utf-8")

        self.assertIn(
            "compactActionRow([startAll, closeAll, startSelected, closeSelected, restartSettings])",
            panel,
        )
        self.assertIn('compactTextButton("重啟"', panel)
        self.assertIn("onSaveDailyRestart?(entries)", panel)
        self.assertIn("for index in 0..<3", panel)
        self.assertIn("for step in 0..<3", panel)
        self.assertIn("fetchDailySlotRestart", api)
        self.assertIn("saveDailySlotRestart", api)
        self.assertIn('/gui-test-pc/api/daily-slot-restart', models)

    def test_busy_playback_slot_tap_cancels_and_long_press_selects_for_replacement(self) -> None:
        panel = (ROOT / "ios/OPLINKStreamTest/GUIControlPanelView.swift").read_text(encoding="utf-8")

        self.assertIn("automaticSelectionBlockedSlots", panel)
        self.assertIn("playbackBusySlots", panel)
        self.assertIn("selectedSlots.subtract(launcherTransitionSlots)", panel)
        self.assertIn("onStopSlot?(slot)", panel)
        self.assertIn("onCancelAutomation?(automation.id)", panel)
        self.assertIn("#selector(slotLongPressed(_:))", panel)
        self.assertIn("下一個播放設定會立即取代目前工作", panel)

    def test_controller_health_accepts_fractional_heartbeat_and_server_truth(self) -> None:
        models = (ROOT / "ios/OPLINKStreamTest/GUIBridgeModels.swift").read_text(encoding="utf-8")
        controller = (ROOT / "ios/OPLINKStreamTest/StreamViewController.swift").read_text(encoding="utf-8")
        panel = (ROOT / "ios/OPLINKStreamTest/GUIControlPanelView.swift").read_text(encoding="utf-8")

        self.assertIn(".withFractionalSeconds", models)
        self.assertIn("response.controller?.online", controller)
        self.assertIn("GUI_TEST_PC 控制器已恢復連線", panel)

    def test_offline_controller_restart_button_cannot_collapse_into_a_line(self) -> None:
        panel = (ROOT / "ios/OPLINKStreamTest/GUIControlPanelView.swift").read_text(encoding="utf-8")

        self.assertIn(
            "restartControllerButton.heightAnchor.constraint(greaterThanOrEqualToConstant: 30)",
            panel,
        )
        self.assertIn(
            "restartControllerButton.setContentCompressionResistancePriority(.required, for: .vertical)",
            panel,
        )


if __name__ == "__main__":
    unittest.main()
