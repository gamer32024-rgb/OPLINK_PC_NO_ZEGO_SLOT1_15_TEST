from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ScreenState(str, Enum):
    UNKNOWN = "unknown"
    WORLD_IDLE = "world_idle"
    WORLD_AUTO = "world_auto"
    TRANSITION = "transition"
    BATTLE_LOCKED = "battle_locked"
    BATTLE_READY = "battle_ready"
    RESULT_IDLE = "result_idle"
    RESULT_AUTO = "result_auto"


class MachinePhase(str, Enum):
    OBSERVING = "observing"
    WAIT_BATTLE = "wait_battle"
    WAIT_CONTROL = "wait_control"
    WAIT_CANCEL = "wait_cancel"
    VERIFY_CANCEL = "verify_cancel"
    FALLBACK_WAIT_CURRENT_END = "fallback_wait_current_end"
    FALLBACK_WAIT_NEXT = "fallback_wait_next"
    COMPLETE = "complete"
    FAILED_SAFE = "failed_safe"


class DecisionKind(str, Enum):
    WAIT = "wait"
    CLICK_MORE = "click_more"
    CLICK_CANCEL = "click_cancel"
    COMPLETE = "complete"
    FAIL = "fail"


@dataclass(frozen=True)
class BattleEvidence:
    captured: bool = True
    correct_size: bool = True
    transition: bool = False
    world_anchor: bool | None = None
    result_anchor: bool | None = None
    battle_anchor: bool | None = None
    timer_visible: bool | None = None
    more_visible: bool | None = None
    auto_visible: bool | None = None
    cancel_visible: bool | None = None
    ambush_visible: bool | None = None
    success_visible: bool | None = None
    scores: dict[str, float] = field(default_factory=dict)

    def classify(self) -> ScreenState:
        if not self.captured or not self.correct_size:
            return ScreenState.UNKNOWN
        if self.transition:
            return ScreenState.TRANSITION
        if self.battle_anchor is True or self.success_visible is True or self.ambush_visible is True:
            ready = (
                self.ambush_visible is not True
                and (self.cancel_visible is True or self.more_visible is True)
            )
            return ScreenState.BATTLE_READY if ready else ScreenState.BATTLE_LOCKED
        if self.result_anchor is True:
            return ScreenState.RESULT_AUTO if self.timer_visible is True else ScreenState.RESULT_IDLE
        if self.world_anchor is True:
            return ScreenState.WORLD_AUTO if self.timer_visible is True else ScreenState.WORLD_IDLE
        return ScreenState.UNKNOWN


@dataclass(frozen=True)
class BattleInterruptPolicy:
    state_window: int = 5
    state_votes: int = 3
    action_consecutive_frames: int = 2
    success_consecutive_frames: int = 2
    control_timeout_sec: float = 35.0
    cancel_click_delay_sec: float = 1.5
    menu_timeout_sec: float = 3.0
    more_retry_interval_sec: float = 1.0
    max_more_attempts: int = 3
    next_battle_timeout_sec: float = 12.0
    timer_clear_confirm_sec: float = 12.0
    max_current_battle_sec: float = 120.0
    total_timeout_sec: float = 180.0
    max_battle_attempts: int = 3


@dataclass(frozen=True)
class BattleDecision:
    kind: DecisionKind
    phase: MachinePhase
    screen: ScreenState
    reason: str
    attempt: int
    fallback: bool


class BattleInterruptMachine:
    """Pure fail-safe state machine; action execution is supplied by the caller."""

    def __init__(self, policy: BattleInterruptPolicy | None = None) -> None:
        self.policy = policy or BattleInterruptPolicy()
        self.phase = MachinePhase.OBSERVING
        self.started_at: float | None = None
        self.battle_started_at: float | None = None
        self.wait_deadline: float | None = None
        self.timer_clear_since: float | None = None
        self.attempt = 0
        self.fallback = False
        self.pending_action: DecisionKind | None = None
        self.action_sequence_started = False
        self.cancel_attempted = False
        self.success_notice_seen = False
        self.more_attempts = 0
        self.next_action_not_before = 0.0
        self.last_failure = ""
        self.last_screen = ScreenState.UNKNOWN
        self._states: deque[ScreenState] = deque(maxlen=max(1, self.policy.state_window))
        self._more_ready_streak = 0
        self._cancel_ready_streak = 0
        self._success_streak = 0

    def step(self, evidence: BattleEvidence, now: float) -> BattleDecision:
        now = float(now)
        if self.started_at is None:
            self.started_at = now

        raw = evidence.classify()
        self._states.append(raw)
        stable = self._stable_screen()
        self.last_screen = stable
        self._update_streaks(evidence, raw)

        if self.phase is MachinePhase.COMPLETE:
            return self._decision(DecisionKind.COMPLETE, stable, "already complete")
        if self.phase is MachinePhase.FAILED_SAFE:
            return self._decision(DecisionKind.FAIL, stable, self.last_failure or "failed safe")
        if now - self.started_at >= self.policy.total_timeout_sec:
            return self._fail(stable, "total timeout")
        if self.pending_action is not None:
            return self._decision(DecisionKind.WAIT, stable, f"waiting for {self.pending_action.value} result")

        if self._success_streak >= self.policy.success_consecutive_frames:
            self.success_notice_seen = True
            if self.phase is MachinePhase.VERIFY_CANCEL:
                return self._decision(
                    DecisionKind.WAIT,
                    stable,
                    "cancel success notice seen; verifying timer disappearance",
                )

        if self.phase is MachinePhase.OBSERVING:
            return self._observe(stable, raw, now)
        if self.phase is MachinePhase.WAIT_BATTLE:
            return self._wait_battle(stable, now)
        if self.phase is MachinePhase.WAIT_CONTROL:
            return self._wait_control(stable, evidence, now)
        if self.phase is MachinePhase.WAIT_CANCEL:
            return self._wait_cancel(stable, evidence, now)
        if self.phase is MachinePhase.VERIFY_CANCEL:
            return self._verify_cancel(stable, evidence, now)
        if self.phase is MachinePhase.FALLBACK_WAIT_CURRENT_END:
            return self._fallback_wait_current_end(stable, now)
        if self.phase is MachinePhase.FALLBACK_WAIT_NEXT:
            return self._fallback_wait_next(stable, evidence, now)
        return self._fail(stable, f"unsupported phase {self.phase.value}")

    def acknowledge_action(self, action: DecisionKind, succeeded: bool, now: float) -> None:
        if action not in {DecisionKind.CLICK_MORE, DecisionKind.CLICK_CANCEL}:
            raise ValueError(f"not an action decision: {action}")
        if self.pending_action is not action:
            raise RuntimeError(f"unexpected action result: pending={self.pending_action}, got={action}")
        self.pending_action = None
        if not succeeded:
            self.phase = MachinePhase.FAILED_SAFE
            self.last_failure = (
                f"{action.value} transport failed; one-shot lock prevents retry"
            )
            return
        if action is DecisionKind.CLICK_MORE:
            self.more_attempts += 1
            self.phase = MachinePhase.WAIT_CANCEL
            self.next_action_not_before = float(now) + self.policy.cancel_click_delay_sec
            self.wait_deadline = float(now) + self.policy.menu_timeout_sec
            return
        self.phase = MachinePhase.VERIFY_CANCEL
        self.wait_deadline = None
        self.timer_clear_since = None

    def cancel(self, reason: str = "stop requested") -> BattleDecision:
        self.pending_action = None
        self.phase = MachinePhase.FAILED_SAFE
        self.last_failure = reason
        return self._decision(DecisionKind.FAIL, self.last_screen, reason)

    def snapshot(self) -> dict[str, Any]:
        return {
            "phase": self.phase.value,
            "screen": self.last_screen.value,
            "attempt": self.attempt,
            "fallback": self.fallback,
            "pending_action": self.pending_action.value if self.pending_action else None,
            "action_sequence_started": self.action_sequence_started,
            "cancel_attempted": self.cancel_attempted,
            "success_notice_seen": self.success_notice_seen,
            "more_attempts": self.more_attempts,
            "last_failure": self.last_failure,
        }

    def _observe(
        self,
        stable: ScreenState,
        raw: ScreenState,
        now: float,
    ) -> BattleDecision:
        if raw is ScreenState.TRANSITION:
            self.phase = MachinePhase.WAIT_BATTLE
            self.wait_deadline = now + self.policy.next_battle_timeout_sec
            self.timer_clear_since = None
            return self._decision(
                DecisionKind.WAIT,
                ScreenState.TRANSITION,
                "transition detected; primed for the next More button",
            )
        if stable in {ScreenState.BATTLE_LOCKED, ScreenState.BATTLE_READY}:
            self._begin_battle(now)
            return self._decision(DecisionKind.WAIT, stable, "current battle detected; primary attempt")
        if stable in {ScreenState.WORLD_AUTO, ScreenState.RESULT_AUTO}:
            self.phase = MachinePhase.WAIT_BATTLE
            self.wait_deadline = now + self.policy.next_battle_timeout_sec
            self.timer_clear_since = None
            return self._decision(DecisionKind.WAIT, stable, "auto encounter timer confirmed; waiting for battle")
        if stable in {ScreenState.WORLD_IDLE, ScreenState.RESULT_IDLE}:
            return self._confirm_timer_clear(stable, now, "no active auto encounter")
        return self._decision(DecisionKind.WAIT, stable, "waiting for a stable known screen")

    def _wait_battle(self, stable: ScreenState, now: float) -> BattleDecision:
        if stable in {ScreenState.BATTLE_LOCKED, ScreenState.BATTLE_READY}:
            self._begin_battle(now)
            return self._decision(DecisionKind.WAIT, stable, "battle entered; primary attempt")
        if stable in {ScreenState.WORLD_IDLE, ScreenState.RESULT_IDLE}:
            return self._confirm_timer_clear(stable, now, "timer disappeared before battle")
        self.timer_clear_since = None
        if self.wait_deadline is not None and now >= self.wait_deadline:
            return self._fail(stable, "auto encounter visible but no battle within next-battle timeout")
        return self._decision(DecisionKind.WAIT, stable, "waiting for battle entry")

    def _wait_control(
        self,
        stable: ScreenState,
        evidence: BattleEvidence,
        now: float,
    ) -> BattleDecision:
        if stable in {ScreenState.WORLD_IDLE, ScreenState.WORLD_AUTO, ScreenState.RESULT_IDLE, ScreenState.RESULT_AUTO}:
            return self._begin_next_battle_watch(
                stable,
                now,
                "battle ended before controls became actionable",
            )
        if self.battle_started_at is not None:
            elapsed = now - self.battle_started_at
            if elapsed >= self.policy.max_current_battle_sec:
                return self._fail(stable, "current battle exceeded maximum duration")
            if elapsed >= self.policy.control_timeout_sec:
                return self._attempt_failed(stable, now, "actionable controls not found in early battle window")
        if now < self.next_action_not_before:
            return self._decision(DecisionKind.WAIT, stable, "waiting before another safe More probe")
        if stable is ScreenState.BATTLE_READY and self._cancel_ready_streak >= self.policy.action_consecutive_frames:
            return self._propose(DecisionKind.CLICK_CANCEL, stable, "cancel button stable while battle confirmed")
        if (
            stable is ScreenState.BATTLE_READY
            and evidence.ambush_visible is not True
            and self._more_ready_streak >= self.policy.action_consecutive_frames
        ):
            return self._propose(
                DecisionKind.CLICK_MORE,
                stable,
                "battle and More stable; earliest actionable point",
            )
        return self._decision(DecisionKind.WAIT, stable, "battle confirmed; waiting for More")

    def _wait_cancel(
        self,
        stable: ScreenState,
        evidence: BattleEvidence,
        now: float,
    ) -> BattleDecision:
        if stable in {ScreenState.WORLD_IDLE, ScreenState.WORLD_AUTO, ScreenState.RESULT_IDLE, ScreenState.RESULT_AUTO}:
            return self._begin_next_battle_watch(
                stable,
                now,
                "battle ended after More before Cancel became actionable",
            )
        if now < self.next_action_not_before:
            return self._decision(
                DecisionKind.WAIT,
                stable,
                "holding the one-shot Cancel until 1.5 seconds after More",
            )
        if (
            stable is ScreenState.BATTLE_READY
            and evidence.ambush_visible is not True
            and self._cancel_ready_streak >= self.policy.action_consecutive_frames
        ):
            return self._propose(DecisionKind.CLICK_CANCEL, stable, "cancel button stable after More")
        if self.wait_deadline is not None and now >= self.wait_deadline:
            self.last_failure = "cancel menu did not appear in the current battle"
            self.fallback = True
            self.pending_action = None
            self.phase = MachinePhase.FALLBACK_WAIT_CURRENT_END
            return self._decision(
                DecisionKind.WAIT,
                stable,
                "cancel menu did not appear; waiting for this battle to end without another click",
            )
        return self._decision(DecisionKind.WAIT, stable, "waiting for cancel menu")

    def _verify_cancel(
        self,
        stable: ScreenState,
        evidence: BattleEvidence,
        now: float,
    ) -> BattleDecision:
        if stable in {ScreenState.WORLD_IDLE, ScreenState.WORLD_AUTO, ScreenState.RESULT_IDLE, ScreenState.RESULT_AUTO}:
            return self._begin_next_battle_watch(
                stable,
                now,
                "battle ended after Cancel",
            )
        if stable in {ScreenState.BATTLE_LOCKED, ScreenState.BATTLE_READY, ScreenState.TRANSITION, ScreenState.UNKNOWN}:
            self.timer_clear_since = None
            if self.battle_started_at is not None and self._battle_elapsed(now) >= self.policy.max_current_battle_sec:
                return self._fail(stable, "could not verify battle exit after cancel")
            return self._decision(DecisionKind.WAIT, stable, "cancel sent; waiting for success notice or stable timer absence")
        return self._decision(DecisionKind.WAIT, stable, "verifying cancel")

    def _fallback_wait_current_end(self, stable: ScreenState, now: float) -> BattleDecision:
        if stable in {ScreenState.WORLD_IDLE, ScreenState.WORLD_AUTO, ScreenState.RESULT_IDLE, ScreenState.RESULT_AUTO}:
            return self._begin_next_battle_watch(
                stable,
                now,
                self.last_failure or "current battle ended after a failed cancel attempt",
            )
        if self.battle_started_at is not None and self._battle_elapsed(now) >= self.policy.max_current_battle_sec:
            return self._fail(stable, "fallback timed out waiting for current battle end")
        return self._decision(DecisionKind.WAIT, stable, "primary failed; do not click again in this battle")

    def _fallback_wait_next(
        self,
        stable: ScreenState,
        evidence: BattleEvidence,
        now: float,
    ) -> BattleDecision:
        if stable in {ScreenState.BATTLE_LOCKED, ScreenState.BATTLE_READY}:
            self._begin_battle(now)
            if self.phase is MachinePhase.FAILED_SAFE:
                return self._decision(DecisionKind.FAIL, stable, self.last_failure)
            return self._decision(DecisionKind.WAIT, stable, "next battle detected; fallback attempt")
        if self.wait_deadline is not None and now >= self.wait_deadline:
            self.phase = MachinePhase.COMPLETE
            return self._decision(
                DecisionKind.COMPLETE,
                stable,
                "no new battle during the post-battle confirmation window",
            )
        return self._decision(
            DecisionKind.WAIT,
            stable,
            f"waiting {self.policy.next_battle_timeout_sec:g} seconds for another battle",
        )

    def _confirm_timer_clear(self, stable: ScreenState, now: float, reason: str) -> BattleDecision:
        if self.timer_clear_since is None:
            self.timer_clear_since = now
            return self._decision(DecisionKind.WAIT, stable, "timer absent; starting confirmation window")
        if now - self.timer_clear_since >= self.policy.timer_clear_confirm_sec:
            self.phase = MachinePhase.COMPLETE
            return self._decision(DecisionKind.COMPLETE, stable, reason)
        return self._decision(DecisionKind.WAIT, stable, "confirming stable timer absence")

    def _begin_next_battle_watch(
        self,
        stable: ScreenState,
        now: float,
        reason: str,
    ) -> BattleDecision:
        self.last_failure = reason
        self.fallback = True
        self.pending_action = None
        self.action_sequence_started = False
        self.cancel_attempted = False
        self.success_notice_seen = False
        self.more_attempts = 0
        self.timer_clear_since = None
        self.battle_started_at = None
        self.phase = MachinePhase.FALLBACK_WAIT_NEXT
        self.wait_deadline = now + self.policy.next_battle_timeout_sec
        return self._decision(
            DecisionKind.WAIT,
            stable,
            f"battle ended; waiting {self.policy.next_battle_timeout_sec:g} seconds to confirm no next battle",
        )

    def _begin_battle(self, now: float) -> None:
        self.attempt += 1
        if self.attempt > self.policy.max_battle_attempts:
            self.phase = MachinePhase.FAILED_SAFE
            self.last_failure = "maximum battle attempts exceeded"
            return
        self.phase = MachinePhase.WAIT_CONTROL
        self.battle_started_at = now
        self.wait_deadline = None
        self.timer_clear_since = None
        self.action_sequence_started = False
        self.cancel_attempted = False
        self.success_notice_seen = False
        self.more_attempts = 0
        self.next_action_not_before = now

    def _attempt_failed(self, stable: ScreenState, now: float, reason: str) -> BattleDecision:
        if self.action_sequence_started:
            return self._fail(stable, reason + "; one-shot lock prevents retry")
        self._mark_attempt_failed(reason, now)
        if self.phase is MachinePhase.FAILED_SAFE:
            return self._decision(DecisionKind.FAIL, stable, self.last_failure)
        return self._decision(DecisionKind.WAIT, stable, reason + "; fallback armed")

    def _mark_attempt_failed(self, reason: str, now: float) -> None:
        self.last_failure = reason
        if self.action_sequence_started:
            self.phase = MachinePhase.FAILED_SAFE
            self.pending_action = None
            return
        self.fallback = True
        self.pending_action = None
        if self.attempt >= self.policy.max_battle_attempts:
            self.phase = MachinePhase.FAILED_SAFE
            return
        if self.last_screen in {ScreenState.BATTLE_LOCKED, ScreenState.BATTLE_READY, ScreenState.TRANSITION, ScreenState.UNKNOWN}:
            self.phase = MachinePhase.FALLBACK_WAIT_CURRENT_END
        else:
            self.phase = MachinePhase.FALLBACK_WAIT_NEXT
            self.wait_deadline = now + self.policy.next_battle_timeout_sec

    def _propose(self, action: DecisionKind, stable: ScreenState, reason: str) -> BattleDecision:
        if action is DecisionKind.CLICK_MORE:
            if self.action_sequence_started:
                return self._fail(stable, "duplicate More blocked by one-shot lock")
            self.action_sequence_started = True
        elif action is DecisionKind.CLICK_CANCEL:
            if self.cancel_attempted:
                return self._fail(stable, "duplicate Cancel blocked by one-shot lock")
            self.action_sequence_started = True
            self.cancel_attempted = True
        self.pending_action = action
        return self._decision(action, stable, reason)

    def _stable_screen(self) -> ScreenState:
        if not self._states:
            return ScreenState.UNKNOWN
        latest = self._states[-1]
        counts = Counter(self._states)
        if latest is not ScreenState.UNKNOWN and counts[latest] >= self.policy.state_votes:
            return latest
        return ScreenState.UNKNOWN

    def _update_streaks(self, evidence: BattleEvidence, raw: ScreenState) -> None:
        more_ready = (
            raw is ScreenState.BATTLE_READY
            and evidence.ambush_visible is not True
            and evidence.battle_anchor is True
            and evidence.more_visible is True
        )
        cancel_ready = (
            raw is ScreenState.BATTLE_READY
            and evidence.ambush_visible is not True
            and evidence.battle_anchor is True
            and evidence.cancel_visible is True
        )
        self._more_ready_streak = self._more_ready_streak + 1 if more_ready else 0
        self._cancel_ready_streak = self._cancel_ready_streak + 1 if cancel_ready else 0
        self._success_streak = self._success_streak + 1 if evidence.success_visible is True else 0

    def _battle_elapsed(self, now: float) -> float:
        return max(0.0, now - (self.battle_started_at if self.battle_started_at is not None else now))

    def _fail(self, stable: ScreenState, reason: str) -> BattleDecision:
        self.pending_action = None
        self.phase = MachinePhase.FAILED_SAFE
        self.last_failure = reason
        return self._decision(DecisionKind.FAIL, stable, reason)

    def _decision(self, kind: DecisionKind, screen: ScreenState, reason: str) -> BattleDecision:
        return BattleDecision(
            kind=kind,
            phase=self.phase,
            screen=screen,
            reason=reason,
            attempt=self.attempt,
            fallback=self.fallback,
        )
