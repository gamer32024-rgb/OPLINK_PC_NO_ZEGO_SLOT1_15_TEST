# Build receipt: 2026-07-18

## GitHub Actions

- Repository: `gamer32024-rgb/OPLINK_PC_NO_ZEGO_SLOT1_15_TEST`
- Workflow: `Build unsigned IPA`
- Run: `29648324663` / run number 7
- Source commit: `60b5b67b28bc3612746eba3f98624ced78380ccd`
- Result: success
- Completed: `2026-07-18T14:36:09Z`
- Artifact: `OPLINKStreamTest-unsigned`
- Artifact ID: `8430710890`
- Artifact ZIP size reported by GitHub: `5,762,170` bytes

Run URL:

```text
https://github.com/gamer32024-rgb/OPLINK_PC_NO_ZEGO_SLOT1_15_TEST/actions/runs/29648324663
```

## Downloaded IPA

- Local file: `dist/OPLINKStreamTest-slots1-15-gui-bridge-v0.3.1-run7.ipa`
- Size: `5,771,423` bytes
- SHA-256: `4D2FA6563C6BEEA9AF8819574795D7D7A39549FC27141FD5D981A265D3B19D80`
- Bundle ID: `com.gamer32024.oplink.streamtest`
- Version: `0.3.1` build `5`
- Display name: `OPLINK Stream Test`
- Contains: `Payload/OPLINKStreamTest.app`, app executable, `Info.plist`, and `WebRTC.framework`
- Embedded provisioning profile: absent by design
- Code signature: absent by design

This receipt proves that the 15-slot native stream selector, GUI_TEST_PC bridge UI, and single-active publisher API client compiled and packaged on a GitHub macOS runner. It does not prove native iPhone first-frame switch time, rendered FPS, input RTT, or visual touch-to-photon latency. Those remain real-device acceptance gates.
