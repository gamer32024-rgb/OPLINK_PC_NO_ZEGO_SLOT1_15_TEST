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
| Publisher | One hardware H.264 publisher for the slot currently selected by the iPhone |
| Output | H.264 Constrained Baseline, `1920x1080`, 30 fps, 6 Mbps, no B-frames |
| Display | Native iOS `RTCMTLVideoView`, aspect-fit, landscape |
| Network | Tailscale Serve for HTTPS/WHEP; media ICE advertises only the host Tailscale IPv4 |
| Switch target | First rendered frame within 1000 ms |
| Input target | iPhone-to-HID round trip below 300 ms through authenticated live-touch relay |
| Control owner | `GUI_TEST_PC`; iOS calls only the stream input relay and `/gui-test-pc/api/...` bridge endpoints |

The host exposes all 15 source identities, but never keeps 15 encoders running. Before opening WHEP, iOS calls `POST /oplink-test/api/v1/activate` for the selected slot. Windows stops the old exact-HWND publisher, starts the new one, waits until its MediaMTX path is online, and then returns success.

This design is required on the current host. A live 15-publisher trial reached the NVIDIA hardware-session ceiling after eight sessions, while 15 concurrent `libx264` publishers could not maintain 30 fps. The single-active publisher keeps the correct observation behavior during GUI_TEST_PC playback without wasting 15 encoder sessions.

Measured locally on 2026-07-18:

- all slots 1 through 15 activated successfully;
- non-reused publisher activation averaged about 357 ms and never exceeded 396 ms;
- exactly one FFmpeg process remained alive throughout the sweep;
- RTSP probe reported H.264 Constrained Baseline, `1920x1080`, `30/1` fps, and zero B-frames.

These are host-side measurements. The final switch acceptance gate remains the native iPhone first-rendered-frame measurement.

## Windows host

Prerequisites:

- All 15 game windows are running and registered in the GUI_TEST_PC PID map.
- Every source is 16:9 and accepted by the active GUI_TEST_PC layout policy.
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
