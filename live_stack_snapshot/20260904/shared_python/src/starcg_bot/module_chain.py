from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Callable, Sequence


@dataclass(frozen=True)
class ModuleStepAssignment:
    step_index: int
    module_name: str
    script_path: Path


def build_module_chain_plan(
    module_steps: Sequence[tuple[str, Sequence[Path]]],
    slots: Sequence[int],
) -> dict[int, list[ModuleStepAssignment]]:
    """Assign one script per module to each slot in stable round-robin order."""
    ordered_slots = sorted({int(slot) for slot in slots})
    if not ordered_slots:
        return {}
    plans = {slot: [] for slot in ordered_slots}
    for step_index, (module_name, scripts) in enumerate(module_steps):
        choices = [Path(script) for script in scripts]
        if not choices:
            raise ValueError(f"module {module_name!r} has no playable scripts")
        for rank, slot in enumerate(ordered_slots):
            plans[slot].append(
                ModuleStepAssignment(
                    step_index=step_index,
                    module_name=str(module_name),
                    script_path=choices[rank % len(choices)],
                )
            )
    return plans


def slot_start_delays(slots: Sequence[int], stagger_seconds: float) -> dict[int, float]:
    ordered_slots = sorted({int(slot) for slot in slots})
    stagger = max(0.0, float(stagger_seconds))
    return {slot: rank * stagger for rank, slot in enumerate(ordered_slots)}


class ModuleLeadLimiter:
    """Allow a slot to run ahead without exceeding a bounded module lead."""

    def __init__(self, slots: Sequence[int], *, max_lead: int = 1) -> None:
        ordered_slots = sorted({int(slot) for slot in slots})
        if not ordered_slots:
            raise ValueError("ModuleLeadLimiter requires at least one slot")
        if int(max_lead) < 0:
            raise ValueError("max_lead must be non-negative")
        self.max_lead = int(max_lead)
        self._completed = {slot: 0 for slot in ordered_slots}
        self._active = set(ordered_slots)
        self._condition = threading.Condition()

    def wait_until_next_allowed(
        self,
        slot: int,
        *,
        stop_requested: Callable[[], bool] | None = None,
    ) -> bool:
        slot = int(slot)
        with self._condition:
            while True:
                if stop_requested and stop_requested():
                    return False
                if slot not in self._active:
                    return False
                slowest = min(self._completed[item] for item in self._active)
                if self._completed[slot] <= slowest + self.max_lead:
                    return True
                self._condition.wait(timeout=0.05)

    def is_next_allowed(self, slot: int) -> bool:
        slot = int(slot)
        with self._condition:
            if slot not in self._active:
                return False
            slowest = min(self._completed[item] for item in self._active)
            return self._completed[slot] <= slowest + self.max_lead

    def complete_step(self, slot: int) -> int:
        slot = int(slot)
        with self._condition:
            if slot not in self._completed:
                raise KeyError(f"unknown slot {slot}")
            self._completed[slot] += 1
            completed = self._completed[slot]
            self._condition.notify_all()
            return completed

    def deactivate(self, slot: int) -> None:
        with self._condition:
            self._active.discard(int(slot))
            self._condition.notify_all()

    def snapshot(self) -> dict[int, int]:
        with self._condition:
            return dict(self._completed)
