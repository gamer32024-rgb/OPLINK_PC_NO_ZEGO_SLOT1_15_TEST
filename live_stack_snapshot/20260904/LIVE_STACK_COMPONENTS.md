# Live Stack Snapshot: 2026-09-04

## Scope

This is a source snapshot of the current GUI_TEST_PC and Pico components that are not owned by the OPLINK Git repository. The OPLINK host and iOS sources are already tracked by this repository at the commit recorded in `snapshot_manifest.json`.

This backup deliberately excludes account credentials, Windows-user mappings, adapter-specific NetBind data, current job state, logs, diagnostics, screenshots, keys, certificates, and executable build directories. It must not be copied over a live service while GUI_TEST_PC is running.

## Component Ownership

| User-facing function | Required owner and components | Local health evidence | If this is unavailable |
| --- | --- | --- | --- |
| GUI_TEST_PC launcher and desktop module playback | `gui_test_pc/gui_test_pc.py`, `shared_python/src/starcg_bot`, `gui_test_pc/launcher/starcg_15_control_gui_test_pc.ps1`, safe configuration under `gui_test_pc/config_pc` | GUI heartbeat under `runtime_pc/pwa_bridge/gui_heartbeat.json`; live input `http://127.0.0.1:5111/health` | No real module execution, Pico input, launcher actions, or live Slot state. |
| Phone PWA/native module panel | `gui_test_pc/gui_test_pc_server.py` on port `5100`; its `runtime_pc/pwa_bridge` command/heartbeat files; OPLINK iOS `GUIBridgeAPI.swift`, `GUIBridgeModels.swift`, and `GUIControlPanelView.swift`; Tailscale Serve mount `/gui-test-pc` | `http://127.0.0.1:5100/api/status` and fresh GUI heartbeat | Module list or Slot controls are offline even when video still works. |
| OPLINK 720p streaming | OPLINK repository `host/start_stream_test.ps1`, `host/stream_test_server.py`, native capture router, FFmpeg, MediaMTX, WGC, and Tailscale Serve mounts `/oplink-test` and `/oplink-whep`; iOS WHEP/stream view code | Stream API normally uses port `5112`; MediaMTX WHEP uses `8889`; a valid WHEP session and adapter byte movement prove delivery | Video is black/offline. This does not by itself prove GUI module control is healthy. |
| Phone remote touch during OPLINK streaming | GUI_TEST_PC live touch bridge on `5111`, Pico COM device/configuration, `shared_python/src/starcg_bot/pico_touch.py`, OPLINK `host/pico_stream_input.py` and input relay | `http://127.0.0.1:5111/health` reports enabled and GUI_TEST_PC execution ownership | Video can remain visible, but a phone click reports input unavailable or times out. |

## Fault Isolation

1. Video works but the module list, launch, stop, or playback commands fail: check port `5100`, then heartbeat freshness and GUI_TEST_PC itself.
2. Video and module list work but phone clicks fail: check port `5111`, Pico connection/configuration, and GUI_TEST_PC responsiveness.
3. Module cards show stale state after a controller outage: check `runtime_pc/pwa_bridge/gui_heartbeat.json` freshness before trusting the card state. Do not replay old queued commands after an outage.
4. Stream is unavailable but GUI_TEST_PC works locally: check the stream API on `5112`, MediaMTX/WHEP on `8889`, capture router/FFmpeg, and Tailscale Serve.

## Asset Inventory Report

`asset_inventory/GUI_TEST_PC_ASSET_INVENTORY_HANDOFF_20260823.md` documents an isolated, read-only prototype for the future money/weapon report. It has validated OCR fixtures and tests, but is **not deployed** into GUI_TEST_PC, the PWA/native app, OPLINK, Pico input, or any live `修裝` playback.

## Restore Boundary

Restore only after backing up the target files and stopping GUI_TEST_PC plus its PWA server. Deploy the GUI_TEST_PC atomic compatibility set together, then verify `/api/status`, a fresh GUI heartbeat, `5111/health`, and one real phone input before treating the stack as restored. StarCG game processes do not need to be stopped merely to restore source files.
