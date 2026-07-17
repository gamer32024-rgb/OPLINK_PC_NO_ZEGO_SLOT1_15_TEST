# Build receipt: 2026-07-17

## GitHub Actions

- Repository: `gamer32024-rgb/OPLINK_PC_NO_ZEGO_SLOT1_15_TEST`
- Workflow: `Build unsigned IPA`
- Run: `29563093515`
- Source commit: `d121f0ca581257fe12149496ecd5476e91023a21`
- Result: success
- Duration: 55 seconds
- Artifact: `OPLINKStreamTest-unsigned`
- Artifact ID: `8400039670`
- Artifact ZIP size shown by GitHub: 5.41 MB
- Artifact digest shown by GitHub: `sha256:2b54a17b5902b524e0991293787da009afcf7bd58221a43555f6f82a967d4f5b`

Run URL:

```text
https://github.com/gamer32024-rgb/OPLINK_PC_NO_ZEGO_SLOT1_15_TEST/actions/runs/29563093515
```

## Downloaded IPA

- Local file: `dist/OPLINKStreamTest-unsigned-run3.ipa`
- Size: 5,682,306 bytes
- SHA-256: `4E90635E326845351C8D4BFCFE7499A16F5D3D7A13EE96431804318626B7723A`
- Contains: `Payload/OPLINKStreamTest.app`, app executable, `Info.plist`, and `WebRTC.framework`
- Code signature: absent by design

This receipt proves that the native project compiled and was packaged by a macOS GitHub runner. It does not prove real-device FPS, switch time, input RTT, or visual touch-to-photon latency. Those remain the acceptance gates in `REAL_DEVICE_TEST.md`.
