# iPhone 12 Pro Max real-device test

## Gate A: source identity

1. Open game windows `[01]` and `[15]` and keep both visible for this A/B test.
2. Disable Surfshark or any VPN that owns the Windows default route. Keep Tailscale connected.
3. Run `host\start_stream_test.ps1 -ConfigureTailscaleServe` without an override flag.
4. Confirm the startup report lists slot 1 and 15 with different source sizes but aspect `1.77778`.
5. Open the printed `/oplink-test/api/v1/sources` URL from an iPhone on the same Tailnet.
6. Confirm `identity_source` is `gui_test_pc_pid_map`, `title_rename_ok` is true, and titles are `[01]`/`[15]`.
7. Reject the test if the HWND, PID, process path, or aspect does not match the intended game window.

## Gate B: native stream

1. Install the signed IPA and force landscape orientation.
2. Enter only the printed HTTPS host, for example `https://host-name.example.ts.net`; do not append a path.
3. Select slot 1 and wait 30 seconds.
4. Select slot 15 and wait 30 seconds.
5. Repeat `1 -> 15 -> 1` ten times.

Record these values for every run using the transient input result and host diagnostics:

- Source logical size and expected WGC size.
- Rendered video size.
- Rendered FPS.
- Switch-to-first-frame time.
- Connection state and any visible freeze.
- Input RTT and host-to-HID ACK time after one tap, spaced at least 1.5 seconds apart.
- The backend must report `pico_hid_touch_gui_test_pc`, with `execution_owner` and `relayed_to` both equal to `GUI_TEST_PC`.
- The GUI_TEST_PC live-touch health report must identify `report_mode: touchscreen` and Pico `COM5`.

## Acceptance

- Both sources fill the same maximum 16:9 area without crop, stretch, or desktop content.
- Rendered resolution is 1920x1080 for both slots.
- Rendered FPS remains at least 29 fps during each 30-second observation.
- Every switch renders the first frame within 1000 ms.
- No freeze lasts longer than 300 ms in the observation period.
- A changed source geometry is shown in metadata rather than silently mapped with stale dimensions.
- The underlay line shows Ethernet selected, `USB-WIN NO`, and `DEFAULT ETH`.
- Input RTT is below 300 ms when no slot cooldown wait is active.
- A tap emits one ordered `DOWN -> UP` sequence; a drag emits `DOWN -> MOVE... -> UP` without an extra click.
- Touches in the video black border do not reach the game and still dismiss an expanded stream control.

The displayed input RTT covers iPhone request to host response, while `HOST->HID` covers host receipt through Pico acknowledgement. The game's visual touch-to-photon response still requires observation or a later game-specific image-change probe.
