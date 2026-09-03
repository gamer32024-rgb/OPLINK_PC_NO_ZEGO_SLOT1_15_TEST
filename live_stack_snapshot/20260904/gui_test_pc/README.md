# GUI_TEST_PC_DEV_20260703

Native Windows development copy for `GUI_TEST_PC`.

Current direction:

- Main UI is the Python/PyQt5 desktop app `gui_test_pc.py`.
- `start_gui_test_pc.bat` launches the desktop app, not a browser page.
- `gui_test_pc_server.py` also serves the new GUI_TEST_PC-only mobile PWA at `/mobile/`.
- `start_gui_test_pc_pwa_server.bat` starts the PWA/API server on `0.0.0.0:5100`.
- `start_gui_test_pc_pwa_ngrok.bat` runs `ngrok http 5100` if `ngrok.exe` is already available in `PATH`.
- `install_gui_test_pc_pwa_server_autostart.bat` installs a Windows logon task that starts only the GUI_TEST_PC PWA/API server on port `5100`.
- `uninstall_gui_test_pc_pwa_server_autostart.bat` removes that autostart task.
- OPLINK_PC streaming remains a separate future project; this PWA is a lightweight controller for slots, launcher actions, frame/MJPEG viewing, background taps, and PC script/module playback.
- Old `GUI_TEST`, old `GUI_TEST_PC`, and OPLINK are not modified by this folder.

Desktop tabs:

- `腳本連串`
- `腳本管理`
- `同步器`
- `啟動`
- `日誌`

The `啟動` tab is the core PC launcher page. It calls the GUI_TEST_PC-local launcher control script:

`launcher\starcg_15_control_gui_test_pc.ps1`

The original standalone launcher/control scripts under `D:\15game` are preserved. The local copy starts every slot from the single source `D:\TWFULLPC1.2.76`; `TargetRoot` is retained only as a compatibility parameter.

Network binding:

- GUI_TEST_PC now defaults to `netbind loopback-safe`, not the old `D:\15game\ForceBindIP\BindIP64.dll`.
- `netbind_pc\build_ninja\GuiTestNetBindLauncher.exe` injects `GuiTestNetBindHook64.dll` into the launched slot process.
- The hook binds outbound IPv4 sockets to the slot adapter IP, but skips loopback destinations such as `127.0.239.148:52500`.
- `Bind Test` returns `Preflight` plus `Slots`; `Preflight.VpnDefaultRouteActive=true` means Surfshark/other VPN default route is active and game start is blocked in `netbind` mode.
- Legacy ForceBindIP remains available only as `legacy delayed` / `legacy normal old` for manual comparison.

Mutating launcher actions now run the local control script directly from the GUI worker thread:

- This avoids the previous hidden `Start-Process -Verb RunAs` request that returned success but did not enter `D:\15game\launcher_action.log`.
- The verified non-game test is `restore-login` slot 1, which now reaches `D:\15game\launcher_action.log`.
- `status` and `bind-test` remain non-elevated JSON reads.
- Broken 24-byte `account` files are not accepted as useful login data; known-good 56-byte `account` files are preserved/restored instead.
- GUI crash diagnostics are written to `logs_pc/gui_test_pc_crash.log`.

Launcher actions exposed in the desktop UI:

- Start selected `.exe` game slots.
- Stop selected `.exe` game slots.
- Restart selected `.exe` game slots.
- Start missing slots.
- Repair bad slots.
- Relabel/name game windows as slots 1-15.
- Bind test.
- Timed game actions.
- Timed PC shutdown/reboot.

Slot binding rules:

- Prefer the launcher PID map `D:\15game\gui_test_pc_slot_pids.json` for single-source slot ownership.
- Use `[01]` to `[15]` window titles as the fallback slot signal after the process has a main window.
- Keep `StarCG_slotN\StarCG.exe` process-path detection only as compatibility for old multi-folder runs.
- Exclude LDPlayer/dnplayer windows from PC game slots.
- Use 1-15 slot order for account binding, script recording/playback targets, and later `oplink_pc` streaming source consistency.

Isolated runtime folders:

- `scripts_pc/`
- `config_pc/`
- `logs_pc/`
- `backups_pc/`

Mobile PWA:

- Local URL: `http://127.0.0.1:5100/mobile/`
- LAN/ngrok URL: use the host/ngrok base URL plus `/mobile/`.
- PWA scripts and modules are GUI_TEST_PC-only: `scripts_pc/*.pcscript.json` and `config_pc/modules_pc.json`.
- Phone taps use background window messages through `/api/click`; they do not move the PC mouse cursor.
- Module/script playback uses `/api/play/module` and `/api/play/script`, returns a background play job, and uses the `window_message` backend by default.

Touch Injection playback remains a later validation step after launcher/login binding is stable.

2026-07-05 account/profile isolation update:

- Single-source launch still uses `D:\TWFULLPC1.2.76\StarCG.exe` for every slot.
- NetBind now supports multiple file redirect pairs per process.
- Slot startup redirects `AppData\LocalLow\CrossGate\StarCG` to `AppData\LocalLow\CrossGate\SCG001` through `SCG015`.
- Slot startup also redirects mutable game-root data currently observed under `StarCG_Data\chat` to `D:\15game\account_slots\SCGxxx\game_root\StarCG_Data\chat`.
- The hook patches late-loaded modules after `LoadLibrary*`, and also intercepts dynamic `GetProcAddress` for Win32 file/registry/socket functions.
- Direct `fopen` replacement is intentionally disabled; CRT modules are patched at their lower-level file API imports to avoid mixing `FILE*` ownership across different CRTs.
