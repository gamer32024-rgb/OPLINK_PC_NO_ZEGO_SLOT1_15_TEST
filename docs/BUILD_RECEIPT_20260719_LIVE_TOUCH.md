# OPLINK_PC live-touch IPA build receipt

## Source

- Repository: `gamer32024-rgb/OPLINK_PC_NO_ZEGO_SLOT1_15_TEST`
- Branch: `main`
- Build commit: `8bf0686aba427e83b75db3f555b085e1fe842cb8`
- GitHub Actions workflow: `Build unsigned IPA`
- Workflow run: `29651124309` (`run 9`)
- Result: `success`

## Artifact

- GitHub artifact ID: `8431499609`
- Artifact name: `OPLINKStreamTest-unsigned`
- Local IPA: `dist/OPLINKStreamTest-live-touch-v0.4.0-run9-unsigned.ipa`
- IPA SHA-256: `EE010D8CCB6EC150304C4AA43DC9625A2033FE6736B68383DF1EB5729945BE3E`
- IPA size: `5,784,642` bytes

## Package identity

- Bundle identifier: `com.gamer32024.oplink.streamtest`
- Display name: `OPLINK Stream Test`
- Version: `0.4.0`
- Build: `6`
- Code signature: none; Windows sideload signing is still required

## Verified scope

- Xcode 16.4 compiled the native iOS WebRTC app and packaged the unsigned IPA.
- The persistent top bar is not added to the stream view.
- The floating selector expands to previous, list, and next controls only.
- The slot list is a short translucent scrolling panel and remains open after selection.
- iOS emits ordered normalized `DOWN`, `MOVE`, `UP`, and `CANCEL` events.
- The authenticated host endpoint relays input only to loopback `GUI_TEST_PC` live touch.
- `GUI_TEST_PC` remains the sole Pico HID and module-playback owner.

The cloud build proves compilation and packaging. Native touch-to-game response, drag feel, input RTT, and visual layout remain real-device acceptance checks.
