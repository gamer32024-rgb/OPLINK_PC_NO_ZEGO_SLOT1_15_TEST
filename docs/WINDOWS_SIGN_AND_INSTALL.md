# Windows signing and installation

`OPLINKStreamTest-unsigned.ipa` is a complete native iOS package but has no Apple code signature. iOS will reject it until a Windows sideloading tool signs it for the target iPhone.

## Existing host tooling

This Windows host currently has Sideloadly at:

```text
C:\Users\andyb\AppData\Local\Sideloadly\Sideloadly.exe
```

The previous OPLINK repository also contains a reminder/launcher helper at:

```text
C:\Users\andyb\Downloads\OPLINK_iOS_Native-main\windows\oplink_resign_automation
```

That helper does not sign an IPA by itself and does not store Apple credentials. It starts or monitors the signing GUI.

## Install this build

1. Open Sideloadly on Windows.
2. Select `dist\OPLINKStreamTest-unsigned.ipa`.
3. Select the iPhone 12 Pro Max and sign with the Apple ID used for sideloading.
4. Install the app and trust the developer profile if iOS requests it.
5. Keep the iPhone on Tailscale for streaming. USB is not part of the stream transport.
6. With a free Apple ID, repeat the signing/install flow before the development signature expires.

Do not commit an Apple password, app-specific password, `.p12`, `.mobileprovision`, Tailscale key, or runtime pairing token to this public repository.
