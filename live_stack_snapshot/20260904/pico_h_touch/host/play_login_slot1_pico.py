from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


WORKSPACE = Path(__file__).resolve().parents[2]
SRC_DIR = WORKSPACE / "src"
GUI_DIR = WORKSPACE / "GUI_TEST_PC_DEV_20260703"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from starcg_bot.pc_script import play_pc_script  # noqa: E402
from starcg_bot.windows_device import client_size, list_windows  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Preflight or execute only GUI_TEST_PC module LOGIN on slot 1 through Pico HID touch."
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Send the LOGIN touch gestures after all preflight checks pass.",
    )
    args = parser.parse_args()

    modules_path = GUI_DIR / "config_pc" / "modules_pc.json"
    scripts_dir = GUI_DIR / "scripts_pc"
    pico_config_path = GUI_DIR / "config_pc" / "pico_touch.json"
    module_scripts = _login_module_scripts(modules_path, scripts_dir)
    target = _slot1_target()
    current_width, current_height = client_size(target.hwnd)

    plans: list[dict[str, Any]] = []
    for script_path in module_scripts:
        script = json.loads(script_path.read_text(encoding="utf-8-sig"))
        recorded_size = script.get("client_size") or {}
        events = script.get("events") or []
        gestures = sum(1 for event in events if event.get("type") in {"click", "tap", "drag", "drag_start"})
        plans.append(
            {
                "script": script_path.name,
                "recorded_client_size": recorded_size,
                "current_client_size": {"w": current_width, "h": current_height},
                "event_count": len(events),
                "gesture_count": gestures,
                "minimum_gesture_time_seconds": gestures * 1.5,
            }
        )

    summary: dict[str, Any] = {
        "module": "LOGIN",
        "slot": 1,
        "target": {
            "hwnd": f"0x{target.hwnd:X}",
            "pid": target.pid,
            "title": target.title,
            "process_name": target.process_name,
        },
        "execute": bool(args.execute),
        "plans": plans,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if not args.execute:
        return 0

    results = []
    for script_path in module_scripts:
        results.append(
            play_pc_script(
                script_path=script_path,
                hwnd=target.hwnd,
                expected_slot=1,
                backend="pico_hid_touch",
                pico_config_path=pico_config_path,
                allow_size_mismatch=True,
            )
        )
    print(json.dumps({"ok": True, "results": results}, ensure_ascii=False, indent=2))
    return 0


def _login_module_scripts(modules_path: Path, scripts_dir: Path) -> list[Path]:
    data = json.loads(modules_path.read_text(encoding="utf-8-sig"))
    modules = data.get("modules") if isinstance(data, dict) else None
    if not isinstance(modules, dict) or not isinstance(modules.get("LOGIN"), list):
        raise RuntimeError("modules_pc.json must contain a LOGIN module")
    paths = [scripts_dir / str(name) for name in modules["LOGIN"]]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise RuntimeError(f"LOGIN module has missing scripts: {missing}")
    if not paths:
        raise RuntimeError("LOGIN module has no scripts")
    return paths


def _slot1_target() -> Any:
    matches = [
        window
        for window in list_windows(process_name="StarCG.exe")
        if window.title.strip() == "[01]"
    ]
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one StarCG slot [01] window, found {len(matches)}")
    return matches[0]


if __name__ == "__main__":
    raise SystemExit(main())
