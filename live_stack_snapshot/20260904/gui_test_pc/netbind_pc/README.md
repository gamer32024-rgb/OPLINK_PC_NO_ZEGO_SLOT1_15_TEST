# GUI_TEST_PC netbind

This is a GUI_TEST_PC-owned replacement path for the old `D:\15game\ForceBindIP`
launcher.

Design target:

- Launch `StarCG.exe` through `GuiTestNetBindLauncher.exe`.
- Inject `GuiTestNetBindHook64.dll` into the child process before it starts.
- Bind outbound IPv4 sockets to the configured adapter IP.
- Never bind loopback destinations such as `127.0.239.148:52500`.

The old ForceBindIP binary is not deleted. GUI_TEST_PC can select this path as
the default binder and keep the legacy binder only as a manual fallback.

Build:

```bat
build_netbind_pc.bat
```

Runtime logs are written to `logs_pc\gui_test_pc_netbind_hook.log` when the
launcher is used from GUI_TEST_PC.

2026-07-05 redirect behavior:

- `GuiTestNetBindLauncher.exe` accepts repeated `--redirect-pair FROM TO`
  arguments.
- The hook still accepts the legacy single `--redirect-from` / `--redirect-to`
  pair for compatibility.
- The first redirect pair is used to infer the default registry redirect from
  `Software\CrossGate\StarCG` to the slot name.
- Smoke coverage includes CreateFileW, CreateFileA, fopen through patched CRT
  internals, and a late-loaded DLL writing after LoadLibraryW.
