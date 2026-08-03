from __future__ import annotations

import argparse
import json
import os
import sys
import time


def emit(event: dict[str, object]) -> None:
    sys.stderr.write(json.dumps(event, separators=(",", ":")) + "\n")
    sys.stderr.flush()


def parse_command(line: str) -> tuple[str, dict[str, str]]:
    parts = line.split()
    if not parts:
        return "", {}
    fields: dict[str, str] = {}
    for token in parts[1:]:
        key, separator, value = token.partition("=")
        if not separator or not key or not value:
            raise ValueError(f"invalid command token: {token}")
        fields[key] = value
    return parts[0], fields


def first_frame(generation: int, slot: int, elapsed_ms: int = 7) -> dict[str, object]:
    return {
        "event": "first_frame",
        "generation": generation,
        "slot": slot,
        "source_w": 1920,
        "source_h": 1080,
        "output_w": 1920,
        "output_h": 1080,
        "elapsed_ms": elapsed_ms,
        "stdout_frame_index": generation,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=(
            "normal",
            "stale",
            "future",
            "timeout",
            "malformed",
            "crash",
            "remote-error",
            "wrong-slot",
            "first-before-start",
            "ignore-stop",
            "no-ready",
        ),
        default="normal",
    )
    parser.add_argument("--stdout-bytes", type=int, default=0)
    parser.add_argument("--first-frame-delay", type=float, default=0.0)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--fps", type=int, required=True)
    args = parser.parse_args()

    remaining = max(0, args.stdout_bytes)
    chunk = b"\0" * (64 * 1024)
    while remaining:
        size = min(remaining, len(chunk))
        sys.stdout.buffer.write(chunk[:size])
        sys.stdout.buffer.flush()
        remaining -= size

    if args.mode == "no-ready":
        time.sleep(60)

    emit(
        {
            "event": "ready",
            "pid": os.getpid(),
            "width": args.width,
            "height": args.height,
            "fps": args.fps,
        }
    )

    previous_generation: int | None = None
    previous_slot: int | None = None
    started = False
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            verb, fields = parse_command(line)
        except ValueError:
            emit(
                {
                    "event": "error",
                    "code": "BAD_COMMAND",
                    "recoverable": True,
                }
            )
            continue

        if verb == "STOP":
            if args.mode == "ignore-stop":
                time.sleep(60)
            emit({"event": "stopped", "reason": fields.get("reason", "")})
            return 0
        if verb == "START":
            expected = {
                "width": str(args.width),
                "height": str(args.height),
                "fps": str(args.fps),
                "format": "bgra",
            }
            if started or fields != expected:
                emit(
                    {
                        "event": "error",
                        "code": "START_PROFILE_MISMATCH",
                        "recoverable": True,
                    }
                )
                continue
            started = True
            emit(
                {
                    "event": "started",
                    "width": args.width,
                    "height": args.height,
                    "fps": args.fps,
                    "format": "bgra",
                }
            )
            continue
        if verb != "SWITCH":
            emit(
                {
                    "event": "error",
                    "code": "BAD_COMMAND",
                    "recoverable": True,
                }
            )
            continue
        if not started:
            emit(
                {
                    "event": "error",
                    "code": "NOT_STARTED",
                    "recoverable": True,
                }
            )
            continue

        generation = int(fields["generation"], 10)
        slot = int(fields["slot"], 10)
        hwnd = int(fields["hwnd"], 0)
        started = {
            "event": "switch_started",
            "generation": generation,
            "slot": slot,
            "hwnd": f"0x{hwnd:x}",
            "at_ms": int(time.time() * 1000),
            "command": line,
        }

        if args.mode == "malformed":
            sys.stderr.write("{malformed-json\n")
            sys.stderr.flush()
            continue
        if args.mode == "crash":
            emit(started)
            os._exit(23)
        if args.mode == "timeout":
            emit(started)
            time.sleep(60)
        if args.mode == "remote-error":
            emit(started)
            emit(
                {
                    "event": "error",
                    "generation": generation,
                    "slot": slot,
                    "code": "HWND_DESTROYED",
                    "recoverable": True,
                }
            )
            continue
        if args.mode == "wrong-slot":
            started["slot"] = 15 if slot != 15 else 14
            emit(started)
            continue
        if args.mode == "first-before-start":
            emit(first_frame(generation, slot))
            emit(started)
            continue

        emit(started)
        if args.mode == "stale" and previous_generation is not None:
            emit(first_frame(previous_generation, int(previous_slot)))
        if args.mode == "future":
            emit(first_frame(generation + 1, slot))
            continue
        if args.first_frame_delay:
            time.sleep(args.first_frame_delay)
        emit(first_frame(generation, slot))
        previous_generation = generation
        previous_slot = slot

    emit({"event": "stopped", "reason": "stdin_eof"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
