from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Callable, Sequence

from .slot_limits import MAX_SLOT, MIN_SLOT


@dataclass
class _PlaybackState:
    handle: int
    slot: int
    queue_index: int
    group_index: int
    added_at: float
    status: str = "queued"
    timeline_started_at: float | None = None
    deadline: float | None = None
    ticket: int = 0


class CooperativePlaybackCoordinator:
    """Coordinate all GUI_TEST_PC playback on one global Pico input channel."""

    def __init__(
        self,
        *,
        group_size: int = 5,
        long_wait_seconds: float = 10.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if int(group_size) < 1:
            raise ValueError("group_size must be at least 1")
        if float(long_wait_seconds) < 0:
            raise ValueError("long_wait_seconds must not be negative")
        self.group_size = int(group_size)
        self.long_wait_seconds = float(long_wait_seconds)
        self._clock = clock
        self._condition = threading.Condition()
        self._states: dict[int, _PlaybackState] = {}
        self._order: list[int] = []
        self._activated_groups: dict[int, float] = {}
        self._next_handle = 1
        self._next_ticket = 1
        self._turn_owner: int | None = None
        self._preferred_handle: int | None = None
        self._grant_slots: list[int] = []

    def enqueue(self, slots: Sequence[int]) -> dict[int, int]:
        ordered_slots = _ordered_unique_slots(slots)
        if not ordered_slots:
            return {}
        with self._condition:
            if not self._has_unfinished_locked():
                self._reset_locked()
            active_slots = {
                state.slot
                for state in self._states.values()
                if state.status != "finished"
            }
            duplicates = [slot for slot in ordered_slots if slot in active_slots]
            if duplicates:
                raise ValueError(f"slots already queued for playback: {duplicates}")
            handles: dict[int, int] = {}
            for slot in ordered_slots:
                handle = self._next_handle
                self._next_handle += 1
                queue_index = len(self._order)
                state = _PlaybackState(
                    handle=handle,
                    slot=slot,
                    queue_index=queue_index,
                    group_index=queue_index // self.group_size,
                    added_at=self._clock(),
                )
                self._states[handle] = state
                self._order.append(handle)
                handles[slot] = handle
            self._condition.notify_all()
            return handles

    def wait_for_timeline_start(
        self,
        handle: int,
        *,
        stop_requested: Callable[[], bool] | None = None,
    ) -> float | None:
        with self._condition:
            state = self._require_state_locked(handle)
            while state.timeline_started_at is None:
                if _stop_requested(stop_requested):
                    self._finish_locked(state)
                    return None
                now = self._clock()
                self._activate_available_groups_locked(now)
                group_started_at = self._activated_groups.get(state.group_index)
                if group_started_at is not None:
                    state.timeline_started_at = max(group_started_at, state.added_at)
                    state.status = "preparing"
                    self._condition.notify_all()
                    return state.timeline_started_at
                self._condition.wait(timeout=0.05)
            return state.timeline_started_at

    def wait_for_turn(
        self,
        handle: int,
        deadline: float,
        *,
        stop_requested: Callable[[], bool] | None = None,
    ) -> float | None:
        with self._condition:
            state = self._require_state_locked(handle)
            if state.status == "finished":
                return None
            state.status = "waiting"
            state.deadline = float(deadline)
            state.ticket = self._next_ticket
            self._next_ticket += 1
            if (
                self._preferred_handle == handle
                and state.deadline - self._clock() > self.long_wait_seconds
            ):
                self._preferred_handle = None
            self._condition.notify_all()

            while True:
                if _stop_requested(stop_requested):
                    self._finish_locked(state)
                    return None
                now = self._clock()
                self._activate_available_groups_locked(now)
                winner, wake_at = self._winner_locked(now)
                if winner == handle and self._turn_owner is None:
                    self._turn_owner = handle
                    self._preferred_handle = handle
                    self._grant_slots.append(state.slot)
                    state.status = "executing"
                    delay = max(0.0, now - float(state.deadline or now))
                    state.deadline = None
                    self._condition.notify_all()
                    return delay
                timeout = 0.05
                if wake_at is not None:
                    timeout = min(timeout, max(0.001, wake_at - now))
                self._condition.wait(timeout=timeout)

    def release_turn(
        self,
        handle: int,
        *,
        next_deadline: float | None = None,
    ) -> None:
        with self._condition:
            state = self._require_state_locked(handle)
            if self._turn_owner == handle:
                self._turn_owner = None
            if state.status != "finished":
                now = self._clock()
                state.deadline = (
                    None if next_deadline is None else float(next_deadline)
                )
                self._preferred_handle = handle
                if (
                    state.deadline is not None
                    and state.deadline - now > self.long_wait_seconds
                ):
                    self._preferred_handle = None
                    state.status = "waiting"
                else:
                    state.status = "between"
            self._activate_available_groups_locked(self._clock())
            self._condition.notify_all()

    def wait_delay(
        self,
        handle: int,
        delay_seconds: float,
        *,
        stop_requested: Callable[[], bool] | None = None,
    ) -> bool:
        deadline = self._clock() + max(0.0, float(delay_seconds))
        delay = self.wait_for_turn(
            handle,
            deadline,
            stop_requested=stop_requested,
        )
        if delay is None:
            return False
        self.release_turn(handle)
        return True

    def finish(self, handle: int) -> None:
        with self._condition:
            state = self._require_state_locked(handle)
            self._finish_locked(state)

    def metadata(self, handle: int) -> dict[str, int | float | str | None]:
        with self._condition:
            state = self._require_state_locked(handle)
            return {
                "handle": state.handle,
                "slot": state.slot,
                "queue_index": state.queue_index,
                "group_index": state.group_index,
                "group_number": state.group_index + 1,
                "status": state.status,
                "timeline_started_at": state.timeline_started_at,
                "deadline": state.deadline,
            }

    def snapshot(self) -> dict[str, object]:
        with self._condition:
            return {
                "group_size": self.group_size,
                "long_wait_seconds": self.long_wait_seconds,
                "turn_owner": self._turn_owner,
                "preferred_handle": self._preferred_handle,
                "activated_groups": sorted(self._activated_groups),
                "grant_runs": self._grant_runs_locked(),
                "states": [
                    self.metadata(handle)
                    for handle in self._order
                    if handle in self._states
                ],
            }

    def _winner_locked(self, now: float) -> tuple[int | None, float | None]:
        if self._turn_owner is not None:
            return self._turn_owner, None
        if self._preferred_handle is not None:
            preferred = self._states.get(self._preferred_handle)
            if preferred is None or preferred.status == "finished":
                self._preferred_handle = None
            elif (
                preferred.deadline is not None
                and preferred.deadline - now > self.long_wait_seconds
            ):
                self._preferred_handle = None
            elif preferred.status in {"queued", "preparing", "between"}:
                return None, preferred.deadline
            elif preferred.status == "waiting" and preferred.deadline is not None:
                if preferred.deadline <= now:
                    return preferred.handle, now
                return None, preferred.deadline
        wake_at: float | None = None
        for group_index in sorted(self._activated_groups):
            states = self._unfinished_group_states_locked(group_index)
            if not states:
                continue
            if any(state.status in {"queued", "preparing", "between"} for state in states):
                return None, None
            ready = [
                state
                for state in states
                if state.status == "waiting"
                and state.deadline is not None
                and state.deadline <= now
            ]
            if ready:
                winner = min(ready, key=lambda state: (state.slot, state.ticket))
                return winner.handle, now
            group_wake_at = min(
                (
                    float(state.deadline)
                    for state in states
                    if state.status == "waiting" and state.deadline is not None
                ),
                default=None,
            )
            if group_wake_at is not None:
                wake_at = (
                    group_wake_at
                    if wake_at is None
                    else min(wake_at, group_wake_at)
                )
        return None, wake_at

    def _activate_available_groups_locked(self, now: float) -> None:
        group_indices = sorted({state.group_index for state in self._states.values()})
        for group_index in group_indices:
            if group_index in self._activated_groups:
                continue
            earlier_groups = [index for index in group_indices if index < group_index]
            if all(self._group_has_no_choice_locked(index, now) for index in earlier_groups):
                self._activated_groups[group_index] = now
                self._condition.notify_all()
                continue
            break

    def _group_has_no_choice_locked(self, group_index: int, now: float) -> bool:
        states = self._unfinished_group_states_locked(group_index)
        if not states:
            return True
        if group_index not in self._activated_groups:
            return False
        for state in states:
            if state.status in {"queued", "preparing", "between", "executing"}:
                return False
            if (
                state.status == "waiting"
                and state.deadline is not None
                and state.deadline - now <= self.long_wait_seconds
            ):
                return False
        return True

    def _unfinished_group_states_locked(self, group_index: int) -> list[_PlaybackState]:
        return [
            self._states[handle]
            for handle in self._order
            if handle in self._states
            and self._states[handle].group_index == group_index
            and self._states[handle].status != "finished"
        ]

    def _finish_locked(self, state: _PlaybackState) -> None:
        if self._turn_owner == state.handle:
            self._turn_owner = None
        if self._preferred_handle == state.handle:
            self._preferred_handle = None
        state.status = "finished"
        state.deadline = None
        self._activate_available_groups_locked(self._clock())
        self._condition.notify_all()

    def _has_unfinished_locked(self) -> bool:
        return any(state.status != "finished" for state in self._states.values())

    def _reset_locked(self) -> None:
        self._states.clear()
        self._order.clear()
        self._activated_groups.clear()
        self._turn_owner = None
        self._preferred_handle = None
        self._grant_slots.clear()

    def _grant_runs_locked(self) -> list[dict[str, int]]:
        runs: list[dict[str, int]] = []
        for slot in self._grant_slots:
            if runs and runs[-1]["slot"] == slot:
                runs[-1]["actions"] += 1
            else:
                runs.append({"slot": slot, "actions": 1})
        return runs

    def _require_state_locked(self, handle: int) -> _PlaybackState:
        state = self._states.get(int(handle))
        if state is None:
            raise KeyError(f"unknown cooperative playback handle: {handle}")
        return state


def _ordered_unique_slots(slots: Sequence[int]) -> list[int]:
    ordered: list[int] = []
    seen: set[int] = set()
    for raw_slot in slots:
        slot = int(raw_slot)
        if slot < MIN_SLOT or slot > MAX_SLOT or slot in seen:
            continue
        seen.add(slot)
        ordered.append(slot)
    return ordered


def _stop_requested(callback: Callable[[], bool] | None) -> bool:
    return bool(callback and callback())
