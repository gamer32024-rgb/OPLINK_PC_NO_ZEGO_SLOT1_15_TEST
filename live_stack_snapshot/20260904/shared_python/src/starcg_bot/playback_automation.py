from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from .slot_limits import MAX_SLOT, MIN_SLOT


ACTIVE_STATUSES = {"waiting", "cooling", "running"}
FINAL_STATUSES = {"completed", "cancelled", "failed", "expired"}
SUPPORTED_MODES = {"loop", "scheduled_once"}
SUPPORTED_TARGET_KINDS = {"module_chain", "script"}


def _now_iso(timestamp: float | None = None) -> str:
    value = time.time() if timestamp is None else float(timestamp)
    return datetime.fromtimestamp(value).astimezone().isoformat(timespec="seconds")


def _normalize_slots(values: Any) -> list[int]:
    if isinstance(values, str):
        raw_values: list[Any] = values.replace("-", ",").split(",")
    elif isinstance(values, (list, tuple, set)):
        raw_values = list(values)
    else:
        raw_values = [values]
    slots: list[int] = []
    for value in raw_values:
        try:
            slot = int(value)
        except (TypeError, ValueError):
            continue
        if MIN_SLOT <= slot <= MAX_SLOT and slot not in slots:
            slots.append(slot)
    return slots


def _normalize_modules(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple)):
        values = [values]
    modules: list[str] = []
    for value in values:
        name = str(value or "").strip()
        if name:
            modules.append(name)
    return modules


class PlaybackAutomationStore:
    """Persistent state machine for GUI_TEST_PC loop and one-shot playback."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = RLock()
        self._jobs: list[dict[str, Any]] = []
        self._load()

    def list_jobs(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(job) for job in self._jobs]

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._find(job_id)
            return dict(job) if job is not None else None

    def active_jobs(self) -> list[dict[str, Any]]:
        return [job for job in self.list_jobs() if job.get("status") in ACTIVE_STATUSES]

    def create_job(
        self,
        *,
        mode: str,
        target_kind: str,
        slots: Any,
        modules: Any = None,
        script: str | Path | None = None,
        cooldown_seconds: float = 0.0,
        repeat_count: int | None = None,
        run_at: float | None = None,
        source: str = "desktop",
        now: float | None = None,
    ) -> dict[str, Any]:
        mode = str(mode or "").strip()
        target_kind = str(target_kind or "").strip()
        if mode not in SUPPORTED_MODES:
            raise ValueError(f"unsupported playback automation mode: {mode}")
        if target_kind not in SUPPORTED_TARGET_KINDS:
            raise ValueError(f"unsupported playback target: {target_kind}")
        normalized_slots = _normalize_slots(slots)
        if not normalized_slots:
            raise ValueError("at least one SLOT is required")
        normalized_modules = _normalize_modules(modules)
        normalized_script = str(script or "").strip()
        if target_kind == "module_chain" and not normalized_modules:
            raise ValueError("module_chain requires at least one module")
        if target_kind == "script" and not normalized_script:
            raise ValueError("script target requires a script")
        if mode == "loop" and target_kind != "module_chain":
            raise ValueError("loop playback supports a module or module chain")

        created_at = time.time() if now is None else float(now)
        if mode == "loop":
            due_at = created_at
        else:
            if run_at is None:
                raise ValueError("scheduled_once requires run_at")
            due_at = float(run_at)
            if due_at <= created_at:
                raise ValueError("scheduled_once run_at must be in the future")
        cooldown = max(0.0, float(cooldown_seconds or 0.0))
        normalized_repeat_count: int | None = None
        if mode == "loop" and repeat_count is not None and repeat_count != "":
            try:
                normalized_repeat_count = int(repeat_count)
            except (TypeError, ValueError) as exc:
                raise ValueError("repeat_count must be a positive integer or empty") from exc
            if normalized_repeat_count <= 0:
                raise ValueError("repeat_count must be a positive integer or empty")
        job = {
            "id": f"playback-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:8]}",
            "mode": mode,
            "target_kind": target_kind,
            "slots": normalized_slots,
            "modules": normalized_modules,
            "script": normalized_script,
            "cooldown_seconds": cooldown,
            "repeat_count": normalized_repeat_count,
            "run_at": due_at if mode == "scheduled_once" else None,
            "run_at_iso": _now_iso(due_at) if mode == "scheduled_once" else None,
            "next_run_at": due_at,
            "next_run_at_iso": _now_iso(due_at),
            "iteration": 0,
            "status": "waiting",
            "enabled": True,
            "source": str(source or "desktop"),
            "created_at": _now_iso(created_at),
            "started_at": None,
            "finished_at": None,
            "active_run_id": None,
            "last_error": None,
            "last_result": None,
        }
        with self._lock:
            self._jobs.append(job)
            self._save()
            return dict(job)

    def due_jobs(self, now: float | None = None) -> list[dict[str, Any]]:
        timestamp = time.time() if now is None else float(now)
        with self._lock:
            due = [
                dict(job)
                for job in self._jobs
                if job.get("enabled", True)
                and job.get("status") in {"waiting", "cooling"}
                and float(job.get("next_run_at") or 0.0) <= timestamp
            ]
        return sorted(
            due,
            key=lambda job: (
                0 if job.get("mode") == "scheduled_once" else 1,
                float(job.get("next_run_at") or 0.0),
                str(job.get("id")),
            ),
        )

    def expire_overdue_scheduled(
        self,
        *,
        grace_seconds: float = 120.0,
        now: float | None = None,
    ) -> list[dict[str, Any]]:
        timestamp = time.time() if now is None else float(now)
        cutoff = timestamp - max(0.0, float(grace_seconds))
        expired: list[dict[str, Any]] = []
        with self._lock:
            for job in self._jobs:
                if (
                    job.get("mode") != "scheduled_once"
                    or job.get("status") not in {"waiting", "cooling"}
                    or not job.get("enabled", True)
                ):
                    continue
                due_at = float(job.get("next_run_at") or job.get("run_at") or 0.0)
                if due_at <= 0.0 or due_at >= cutoff:
                    continue
                job["status"] = "expired"
                job["enabled"] = False
                job["active_run_id"] = None
                job["finished_at"] = _now_iso(timestamp)
                job["last_error"] = (
                    "scheduled playback missed its execution window while GUI_TEST_PC was unavailable"
                )
                expired.append(dict(job))
            if expired:
                self._save()
        return expired

    def mark_waiting(self, job_id: str, message: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            job = self._find(job_id)
            if job is None or job.get("status") in FINAL_STATUSES:
                return dict(job) if job is not None else None
            job["status"] = "waiting"
            job["active_run_id"] = None
            if message:
                job["last_error"] = str(message)
            self._save()
            return dict(job)

    def mark_running(self, job_id: str, run_id: str, now: float | None = None) -> dict[str, Any]:
        with self._lock:
            job = self._require(job_id)
            if job.get("status") in FINAL_STATUSES or not job.get("enabled", True):
                raise ValueError(f"playback automation is not active: {job_id}")
            job["status"] = "running"
            job["active_run_id"] = str(run_id)
            job["started_at"] = _now_iso(now)
            job["last_error"] = None
            self._save()
            return dict(job)

    def finish_run(
        self,
        job_id: str,
        *,
        run_id: str,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        timestamp = time.time() if now is None else float(now)
        with self._lock:
            job = self._find(job_id)
            if job is None:
                return None
            if job.get("status") == "cancelled":
                return dict(job)
            if str(job.get("active_run_id") or "") != str(run_id):
                return dict(job)
            job["active_run_id"] = None
            job["last_result"] = dict(result or {})
            if status == "completed":
                job["iteration"] = int(job.get("iteration") or 0) + 1
                job["last_error"] = None
                repeat_count = job.get("repeat_count")
                loop_finished = (
                    job.get("mode") == "loop"
                    and repeat_count is not None
                    and int(job["iteration"]) >= int(repeat_count)
                )
                if job.get("mode") == "loop" and job.get("enabled", True) and not loop_finished:
                    next_run = timestamp + float(job.get("cooldown_seconds") or 0.0)
                    job["status"] = "cooling"
                    job["next_run_at"] = next_run
                    job["next_run_at_iso"] = _now_iso(next_run)
                    job["finished_at"] = None
                else:
                    job["status"] = "completed"
                    job["enabled"] = False
                    job["finished_at"] = _now_iso(timestamp)
            else:
                job["status"] = "cancelled" if status == "cancelled" else "failed"
                job["enabled"] = False
                job["last_error"] = str(error or status)
                job["finished_at"] = _now_iso(timestamp)
            self._save()
            return dict(job)

    def cancel(self, job_id: str, now: float | None = None) -> dict[str, Any] | None:
        with self._lock:
            job = self._find(job_id)
            if job is None:
                return None
            if job.get("status") in FINAL_STATUSES:
                return dict(job)
            job["status"] = "cancelled"
            job["enabled"] = False
            job["active_run_id"] = None
            job["finished_at"] = _now_iso(now)
            job["last_error"] = "cancelled by user"
            self._save()
            return dict(job)

    def fail(self, job_id: str, error: str, now: float | None = None) -> dict[str, Any] | None:
        with self._lock:
            job = self._find(job_id)
            if job is None:
                return None
            if job.get("status") in FINAL_STATUSES:
                return dict(job)
            job["status"] = "failed"
            job["enabled"] = False
            job["active_run_id"] = None
            job["finished_at"] = _now_iso(now)
            job["last_error"] = str(error)
            self._save()
            return dict(job)

    def _load(self) -> None:
        if not self.path.exists():
            self._jobs = []
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, TypeError):
            self._jobs = []
            return
        jobs = payload.get("jobs", []) if isinstance(payload, dict) else payload
        self._jobs = [dict(job) for job in jobs if isinstance(job, dict)] if isinstance(jobs, list) else []
        changed = False
        for job in self._jobs:
            if "repeat_count" not in job:
                job["repeat_count"] = None
                changed = True
            if job.get("status") == "running":
                job["status"] = "failed"
                job["enabled"] = False
                job["active_run_id"] = None
                job["last_error"] = "GUI_TEST_PC closed while this playback was running; not replayed automatically"
                job["finished_at"] = _now_iso()
                changed = True
        if changed:
            self._save()

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"updated_at": _now_iso(), "jobs": self._jobs[-200:]}
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        temp_path = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        temp_path.write_text(text, encoding="utf-8")
        last_error: OSError | None = None
        for attempt in range(8):
            try:
                os.replace(temp_path, self.path)
                return
            except PermissionError as exc:
                last_error = exc
                time.sleep(0.02 * (attempt + 1))
        try:
            temp_path.unlink(missing_ok=True)
        finally:
            if last_error is not None:
                raise last_error

    def _find(self, job_id: str) -> dict[str, Any] | None:
        wanted = str(job_id)
        return next((job for job in self._jobs if str(job.get("id")) == wanted), None)

    def _require(self, job_id: str) -> dict[str, Any]:
        job = self._find(job_id)
        if job is None:
            raise KeyError(f"unknown playback automation: {job_id}")
        return job
