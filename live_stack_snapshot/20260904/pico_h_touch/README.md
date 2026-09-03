# PICO_H_TOUCH

Isolated Raspberry Pi Pico H workspace for USB CDC Serial plus HID touchscreen
and absolute-mouse script playback.

Verified state on 2026-07-17:

- Pico H runs composite firmware `0.4.1` as `VID_CAFE&PID_4021`.
- The existing touchscreen reports remain available. A second absolute-mouse
  report uses `MDOWN`, `MMOVE`, and `MUP` for one persistent left-button drag.
- The controlled slot 1 probe produced one hardware `left_down`, 30 ordered
  move reports, and one hardware `left_up`; all low-level flags were zero.
- Production host configuration uses `report_mode: absolute_mouse`.
- Verified UF2: `artifacts\pico_h_phase3_composite_mouse_0.4.1.uf2`
- SHA256: `5DE8DBC2A05277E1994A3A703D092AC6128952B8A6C154A867CCF82909979802`
- Rollback UF2: `artifacts\pico_h_phase3_composite_touch_0.3.2_scan_time_fix.uf2`
- The `0.4.0_FAILED_CODE10_DO_NOT_FLASH` artifact is retained only as failure
  evidence. It declares USB 2.01 without a BOS descriptor and must not be used.

Verified state on 2026-07-14:

- Pico H runs composite firmware `0.3.0` as `VID_CAFE&PID_4021`.
- Windows enumerates both `USB Serial Device (COM5)` and a HID-compliant
  touchscreen.
- CDC smoke tests verify `HELLO`, `PING`, `STATUS`, and `CANCEL` with no touch
  contact left active.
- The HID command path uses a 64-report ordered queue so temporary endpoint
  backpressure cannot fail drag playback.
- `BOOTSEL <seq>` safely switches the device back to `RPI-RP2` for later UF2
  updates without holding the physical button.
- The `LOGIN` module completed through Pico HID on slot 1: 250 recorded
  events, 92 complete gestures, 768x432 to 512x288 scaled client coordinates,
  and 1.5-second per-slot gesture cooldown.

Drag fix built on 2026-07-14:

- Firmware source is now `0.3.1`.
- Normal `UP` appends the release report after queued `MOVE` reports instead of
  clearing the HID queue first.
- `CANCEL`, `RESET`, and `BOOTSEL` still clear the queue for recovery.
- Built UF2: `b3\pico_h_phase3_composite_touch.uf2`
- SHA256: `710461139BB694DC4D7BB6525A046DF9B1F51365151362F21306F5EE53A822C1`

Useful commands:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
& .\tools\build_phase3.ps1
& .\tools\cdc_phase3_smoke_test.ps1 -PortName COM5
```

The generic `flash_phase0_bootsel.ps1` name is retained for compatibility; it
can flash any verified UF2 while the Pico exposes `RPI-RP2`.
