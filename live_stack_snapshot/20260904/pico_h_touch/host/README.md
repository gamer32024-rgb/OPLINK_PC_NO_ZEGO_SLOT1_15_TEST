# Host Bridge

The Windows host owns script timing, per-slot state, and the global Pico HID
input scheduler. The Pico only accepts small CDC commands and emits either one
touchscreen contact or one absolute-mouse left-button gesture.

Current implementation:

- `src/starcg_bot/pico_touch.py` contains the shared `PicoTouchScheduler`.
- The scheduler serializes every gesture globally.
- Ready gestures are served oldest-first to prevent slot starvation. Gestures
  that become ready within the same `50` ms cohort use the lowest slot number
  first.
- A 15-slot module run starts slots `10` seconds apart (`0..140` seconds), then
  the global scheduler applies the click floor to every ready gesture.
- `report_mode: touchscreen` sends `DOWN`, `MOVE`, and `UP`.
- `report_mode: absolute_mouse` sends `MDOWN`, `MMOVE`, and `MUP` while keeping
  the left button down for the complete path.
- Each slot has an independent `min_slot_interval_ms` floor. The current
  production value is `1000` ms.
- Global clicks and repeated coordinates across slots use the same `1000` ms
  floor; there is no coordinate-specific extension.
- Client coordinates are converted to current screen coordinates, then to the
  HID `0..32767` range for the configured touch surface.
- If recorded and current client sizes differ, playback remains blocked unless
  the explicit size-mismatch override is enabled. The override scales recorded
  client coordinates proportionally.
- The default focus policy is strict. The active configuration may allow an
  unfocused target only when `WindowFromPoint` proves that the exact screen
  point belongs to that target window and is not covered.

Configuration lives at
`GUI_TEST_PC_DEV_20260703/config_pc/pico_touch.json`. It intentionally needs
an explicit `enabled` flag, `report_mode`, and COM port. Do not assume a COM
number after a USB reconnect; verify it against `VID_CAFE&PID_4021` first.

The low-level drag diagnostic is:

```powershell
& 'C:\Users\andyb\Documents\star_cros_bot\.venv\Scripts\python.exe' `
  'C:\Users\andyb\Documents\New project\PICO_H_TOUCH\host\pico_drag_mouse_probe.py' `
  --slot 1 --hwnd 0xA060C --tail-ms 1000
```

The probe keeps its mouse hook active for one second after the sender finishes
so a delayed HID button release is not omitted from the log.

For the current single-slot hardware test, use:

```powershell
& 'C:\Users\andyb\Documents\star_cros_bot\.venv\Scripts\python.exe' `
  'C:\Users\andyb\Documents\New project\PICO_H_TOUCH\host\play_login_slot1_pico.py'
```

Add `--execute` only after the preflight target, client size, and gesture count
are correct. The tool is deliberately pinned to module `LOGIN`, slot `1`, and
window title `[01]`.
