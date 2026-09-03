from __future__ import annotations

from dataclasses import fields
from datetime import datetime
import json
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable

from PIL import Image

from .battle_interrupt import (
    BattleInterruptMachine,
    BattleInterruptPolicy,
    DecisionKind,
)
from .battle_interrupt_vision import BattleVisionAnalyzer, DeduplicatingCaptureCollector
from .slot_limits import MAX_SLOT, MIN_SLOT


BATTLE_INTERRUPT_FORMAT = "gui_test_pc_battle_interrupt_v1"
CAPTURE_ROOT_MARKER = ".gui_test_pc_battle_capture_root"
CAPTURE_CLEANUP_STAMP = ".last_image_cleanup_date"
CAPTURE_IMAGE_SUFFIXES = frozenset({".bmp", ".jpeg", ".jpg", ".png", ".webp"})
_CAPTURE_CLEANUP_LOCK = threading.Lock()
_CAPTURE_CLEANUP_DATES: dict[str, str] = {}


def is_battle_interrupt_descriptor(path: str | Path) -> bool:
    return Path(path).name.casefold().endswith(".battle.json")


def load_battle_interrupt_descriptor(path: str | Path) -> dict[str, Any]:
    descriptor_path = Path(path).resolve()
    payload = json.loads(descriptor_path.read_text(encoding="utf-8-sig"))
    if payload.get("format") != BATTLE_INTERRUPT_FORMAT:
        raise ValueError(
            f"unsupported battle interrupt format in {descriptor_path}: "
            f"{payload.get('format')!r}"
        )
    mode = str(payload.get("mode") or "").strip().casefold()
    if mode not in {"dry_run", "active"}:
        raise ValueError("battle interrupt mode must be 'dry_run' or 'active'")
    allowed_slots = [int(value) for value in payload.get("allowed_slots") or []]
    if (
        not allowed_slots
        or len(set(allowed_slots)) != len(allowed_slots)
        or any(slot < MIN_SLOT or slot > MAX_SLOT for slot in allowed_slots)
    ):
        raise ValueError(
            f"battle interrupt allowed_slots must be unique SLOT numbers "
            f"from {MIN_SLOT} to {MAX_SLOT}"
        )
    return payload


def cleanup_old_capture_images(
    capture_root: str | Path,
    *,
    now: datetime | None = None,
    marker_name: str = CAPTURE_ROOT_MARKER,
) -> dict[str, Any]:
    """Delete only image files outside today's local date from a guarded root."""
    root = Path(capture_root).resolve()
    marker = str(marker_name).strip()
    if not marker or Path(marker).name != marker:
        raise ValueError("capture cleanup marker must be a file name")
    if root == Path(root.anchor):
        raise ValueError(f"refusing capture cleanup at drive root: {root}")

    local_now = (now or datetime.now().astimezone()).astimezone()
    today_text = local_now.date().isoformat()
    root_key = os.path.normcase(str(root))
    with _CAPTURE_CLEANUP_LOCK:
        if _CAPTURE_CLEANUP_DATES.get(root_key) == today_text:
            return {"status": "already_checked", "deleted": 0, "failed": 0}
        if not root.is_dir():
            raise FileNotFoundError(f"capture root is unavailable: {root}")
        guard_path = root / marker
        if not guard_path.is_file():
            raise RuntimeError(f"capture cleanup guard is missing: {guard_path}")

        stamp_path = root / CAPTURE_CLEANUP_STAMP
        try:
            if stamp_path.read_text(encoding="ascii").strip() == today_text:
                _CAPTURE_CLEANUP_DATES[root_key] = today_text
                return {"status": "already_checked", "deleted": 0, "failed": 0}
        except FileNotFoundError:
            pass
        except OSError:
            # A stale/unreadable stamp must not prevent a safe image-only scan.
            pass

        deleted = 0
        failed = 0
        for current_root, directory_names, file_names in os.walk(
            root,
            topdown=True,
            followlinks=False,
        ):
            current_path = Path(current_root)
            directory_names[:] = [
                name
                for name in directory_names
                if not _is_link_or_junction(current_path / name)
            ]
            for file_name in file_names:
                candidate = current_path / file_name
                if candidate.suffix.casefold() not in CAPTURE_IMAGE_SUFFIXES:
                    continue
                if _is_link_or_junction(candidate):
                    continue
                try:
                    resolved = candidate.resolve(strict=True)
                    resolved.relative_to(root)
                    modified_date = datetime.fromtimestamp(
                        resolved.stat().st_mtime
                    ).astimezone().date()
                    if modified_date != local_now.date():
                        resolved.unlink()
                        deleted += 1
                except (OSError, ValueError):
                    failed += 1

        _CAPTURE_CLEANUP_DATES[root_key] = today_text
        if failed == 0:
            stamp_path.write_text(today_text + "\n", encoding="ascii")
        return {"status": "checked", "deleted": deleted, "failed": failed}


def _is_link_or_junction(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


def run_battle_interrupt_module(
    *,
    descriptor_path: str | Path,
    hwnd: int,
    expected_slot: int,
    pico_config_path: str | Path | None,
    stop_requested: Callable[[], bool] | None = None,
    progress_callback: Callable[[str], None] | None = None,
    playback_coordinator: Any | None = None,
    playback_handle: int | None = None,
    capture_image: Callable[[], Image.Image] | None = None,
    action_executor: Callable[[DecisionKind, tuple[int, int]], float | None] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Run one exact-HWND battle detector without using HWND message input."""
    config_path = Path(descriptor_path).resolve()
    payload = load_battle_interrupt_descriptor(config_path)
    slot = int(expected_slot)
    allowed_slots = {int(value) for value in payload["allowed_slots"]}
    if slot not in allowed_slots:
        raise RuntimeError(f"battle interrupt descriptor does not allow SLOT {slot}")
    if (playback_coordinator is None) != (playback_handle is None):
        raise RuntimeError("playback_coordinator and playback_handle must be provided together")

    def emit(message: str) -> None:
        if progress_callback is not None:
            progress_callback(message)

    mode = str(payload["mode"]).casefold()
    poll_seconds = max(0.05, float(payload.get("poll_ms", 250)) / 1000.0)
    policy_values = payload.get("policy") or {}
    allowed_policy_names = {item.name for item in fields(BattleInterruptPolicy)}
    policy = BattleInterruptPolicy(
        **{
            key: value
            for key, value in policy_values.items()
            if key in allowed_policy_names
        }
    )
    analyzer = BattleVisionAnalyzer(config_path)
    machine = BattleInterruptMachine(policy)

    capture_root_value = payload.get("capture_root") or "../diagnostics_pc/battle_interrupt"
    capture_root = Path(str(capture_root_value))
    if not capture_root.is_absolute():
        capture_root = (config_path.parent / capture_root).resolve()
    capture_cleanup: dict[str, Any] = {}
    cleanup_config = payload.get("capture_cleanup") or {}
    if bool(cleanup_config.get("daily", False)):
        try:
            capture_cleanup = cleanup_old_capture_images(
                capture_root,
                marker_name=str(cleanup_config.get("marker") or CAPTURE_ROOT_MARKER),
            )
        except Exception as exc:
            capture_cleanup = {
                "status": "warning",
                "deleted": 0,
                "failed": 1,
                "error": f"{type(exc).__name__}: {exc}",
            }
            emit(f"中斷戰鬥圖片清理略過，不影響操作：{capture_cleanup['error']}")
    run_stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
    run_root = capture_root / f"slot_{slot:02d}" / run_stamp
    collector = DeduplicatingCaptureCollector(
        run_root,
        analyzer.signals,
        hash_distance_min=int(payload.get("capture_hash_distance_min", 8)),
    )
    log_path = run_root / "events.jsonl"
    latest_path = run_root / "latest.png"
    storage_warning_sent = False

    def warn_storage_once(context: str, exc: Exception) -> None:
        nonlocal storage_warning_sent
        if storage_warning_sent:
            return
        storage_warning_sent = True
        emit(f"中斷戰鬥圖片儲存失敗但操作會繼續：{context}: {type(exc).__name__}: {exc}")

    try:
        run_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        warn_storage_once("建立資料夾", exc)

    capture_owner: Any | None = None
    if capture_image is None:
        capture_config = payload.get("capture") or {}
        provider_name = str(capture_config.get("provider") or "print_window").casefold()
        if provider_name == "wgc_router":
            from .wgc_capture import WgcFrameProvider

            router_path = Path(str(capture_config.get("router_path") or ""))
            if not router_path.is_absolute():
                router_path = (config_path.parent / router_path).resolve()
            capture_owner = WgcFrameProvider(
                executable=router_path,
                hwnd=int(hwnd),
                slot=slot,
                width=int(analyzer.expected_size[0]),
                height=int(analyzer.expected_size[1]),
                fps=int(capture_config.get("fps", 8)),
                startup_timeout_sec=float(capture_config.get("startup_timeout_sec", 5.0)),
                frame_timeout_sec=float(capture_config.get("frame_timeout_sec", 2.0)),
            )
            capture_owner.start()
            capture_image = capture_owner.capture
        elif provider_name == "print_window":
            from .windows_device import WindowsWindowController

            controller = WindowsWindowController(hwnd=int(hwnd), serial=f"slot-{slot}")

            def capture_image() -> Image.Image:
                controller.screenshot(latest_path)
                with Image.open(latest_path) as loaded:
                    return loaded.convert("RGB").copy()
        else:
            raise ValueError(f"unsupported battle capture provider: {provider_name}")

    if mode == "active" and action_executor is None:
        if pico_config_path is None:
            raise RuntimeError("active battle interrupt requires pico_config_path")
        from .pico_touch import _scheduler_for_config

        scheduler = _scheduler_for_config(pico_config_path)

        def action_executor(action: DecisionKind, point: tuple[int, int]) -> float:
            del action
            return float(scheduler.click(
                slot,
                int(hwnd),
                int(point[0]),
                int(point[1]),
                duration_sec=float(payload.get("click_hold_sec", 0.06)),
                stop_requested=stop_requested,
            ))

    actions: list[dict[str, Any]] = []
    started_at = clock()
    previous_state: tuple[str, str] | None = None
    last_saved: dict[str, str] = {}

    def append_log(row: dict[str, Any]) -> None:
        row = {"at": datetime.now().astimezone().isoformat(), **row}
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError as exc:
            warn_storage_once("寫入事件紀錄", exc)

    def save_candidate(
        image: Image.Image,
        *,
        label: str,
        force: bool = False,
    ) -> dict[str, str]:
        try:
            return collector.save_candidate(image, label=label, force=force)
        except OSError as exc:
            warn_storage_once("寫入截圖", exc)
            return {}

    def finish(result: dict[str, Any]) -> dict[str, Any]:
        if capture_owner is not None:
            capture_owner.close()
        return result

    emit(f"中斷戰鬥 {mode}: SLOT {slot} 視覺偵測已啟動")
    if capture_cleanup:
        append_log({"event": "capture_cleanup", **capture_cleanup})
    while True:
        if _stop_requested(stop_requested):
            decision = machine.cancel()
            append_log({"event": "cancelled", "machine": machine.snapshot()})
            return finish(_result(
                mode=mode,
                outcome="cancelled",
                machine=machine,
                actions=actions,
                log_path=log_path,
                capture_paths=last_saved,
                cancelled=True,
                step_acknowledged=False,
                elapsed_sec=clock() - started_at,
                decision=decision.kind.value,
            ))

        try:
            image = capture_image()
        except BaseException:
            if capture_owner is not None:
                capture_owner.close()
            raise
        vision = analyzer.analyze(image)
        now = clock()
        decision = machine.step(vision.evidence, now)
        state_key = (decision.phase.value, decision.screen.value)
        state_changed = state_key != previous_state
        if state_changed:
            last_saved = save_candidate(
                image,
                label=f"{decision.phase.value}_{decision.screen.value}",
                force=True,
            )
            emit(
                f"中斷戰鬥: {decision.screen.value} / {decision.phase.value} / "
                f"{decision.reason}"
            )
        elif decision.screen.value in {"unknown", "transition", "battle_locked"}:
            saved = save_candidate(
                image,
                label=f"{decision.phase.value}_{decision.screen.value}",
            )
            if saved:
                last_saved = saved
        previous_state = state_key
        append_log(
            {
                "event": "observation",
                "decision": decision.kind.value,
                "reason": decision.reason,
                "machine": machine.snapshot(),
                "black_ratio": vision.black_ratio,
                "motion_score": vision.motion_score,
                "scores": vision.evidence.scores,
                "matches": {
                    name: getattr(vision.evidence, name)
                    for name in analyzer.signals
                },
                "captures": last_saved if state_changed else {},
            }
        )

        if decision.kind in {DecisionKind.CLICK_MORE, DecisionKind.CLICK_CANCEL}:
            signal_name = (
                "more_visible" if decision.kind is DecisionKind.CLICK_MORE else "cancel_visible"
            )
            point = analyzer.click_point(signal_name)
            action_capture = save_candidate(
                image,
                label=f"action_{decision.kind.value}",
                force=True,
            )
            if mode == "dry_run":
                append_log(
                    {
                        "event": "dry_run_action_ready",
                        "action": decision.kind.value,
                        "point": list(point),
                        "captures": action_capture,
                    }
                )
                emit(
                    f"Dry-run 完成：條件成立，若為正式模式將點擊 "
                    f"{decision.kind.value} @ {point}"
                )
                return finish(_result(
                    mode=mode,
                    outcome="dry_run_action_ready",
                    machine=machine,
                    actions=actions,
                    log_path=log_path,
                    capture_paths=action_capture,
                    cancelled=False,
                    step_acknowledged=True,
                    elapsed_sec=now - started_at,
                    would_click={"action": decision.kind.value, "point": list(point)},
                ))

            action_started = clock()
            succeeded = False
            error = ""
            coordinator_turn_held = False
            executor_started_at = action_started
            transport_delay_sec = 0.0
            touch_down_at: float | None = None
            try:
                if playback_coordinator is not None:
                    delay = playback_coordinator.wait_for_turn(
                        int(playback_handle),
                        action_started,
                        stop_requested=stop_requested,
                    )
                    if delay is None:
                        machine.cancel()
                        return finish(_result(
                            mode=mode,
                            outcome="cancelled",
                            machine=machine,
                            actions=actions,
                            log_path=log_path,
                            capture_paths=action_capture,
                            cancelled=True,
                            step_acknowledged=False,
                            elapsed_sec=clock() - started_at,
                        ))
                    coordinator_turn_held = True
                if action_executor is None:
                    raise RuntimeError("active battle interrupt has no Pico action executor")
                executor_started_at = clock()
                transport_delay_sec = float(action_executor(decision.kind, point) or 0.0)
                touch_down_at = executor_started_at + max(0.0, transport_delay_sec)
                succeeded = True
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            finally:
                if playback_coordinator is not None and coordinator_turn_held:
                    playback_coordinator.release_turn(int(playback_handle))
            action_row = {
                "action": decision.kind.value,
                "point": list(point),
                "acknowledged": succeeded,
                "error": error,
                "elapsed_ms": round((clock() - action_started) * 1000.0, 1),
                "transport_delay_ms": round(max(0.0, transport_delay_sec) * 1000.0, 1),
                "touch_down_elapsed_ms": (
                    round((touch_down_at - started_at) * 1000.0, 1)
                    if touch_down_at is not None
                    else None
                ),
                "captures": action_capture,
            }
            actions.append(action_row)
            append_log({"event": "action_result", **action_row})
            if _stop_requested(stop_requested):
                machine.cancel()
                return finish(_result(
                    mode=mode,
                    outcome="cancelled",
                    machine=machine,
                    actions=actions,
                    log_path=log_path,
                    capture_paths=action_capture,
                    cancelled=True,
                    step_acknowledged=False,
                    elapsed_sec=clock() - started_at,
                ))
            machine.acknowledge_action(
                decision.kind,
                succeeded,
                touch_down_at if touch_down_at is not None else clock(),
            )
            continue

        if decision.kind is DecisionKind.COMPLETE:
            append_log({"event": "complete", "reason": decision.reason})
            emit(f"中斷戰鬥完成：{decision.reason}")
            return finish(_result(
                mode=mode,
                outcome="complete",
                machine=machine,
                actions=actions,
                log_path=log_path,
                capture_paths=last_saved,
                cancelled=False,
                step_acknowledged=True,
                elapsed_sec=now - started_at,
            ))
        if decision.kind is DecisionKind.FAIL:
            append_log({"event": "failed_safe", "reason": decision.reason})
            emit(f"中斷戰鬥安全停止：{decision.reason}")
            return finish(_result(
                mode=mode,
                outcome="failed_safe",
                machine=machine,
                actions=actions,
                log_path=log_path,
                capture_paths=last_saved,
                cancelled=False,
                step_acknowledged=False,
                elapsed_sec=now - started_at,
                error=decision.reason,
            ))

        if not _sleep_interruptibly(poll_seconds, stop_requested, clock):
            machine.cancel()
            return finish(_result(
                mode=mode,
                outcome="cancelled",
                machine=machine,
                actions=actions,
                log_path=log_path,
                capture_paths=last_saved,
                cancelled=True,
                step_acknowledged=False,
                elapsed_sec=clock() - started_at,
            ))


def _result(
    *,
    mode: str,
    outcome: str,
    machine: BattleInterruptMachine,
    actions: list[dict[str, Any]],
    log_path: Path,
    capture_paths: dict[str, str],
    cancelled: bool,
    step_acknowledged: bool,
    elapsed_sec: float,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "backend": "battle_vision_dry_run" if mode == "dry_run" else "pico_hid_touch",
        "mode": mode,
        "dry_run": mode == "dry_run",
        "outcome": outcome,
        "event_count": len(actions),
        "actions": actions,
        "cancelled": bool(cancelled),
        "step_acknowledged": bool(step_acknowledged),
        "machine": machine.snapshot(),
        "elapsed_sec": round(max(0.0, float(elapsed_sec)), 3),
        "log_path": str(log_path),
        "capture_paths": capture_paths,
        **extra,
    }


def _stop_requested(callback: Callable[[], bool] | None) -> bool:
    return bool(callback and callback())


def _sleep_interruptibly(
    seconds: float,
    stop_requested: Callable[[], bool] | None,
    clock: Callable[[], float],
) -> bool:
    deadline = clock() + max(0.0, float(seconds))
    while True:
        if _stop_requested(stop_requested):
            return False
        remaining = deadline - clock()
        if remaining <= 0:
            return True
        time.sleep(min(0.05, remaining))
