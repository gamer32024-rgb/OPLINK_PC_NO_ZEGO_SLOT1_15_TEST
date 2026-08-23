# OPLINK_PC No-ZEGO Slots 1-20 Test

This native iOS proof of concept streams Windows game `.EXE` slots 1 through 20 over WebRTC on a private Tailscale network. It does not use ZEGO, browser playback, CPU screenshots, or desktop-region capture.

The iOS app also exposes the existing `GUI_TEST_PC` mobile-PWA controls. The phone only submits bridge commands. `GUI_TEST_PC` remains the sole owner of module execution, foreground-window scheduling, Pico HID output, cancellation, and launcher actions.

## Streaming architecture

| Item | Rule |
|---|---|
| Selectable sources | Exact verified HWNDs for slots 1 through 20 |
| Identity | GUI_TEST_PC launcher PID map, with `[01]` through `[20]` title fallback |
| Capture | Native Windows Graphics Capture/D3D11 router by exact HWND; occluded windows remain observable |
| Current source geometry | `853x480` logical at 150% DPI, producing a `1280x720` physical client capture |
| Publisher | One on-demand H.264 stream at the fixed MediaMTX path `oplink_active` |
| Output | H.264, `1280x720`, 30 fps, host-configurable bitrate, no B-frames |
| Display | Native iOS `RTCMTLVideoView`, aspect-fit, landscape |
| Network | Tailscale Serve for HTTPS/WHEP; media ICE advertises only the host Tailscale IPv4 |
| Switch target | First rendered frame within 1000 ms |
| Input target | iPhone-to-HID round trip below 300 ms through authenticated live-touch relay |
| Control owner | `GUI_TEST_PC`; iOS calls only the stream input relay and `/gui-test-pc/api/...` bridge endpoints |

The host exposes all 20 source identities but runs only one capture router, one encoder, and one WHEP stream. Before showing a slot, iOS calls `POST /oplink-test/api/v1/activate`; Windows switches the existing router to that slot's verified HWND without rebuilding the MediaMTX path. Slots 1 through 20 therefore do not use adjacent-slot warm streams.

The iOS app sends a viewer heartbeat every three seconds while foregrounded. It keeps the WHEP peer for at most one second after entering the background, allowing a very short app switch to resume immediately without continuously spending mobile data. After that grace it deletes the WHEP session. If no viewer remains for 15 seconds, Windows stops the router and encoder. Foreground recovery retries failed activation/WHEP requests and replaces a stale peer when a new first frame does not arrive.

Live game taps and drags remain Pico HID touchscreen reports through GUI_TEST_PC. The legacy-style iOS keyboard panel uses authenticated `text` and `key` messages, then GUI_TEST_PC activates the selected slot and sends Windows keyboard input. Keyboard support never changes touch input to mouse fallback.

Host-side HWND switching does not prove the complete phone-visible delay. Acceptance still requires the native iPhone first-rendered-frame metric, background/foreground recovery, and a MediaMTX check that no more than one WHEP reader remains.

## Windows host

Prerequisites:

- At least one game window is running and registered in the GUI_TEST_PC PID map; the API keeps all Slots 1-20 addressable as they are opened or closed.
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

The autostart watchdog reads `host/stream_profile.json`. Change only the PC encoder bitrate, without rebuilding the IPA, with:

```powershell
.\set_stream_bitrate.ps1 -BitrateKbps 1600
```

The command waits for the new profile to become healthy. If it fails, it restores the preceding bitrate so one experiment cannot leave the watchdog retrying a broken profile indefinitely.

### Windows logon autostart

Windows Graphics Capture and GUI_TEST_PC require an interactive desktop, so persistence starts after Windows user logon rather than before the sign-in screen. Install the watchdog once:

```powershell
cd host
.\install_stream_autostart.ps1 -StartNow
```

The watchdog never launches GUI_TEST_PC or game windows. It waits for GUI_TEST_PC and at least one running Slot, then starts the 720p/30 fps native-single host with Tailscale Serve. The health API continues to expose Slots 1-20, marking closed Slots unavailable until GUI_TEST_PC opens them. It checks both the `5112` API and MediaMTX API continuously and restarts only the verified stream-host processes after an unexpected exit. The existing GUI_TEST_PC launcher remains the sole owner of the per-launch NetBind and multi-instance bypass sequence.

The Startup shortcut is `OPLINK_PC Stream Host.lnk`. Diagnostics are written to `host/runtime/autostart.log`. Remove persistence with:

```powershell
.\uninstall_stream_autostart.ps1 -StopStreamHost
```

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
- live 720p/FPS/host-activation/first-frame switch metrics.

The Tailnet HTTPS host is required. The input pairing token is required for live touch, but can remain blank for stream observation plus GUI bridge control.

The iOS project is generated by XcodeGen and uses the community `stasel/WebRTC` XCFramework package. GitHub Actions audits the public repository, generates the Xcode project, resolves Swift packages, builds without code signing, and uploads `OPLINKStreamTest-unsigned.ipa`. The IPA still needs personal Apple signing, for example through SideStore, before installation.

No GitHub Actions secret is needed. Do not add a Tailscale auth key, Apple signing certificate, provisioning profile, ZEGO credential, private key, host URL, pairing token, or local runtime file to the repository.

See [docs/REAL_DEVICE_TEST.md](docs/REAL_DEVICE_TEST.md) for the device-test procedure and [PUBLIC_RELEASE_CHECKLIST.md](PUBLIC_RELEASE_CHECKLIST.md) before publishing a release artifact.
