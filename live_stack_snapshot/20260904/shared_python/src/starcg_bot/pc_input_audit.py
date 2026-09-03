from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
from typing import Any


@dataclass(frozen=True)
class InputMethodFinding:
    name: str
    implemented_in: str
    current_use: str
    windows_path: str
    detectable_signal: str
    recommended_use: str
    playback_allowed_by_default: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "implemented_in": self.implemented_in,
            "current_use": self.current_use,
            "windows_path": self.windows_path,
            "detectable_signal": self.detectable_signal,
            "recommended_use": self.recommended_use,
            "playback_allowed_by_default": self.playback_allowed_by_default,
        }


def build_input_method_findings() -> list[InputMethodFinding]:
    return [
        InputMethodFinding(
            name="post_message",
            implemented_in="src/starcg_bot/windows_device.py",
            current_use=(
                "WindowsWindowController.tap/keyevent/text; run-scenario-window and pc-play-script "
                "only with explicit opt-in."
            ),
            windows_path="Posts WM_* messages directly to a target HWND.",
            detectable_signal=(
                "Does not follow the normal foreground cursor path and can be distinguished from "
                "physical mouse input by local client code."
            ),
            recommended_use="Local UI smoke tests only; do not treat as real-user input.",
            playback_allowed_by_default=False,
        ),
        InputMethodFinding(
            name="send_input_foreground",
            implemented_in="starcg_input.ps1",
            current_use="Manual PowerShell helper for foreground click/key/text tests.",
            windows_path="Moves the cursor and calls user32 SendInput on the foreground desktop.",
            detectable_signal=(
                "Low-level hooks can receive LLMHF_INJECTED or related flags; foreground activation "
                "and timing are also observable locally."
            ),
            recommended_use="Compatibility testing for owned/local apps; not an undetectable playback method.",
            playback_allowed_by_default=False,
        ),
        InputMethodFinding(
            name="windows_touch_injection",
            implemented_in="not implemented",
            current_use="Candidate only; tap and drag script primitives can map to synthetic touch contacts.",
            windows_path="Would use InitializeTouchInjection/InjectTouchInput to send touch/pointer input.",
            detectable_signal=(
                "Still synthetic OS input, not HID hardware from a physical touch panel. Apps can handle "
                "touch/pointer/raw-input paths differently, and local detection can compare hardware "
                "capabilities, pointer source, foreground state, and event timing."
            ),
            recommended_use=(
                "Only test as a local compatibility backend if the target app genuinely supports touch; "
                "do not treat it as an anti-detection method."
            ),
            playback_allowed_by_default=False,
        ),
        InputMethodFinding(
            name="manual_mouse_probe",
            implemented_in="tools/pc_manual_click_probe.py; src/starcg_bot/pc_script.py",
            current_use="Passive probing and pc-record-script recording for real mouse events and target client coordinates.",
            windows_path="Installs a WH_MOUSE_LL hook and records events without injecting input.",
            detectable_signal="Records whether Windows flagged an event as injected; it does not send input.",
            recommended_use="Use this for coordinate calibration and manual recording.",
            playback_allowed_by_default=False,
        ),
    ]


def summarize_probe_log(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    counts: dict[str, int] = {}
    total = 0
    first_event: dict[str, Any] | None = None
    last_event: dict[str, Any] | None = None
    target_windows: dict[str, int] = {}

    with target.open("r", encoding="utf-8") as file:
        for line in file:
            text = line.strip()
            if not text:
                continue
            event = json.loads(text)
            total += 1
            source = str(event.get("source", "unknown"))
            counts[source] = counts.get(source, 0) + 1
            root_hwnd_hex = str(event.get("target", {}).get("root_hwnd_hex", "0x0"))
            target_windows[root_hwnd_hex] = target_windows.get(root_hwnd_hex, 0) + 1
            if first_event is None:
                first_event = event
            last_event = event

    return {
        "path": str(target),
        "total": total,
        "source_counts": counts,
        "target_windows": target_windows,
        "first_event": _event_preview(first_event),
        "last_event": _event_preview(last_event),
    }


def audit_report(probe_log: str | Path | None = None) -> dict[str, Any]:
    report: dict[str, Any] = {
        "scope": "GUI_TEST_PC Windows input method audit",
        "guarantee": (
            "This audit does not and cannot guarantee that a game client, anti-cheat, or server "
            "will accept automation as physical user input."
        ),
        "methods": [finding.to_dict() for finding in build_input_method_findings()],
    }
    if probe_log:
        report["probe_log"] = summarize_probe_log(probe_log)
    return report


def _event_preview(event: dict[str, Any] | None) -> dict[str, Any] | None:
    if event is None:
        return None
    target = event.get("target", {})
    return {
        "ts": event.get("ts"),
        "event": event.get("event"),
        "source": event.get("source"),
        "flags": event.get("flags"),
        "screen": event.get("screen"),
        "target": {
            "root_hwnd_hex": target.get("root_hwnd_hex"),
            "pid": target.get("pid"),
            "title": target.get("title"),
            "client": target.get("client"),
        },
    }
