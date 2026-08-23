from __future__ import annotations

import argparse
import json
import os
import shutil
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError

from native_single_stream_controller import NativeSingleStreamController
from stream_test_server import source_identity


HOST = Path(__file__).resolve().parent
DEFAULT_ROUTER = (
    HOST / "tools" / "oplink_capture_router" / "oplink_capture_router.exe"
)
DEFAULT_RUNTIME = HOST / "runtime" / "native_single_stream_test"
LEGACY_HEALTH_URL = "http://127.0.0.1:5110/api/v1/health"
MEDIAMTX_API = "http://127.0.0.1:9997"


def fetch_json(url: str, timeout: float = 3.0) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"{url} returned HTTP {response.status}")
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise RuntimeError(f"{url} did not return a JSON object")
    return payload


def path_status(path_name: str) -> dict[str, Any] | None:
    url = f"{MEDIAMTX_API}/v3/paths/get/{path_name}"
    try:
        return fetch_json(url, timeout=0.5)
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    except (URLError, TimeoutError, OSError):
        return None


def resolve_ffmpeg(explicit: Path | None) -> Path:
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(explicit)
    environment_path = os.environ.get("OPLINK_FFMPEG")
    if environment_path:
        candidates.append(Path(environment_path))
    command = shutil.which("ffmpeg")
    if command:
        candidates.append(Path(command))
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        winget_root = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
        if winget_root.is_dir():
            candidates.extend(
                sorted(
                    winget_root.glob("**/ffmpeg.exe"),
                    key=lambda path: str(path),
                    reverse=True,
                )
            )
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if resolved.is_file():
            return resolved
    raise FileNotFoundError("FFmpeg was not found; pass --ffmpeg")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Test one native router and one FFmpeg on a fixed MediaMTX path "
            "without restarting legacy OPLINK_PC."
        )
    )
    parser.add_argument("--router", type=Path, default=DEFAULT_ROUTER)
    parser.add_argument("--ffmpeg", type=Path)
    parser.add_argument("--encoder", choices=("mf", "nvenc", "x264"), default="mf")
    parser.add_argument("--slots", type=int, nargs="+", default=[1, 7, 15])
    parser.add_argument("--cycles", type=int, default=1)
    parser.add_argument("--path-name", default="oplink_active")
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--bitrate-kbps", type=int, default=6000)
    parser.add_argument("--switch-timeout", type=float, default=3.0)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_RUNTIME)
    args = parser.parse_args()

    slots = list(dict.fromkeys(args.slots))
    if not slots or any(slot < 1 or slot > 20 for slot in slots):
        parser.error("--slots must contain values from 1 through 20")
    if args.cycles <= 0:
        parser.error("--cycles must be positive")
    router_exe = args.router.resolve()
    if not router_exe.is_file():
        raise FileNotFoundError(f"native router executable not found: {router_exe}")
    ffmpeg = resolve_ffmpeg(args.ffmpeg)
    if path_status(args.path_name) is not None:
        raise RuntimeError(
            f"MediaMTX path {args.path_name!r} is already online; refusing to replace it"
        )

    identities = {slot: source_identity(slot) for slot in slots}
    failures = {
        slot: identity.get("error")
        for slot, identity in identities.items()
        if not identity.get("ok")
    }
    if failures:
        raise RuntimeError(f"requested slots are not ready: {failures}")

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = args.output_dir.resolve() / run_id
    output_dir.mkdir(parents=True, exist_ok=False)
    legacy_before = fetch_json(LEGACY_HEALTH_URL)
    controller = NativeSingleStreamController(
        router_exe=router_exe,
        ffmpeg=ffmpeg,
        encoder=args.encoder,
        width=args.width,
        height=args.height,
        fps=args.fps,
        bitrate_kbps=args.bitrate_kbps,
        mediamtx_api=MEDIAMTX_API,
        identity_provider=source_identity,
        runtime_dir=output_dir,
        path_name=args.path_name,
        viewer_idle_timeout_seconds=120.0,
        pipeline_start_timeout=8.0,
        switch_timeout=args.switch_timeout,
        state_path=output_dir / "state.json",
    )

    evidence: dict[str, Any] = {
        "run_id": run_id,
        "started_at_ms": int(time.time() * 1000),
        "mode": "native_single_stream_host_test",
        "legacy_services_modified": False,
        "router_exe": str(router_exe),
        "ffmpeg": str(ffmpeg),
        "encoder": args.encoder,
        "path": args.path_name,
        "whep_path": f"{args.path_name}/whep",
        "profile": {
            "width": args.width,
            "height": args.height,
            "fps": args.fps,
            "bitrate_kbps": args.bitrate_kbps,
        },
        "slots": slots,
        "cycles": args.cycles,
        "identities": identities,
        "legacy_health_before": {
            "ok": legacy_before.get("ok"),
            "profile": legacy_before.get("profile"),
            "encoder": legacy_before.get("encoder"),
        },
        "switches": [],
    }
    failure: BaseException | None = None
    expected_router_pid: int | None = None
    expected_publisher_pid: int | None = None
    try:
        for cycle in range(1, args.cycles + 1):
            for slot in slots:
                result = controller.activate(slot)
                router_pid = int(result["router_pid"])
                publisher_pid = int(result["publisher_pid"])
                if expected_router_pid is None:
                    expected_router_pid = router_pid
                    expected_publisher_pid = publisher_pid
                if (
                    router_pid != expected_router_pid
                    or publisher_pid != expected_publisher_pid
                ):
                    raise RuntimeError(
                        "router or FFmpeg PID changed during fixed-path switching"
                    )
                path = path_status(args.path_name)
                if not path or not path.get("ready"):
                    raise RuntimeError(
                        f"MediaMTX path {args.path_name} is not ready after switch"
                    )
                evidence["switches"].append(
                    {
                        "cycle": cycle,
                        "slot": slot,
                        "router_pid": router_pid,
                        "publisher_pid": publisher_pid,
                        "generation": result["switch_generation"],
                        "activation_ms": result["activation_ms"],
                        "pipeline_start_ms": result["pipeline_start_ms"],
                        "pipeline_reused": result["reused"],
                        "first_frame": result["first_frame"],
                        "path_source_id": (path.get("source") or {}).get("id"),
                        "path_inbound_bytes": path.get("inboundBytes"),
                        "path_inbound_frames_in_error": path.get(
                            "inboundFramesInError"
                        ),
                    }
                )
    except BaseException as exc:
        failure = exc
        evidence["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        evidence["status_before_stop"] = controller.status()
        controller.stop()
        deadline = time.monotonic() + 3.0
        while path_status(args.path_name) is not None and time.monotonic() < deadline:
            time.sleep(0.05)
        evidence["path_offline_after_stop"] = path_status(args.path_name) is None
        legacy_after = fetch_json(LEGACY_HEALTH_URL)
        evidence["legacy_health_after"] = {
            "ok": legacy_after.get("ok"),
            "profile": legacy_after.get("profile"),
            "encoder": legacy_after.get("encoder"),
        }
        evidence["finished_at_ms"] = int(time.time() * 1000)
        elapsed_values = [
            float(item["first_frame"]["elapsed_ms"])
            for item in evidence["switches"]
        ]
        ordered = sorted(elapsed_values)
        evidence["latency_ms"] = {
            "count": len(ordered),
            "p50": ordered[(len(ordered) - 1) // 2] if ordered else None,
            "p95": (
                ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
                if ordered
                else None
            ),
            "worst": max(ordered) if ordered else None,
        }
        evidence_path = output_dir / "evidence.json"
        evidence_path.write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    if failure is not None:
        raise failure
    if not evidence["path_offline_after_stop"]:
        raise RuntimeError(f"MediaMTX path {args.path_name} stayed online after stop")
    print(
        json.dumps(
            {
                "ok": True,
                "router_pid": expected_router_pid,
                "publisher_pid": expected_publisher_pid,
                "switches": len(evidence["switches"]),
                "latency_ms": evidence["latency_ms"],
                "path_offline_after_stop": evidence["path_offline_after_stop"],
                "evidence": str(output_dir / "evidence.json"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
