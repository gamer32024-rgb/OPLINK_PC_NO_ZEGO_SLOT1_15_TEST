# Build receipt: 2026-07-17

## GitHub Actions

- Repository: `gamer32024-rgb/OPLINK_PC_NO_ZEGO_SLOT1_15_TEST`
- Workflow: `Build unsigned IPA`
- Run: `29562300025`
- Source commit: `18aab1ed74c2e45cdf1a408cb567841470f2b5ce`
- Result: success
- Duration: 42 seconds
- Artifact: `OPLINKStreamTest-unsigned`
- Artifact ID: `8399741498`
- Artifact ZIP size shown by GitHub: 5.41 MB
- Artifact digest shown by GitHub: `sha256:801b7306e6f9b06a5398abf684ddacd969a34e55098942a81784d7d321cad780`

Run URL:

```text
https://github.com/gamer32024-rgb/OPLINK_PC_NO_ZEGO_SLOT1_15_TEST/actions/runs/29562300025
```

## Downloaded IPA

- Local file: `dist/OPLINKStreamTest-unsigned.ipa`
- Size: 5,681,856 bytes
- SHA-256: `40EF338BF25C5F4791B9EC9BC02C6346CF271C38A2F75E4015D5846E4F80D6D4`
- Contains: `Payload/OPLINKStreamTest.app`, app executable, `Info.plist`, and `WebRTC.framework`
- Code signature: absent by design

This receipt proves that the native project compiled and was packaged by a macOS GitHub runner. It does not prove real-device FPS, switch time, input RTT, or visual touch-to-photon latency. Those remain the acceptance gates in `REAL_DEVICE_TEST.md`.
