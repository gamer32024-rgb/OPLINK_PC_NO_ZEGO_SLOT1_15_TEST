# OPLINK_PC No-ZEGO Slots 1-15 Test

This native iOS proof of concept streams Windows game `.EXE` slots 1 through 15 over WebRTC on a private Tailscale network. It does not use ZEGO, browser playback, CPU screenshots, or desktop-region capture.

The iOS app also exposes the existing `GUI_TEST_PC` mobile-PWA controls. The phone only submits bridge commands. `GUI_TEST_PC` remains the sole owner of module execution, foreground-window scheduling, Pico HID output, cancellation, and launcher actions.

## Streaming architecture

| Item | Rule |
|---|---|
| Selectable sources | Exact verified HWNDs for slots 1 through 15 |
| Identity | GUI_TEST_PC launcher PID map, with `[01]` through `[15]` title fallback |
| Capture | FFmpeg `gfxcapture` Windows Graphics Capture by HWND; occluded windows remain observable |
| Current source geometry | `1280x720` logical at 150% DPI, producing `1920x1080` WGC frames |
| Publisher | Viewer-aware H.264 cache: three while active, current only in background grace, zero after 15 idle seconds |
| Output | H.264 Constrained Baseline, `1920x1080`, 30 fps, 6 Mbps, no B-frames |
| Display | Native iOS `RTCMTLVideoView`, aspect-fit, landscape |
| Network | Tailscale Serve for HTTPS/WHEP; media ICE advertises only the host Tailscale IPv4 |
| Switch target | First rendered frame within 1000 ms |
| Input target | iPhone-to-HID round trip below 300 ms through authenticated live-touch relay |
| Control owner | `GUI_TEST_PC`; iOS calls only the stream input relay and `/gui-test-pc/api/...` bridge endpoints |

The host exposes all 15 source identities, but never keeps 15 encoders running. Before opening WHEP, iOS calls `POST /oplink-test/api/v1/activate` for the selected slot. Windows retains up to three exact-HWND publishers and iOS retains their WHEP peers. A warmed peer moves to the visible renderer immediately, while `/activate` is updated in the background. A cold peer moves only after it has decoded a frame, so the old frame remains visible during startup.

The iOS app sends a viewer heartbeat every three seconds while foregrounded. Entering the background closes all iOS WHEP peers and asks Windows to retain only the current publisher. If no heartbeat and no WHEP session exist for 15 seconds, Windows stops every publisher. Reopening the app starts the current slot first and prewarms adjacent slots only after the first visible frame.

Live game taps and drags remain Pico HID touchscreen reports through GUI_TEST_PC. The legacy-style iOS keyboard panel uses authenticated `text` and `key` messages, then GUI_TEST_PC activates the selected slot and sends Windows keyboard input. Keyboard support never changes touch input to mouse fallback.

This bounded cache is required on the current host. A live 15-publisher trial reached the NVIDIA hardware-session ceiling after eight sessions, while 15 concurrent `libx264` publishers could not maintain 30 fps. Three warm publishers cover sequential previous/next navigation without wasting 15 encoder sessions. A random jump to a slot outside the warm set remains a cold switch and is not claimed to meet the one-second target.

Measured locally on 2026-07-19:

- slots 1, 14, and 15 remained online together at 30 fps and `1920x1080`;
- cold publisher warm-up took 476-478 ms for slots 14 and 15;
- warmed host activation reported 0 ms, with repeated local API wall time normally 5-9 ms;
- each FFmpeg process used about 84 MB working set during the three-publisher test.

These are host-side measurements. The final switch acceptance gate remains the native iPhone first-rendered-frame measurement.

Viewer-aware power validation on 2026-07-19, with the same game windows left running:

- foreground three-publisher state: about `64.8 W` GPU power;
- background one-publisher grace state: about `63.5 W` GPU power;
- idle zero-publisher state: about `60-62 W` GPU power;
- decoder utilization remained zero because Windows captures and encodes; the iPhone performs stream decoding.

## Windows host

Prerequisites:

- All 15 game windows are running and registered in the GUI_TEST_PC PID map.
- Every source is 16:9 and accepted by the active GUI_TEST_PC layout policy.
- The input pairing token is retained across normal host restarts. Use `-RotateInputToken` only when intentionally invalidating paired clients.
- Tailscale is connected on Windows and iPhone.
- FFmpeg includes the `gfxcapture` filter.
- MediaMTX is available at `host/tools/mediamtx/mediamtx.exe`, or passed with `-MediaMTXPath`.
- `GUI_TEST_PC` is running for both live iOS touch and bridge controls.

Start the host from PowerShell:

```powershell
cd host
.\start_stream_test.ps1 -ConfigureTailscaleServe
```

The default input mode keeps `GUI_TEST_PC` as the sole Pico `COM5` owner. `GUI_TEST_PC` exposes a loopback-only live-touch service on `127.0.0.1:5111`; the authenticated stream API relays normalized iOS `DOWN`, `MOVE`, `UP`, and `CANCEL` commands to that service. The stream host never opens Pico in this mode. `-DisableInput` remains available for observation-only tests, while `-DirectPicoInput` is an explicit diagnostic fallback and must not run beside `GUI_TEST_PC` playback.

The encoder selection order is live-probed NVIDIA NVENC, then hardware Media Foundation H.264, then one `libx264` fallback. The current FFmpeg NVENC API is newer than the installed NVIDIA driver API, so this host selects `h264_mf`, which uses the NVIDIA H.264 Encoder MFT.

Acceptance mode is strict. If Surfshark or another VPN owns the overall default route, startup stops before capture begins. `-AllowVpnDefaultRoute` exists only for development and must not be treated as Ethernet acceptance.

Stop only the processes recorded by this project:

```powershell
.\stop_stream_test.ps1
```

The start command prints the Tailnet HTTPS host and a random local pairing token when input is enabled. Runtime diagnostics and the token stay under ignored `host/runtime/` files.

## GUI_TEST_PC bridge contract

The native app reads:

- `GET /gui-test-pc/api/targets`
- `GET /gui-test-pc/api/modules`
- `GET /gui-test-pc/api/play/jobs`

The native app can enqueue:

- `play_module_chain`
- `stop_slot_playback`
- `stop_all_playback`
- `launcher_action`
- `window_layout`

Every successful command response must report `relayed_to: GUI_TEST_PC`, and the jobs endpoint must report `execution_owner: GUI_TEST_PC`. The app has no script player, Pico scheduler, process launcher, or shell execution path.

## iOS app and unsigned IPA

The app provides:

- no persistent top bar over the game stream;
- a movable translucent button that expands to previous/list/next only;
- a short, translucent, scrollable 15-slot list that remains open after selection;
- tap-outside dismissal back to the single floating button;
- normalized live touch `DOWN`, `MOVE`, `UP`, and drag output through GUI_TEST_PC-owned Pico HID;
- a GUI_TEST_PC slot grid using gray/green/red states for unselected/selected/playing;
- a 10-step module chain showing module names only;
- single-slot cancel without stopping other slots;
- start, stop, restart, and window-arrange bridge controls;
- live 1080p/FPS/host-activation/first-frame switch metrics.

The Tailnet HTTPS host is required. The input pairing token is required for live touch, but can remain blank for stream observation plus GUI bridge control.

The iOS project is generated by XcodeGen and uses the community `stasel/WebRTC` XCFramework package. GitHub Actions audits the public repository, generates the Xcode project, resolves Swift packages, builds without code signing, and uploads `OPLINKStreamTest-unsigned.ipa`. The IPA still needs normal Windows-side signing before installation.

No GitHub Actions secret is needed. Do not add a Tailscale auth key, Apple signing certificate, provisioning profile, ZEGO credential, private key, host URL, pairing token, or local runtime file to the repository.

See [docs/REAL_DEVICE_TEST.md](docs/REAL_DEVICE_TEST.md) for the device-test procedure and [PUBLIC_RELEASE_CHECKLIST.md](PUBLIC_RELEASE_CHECKLIST.md) before publishing a release artifact.
