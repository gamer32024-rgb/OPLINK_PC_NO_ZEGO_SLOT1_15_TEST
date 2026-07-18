# Build receipt: 2026-07-17

## GitHub Actions

- Repository: `gamer32024-rgb/OPLINK_PC_NO_ZEGO_SLOT1_15_TEST`
- Workflow: `Build unsigned IPA`
- Run: `29645674944`
- Source commit: `f1b0a5d77cde34360bc8e521a2ce7f2e573d6c72`
- Result: success
- Duration: 58 seconds
- Artifact: `OPLINKStreamTest-unsigned`
- Artifact ID: `8429962547`
- Artifact ZIP size shown by GitHub: 5.41 MB
- Artifact digest shown by GitHub: `sha256:423f4d8f848f4168c672925e6d97597494b1cd1fbc8568c034b09093e54511ef`

Run URL:

```text
https://github.com/gamer32024-rgb/OPLINK_PC_NO_ZEGO_SLOT1_15_TEST/actions/runs/29645674944
```

## Downloaded IPA

- Local file: `dist/OPLINKStreamTest-1080p30-v0.2.0.ipa`
- Size: 5,682,295 bytes
- SHA-256: `9F3D790E22C3C059A6A611690BFA91AFFC2F67B4262DB582319807222ED95D99`
- Contains: `Payload/OPLINKStreamTest.app`, app executable, `Info.plist`, and `WebRTC.framework`
- Code signature: absent by design

This receipt proves that the native project compiled and was packaged by a macOS GitHub runner. It does not prove real-device FPS, switch time, input RTT, or visual touch-to-photon latency. Those remain the acceptance gates in `REAL_DEVICE_TEST.md`.
