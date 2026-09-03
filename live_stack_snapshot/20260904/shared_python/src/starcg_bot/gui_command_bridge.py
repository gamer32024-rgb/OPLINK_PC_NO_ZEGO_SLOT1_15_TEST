from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import time
from typing import Any
from uuid import uuid4

from .slot_limits import MAX_SLOT, MIN_SLOT


FINAL_STATUSES = {"completed", "cancelled", "failed", "expired", "interrupted"}
WINDOWS_REPLACE_RETRY_DELAYS = (0.02, 0.04, 0.08, 0.16, 0.32, 0.5)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class GuiCommandBridge:
    """Atomic file bridge between the PWA relay and the desktop GUI process."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.commands_dir = self.root / "commands"
        self.heartbeat_path = self.root / "gui_heartbeat.json"
        self._lock = threading.RLock()
        self.commands_dir.mkdir(parents=True, exist_ok=True)

    def enqueue(
        self,
        action: str,
        payload: dict[str, Any] | None = None,
        *,
        label: str | None = None,
        expires_in_seconds: float | None = 30.0,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        command_id = "gui-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f") + "-" + uuid4().hex[:8]
        clean_payload = dict(payload or {})
        slots = self._normalize_slots(clean_payload.get("slots"))
        command = {
            "id": command_id,
            "action": str(action),
            "label": str(label or action),
            "payload": clean_payload,
            "slots": slots,
            "status": "queued",
            "slot_status": {str(slot): "等待 GUI_TEST_PC" for slot in slots},
            "created_at": now,
            "updated_at": now,
            "source": "gui_test_pc_pwa",
        }
        if expires_in_seconds is not None:
            command["expires_at"] = datetime.fromtimestamp(
                now_dt.timestamp() + max(1.0, float(expires_in_seconds)),
                timezone.utc,
            ).isoformat()
        clean_request_id = str(request_id or "").strip()
        if clean_request_id:
            command["request_id"] = clean_request_id
        with self._lock:
            self._write_json(self._command_path(command_id), command)
            self._trim_commands()
        return command

    def get(self, command_id: str) -> dict[str, Any] | None:
        return self._read_json(self._command_path(command_id))

    def list_commands(self, *, limit: int = 100) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for path in sorted(self.commands_dir.glob("gui-*.json"), reverse=True):
            data = self._read_json(path)
            if data:
                items.append(data)
            if len(items) >= max(1, int(limit)):
                break
        return items

    def find_by_request_id(self, request_id: str) -> dict[str, Any] | None:
        wanted = str(request_id or "").strip()
        if not wanted:
            return None
        return next(
            (item for item in self.list_commands(limit=200) if item.get("request_id") == wanted),
            None,
        )

    def queued_commands(self, *, limit: int = 20) -> list[dict[str, Any]]:
        queued = [item for item in self.list_commands(limit=200) if item.get("status") == "queued"]
        queued.sort(key=lambda item: str(item.get("created_at") or ""))
        return queued[: max(1, int(limit))]

    def supersede_queued_slots(
        self,
        slots: Any,
        *,
        actions: set[str] | None = None,
        reason: str = "Superseded by a newer playback request",
    ) -> list[str]:
        """Remove overlapping Slots from older queued playback commands."""

        wanted = set(self._normalize_slots(slots))
        if not wanted:
            return []
        superseded: list[str] = []
        with self._lock:
            for path in sorted(self.commands_dir.glob("gui-*.json")):
                command = self._read_json(path)
                if not command or command.get("status") != "queued":
                    continue
                if actions is not None and str(command.get("action") or "") not in actions:
                    continue
                payload = command.get("payload") if isinstance(command.get("payload"), dict) else {}
                current_slots = self._normalize_slots(payload.get("slots") or command.get("slots"))
                overlap = [slot for slot in current_slots if slot in wanted]
                if not overlap:
                    continue
                remaining = [slot for slot in current_slots if slot not in wanted]
                payload = dict(payload)
                payload["slots"] = remaining
                command["payload"] = payload
                command["slots"] = remaining
                statuses = command.setdefault("slot_status", {})
                for slot in overlap:
                    statuses[str(slot)] = "已被較新的播放設定取代"
                command["updated_at"] = utc_now_iso()
                if not remaining:
                    command["status"] = "cancelled"
                    command["finished_at"] = utc_now_iso()
                    command["error"] = str(reason)
                self._write_json(path, command)
                superseded.append(str(command.get("id") or path.stem))
        return superseded

    def expire_stale(self, *, now: datetime | None = None) -> list[str]:
        current = now or datetime.now(timezone.utc)
        expired: list[str] = []
        for command in self.list_commands(limit=200):
            if command.get("status") != "queued":
                continue
            expires_at = self._parse_timestamp(command.get("expires_at"))
            if expires_at is None or expires_at > current:
                continue
            command_id = str(command.get("id") or "")
            if command_id:
                self.update(
                    command_id,
                    status="expired",
                    error="GUI_TEST_PC did not accept this command before its deadline",
                )
                expired.append(command_id)
        return expired

    def interrupt_inflight(self, reason: str) -> list[str]:
        interrupted: list[str] = []
        for command in self.list_commands(limit=200):
            status = str(command.get("status") or "")
            if status not in {"queued", "running"}:
                continue
            command_id = str(command.get("id") or "")
            if not command_id:
                continue
            self.update(
                command_id,
                status="expired" if status == "queued" else "interrupted",
                error=str(reason),
            )
            interrupted.append(command_id)
        return interrupted

    def update(
        self,
        command_id: str,
        *,
        status: str | None = None,
        error: str | None = None,
        result: dict[str, Any] | None = None,
        slot: int | None = None,
        slot_status: str | None = None,
    ) -> dict[str, Any] | None:
        with self._lock:
            path = self._command_path(command_id)
            command = self._read_json(path)
            if command is None:
                return None
            if status is not None:
                command["status"] = str(status)
                if status == "running" and not command.get("started_at"):
                    command["started_at"] = utc_now_iso()
                if status in FINAL_STATUSES:
                    command["finished_at"] = utc_now_iso()
            if error is not None:
                command["error"] = str(error)
            if result is not None:
                command["result"] = dict(result)
            if slot is not None and slot_status is not None:
                statuses = command.setdefault("slot_status", {})
                statuses[str(int(slot))] = str(slot_status)
            command["updated_at"] = utc_now_iso()
            self._write_json(path, command)
            return command

    def write_heartbeat(self, state: dict[str, Any]) -> None:
        payload = dict(state)
        payload["updated_at"] = utc_now_iso()
        with self._lock:
            self._write_json(self.heartbeat_path, payload)

    def read_heartbeat(self) -> dict[str, Any] | None:
        return self._read_json(self.heartbeat_path)

    def _command_path(self, command_id: str) -> Path:
        safe_id = "".join(char for char in str(command_id) if char.isalnum() or char in "-_")
        if not safe_id:
            raise ValueError("command id is required")
        return self.commands_dir / f"{safe_id}.json"

    def _trim_commands(self, keep: int = 200) -> None:
        paths = sorted(self.commands_dir.glob("gui-*.json"), reverse=True)
        for path in paths[keep:]:
            try:
                path.unlink()
            except OSError:
                pass

    @staticmethod
    def _normalize_slots(raw: Any) -> list[int]:
        if raw is None:
            return []
        values: list[int] = []
        if isinstance(raw, str):
            for token in raw.replace(";", ",").split(","):
                token = token.strip()
                if not token:
                    continue
                if "-" in token:
                    start_text, end_text = token.split("-", 1)
                    start, end = int(start_text), int(end_text)
                    values.extend(range(min(start, end), max(start, end) + 1))
                else:
                    values.append(int(token))
        elif isinstance(raw, (list, tuple)):
            values.extend(int(value) for value in raw)
        elif isinstance(raw, set):
            values.extend(sorted(int(value) for value in raw))
        else:
            values.append(int(raw))

        ordered: list[int] = []
        seen: set[int] = set()
        for value in values:
            if MIN_SLOT <= value <= MAX_SLOT and value not in seen:
                seen.add(value)
                ordered.append(value)
        return ordered

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any] | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _parse_timestamp(value: Any) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(
            f".{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid4().hex}.tmp"
        )
        try:
            temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            for attempt in range(len(WINDOWS_REPLACE_RETRY_DELAYS) + 1):
                try:
                    os.replace(temp, path)
                    return
                except OSError as exc:
                    winerror = getattr(exc, "winerror", None)
                    retriable = isinstance(exc, PermissionError) or winerror in {5, 32, 33}
                    if not retriable or attempt >= len(WINDOWS_REPLACE_RETRY_DELAYS):
                        raise
                    time.sleep(WINDOWS_REPLACE_RETRY_DELAYS[attempt])
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
