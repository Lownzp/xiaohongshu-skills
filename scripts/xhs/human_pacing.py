"""Human-like pacing for read-only collection.

Real users do not read notes at a constant rate: gaps vary, they pause to do
something else, they stop after a while, and they browse other pages between
reads. This module produces that shape with randomized, persisted budgets.

It only controls timing. It never bypasses a captcha or a rate limit.
"""
from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path


class PaceStop(Exception):
    """Raised when a session or daily budget is exhausted."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass
class PaceConfig:
    min_gap: float = 45.0
    max_gap: float = 120.0
    micro_break_every: tuple[int, int] = (3, 7)
    micro_break_seconds: tuple[float, float] = (180.0, 480.0)
    session_items: tuple[int, int] = (25, 40)
    daily_items: tuple[int, int] = (200, 300)
    browse_every: tuple[int, int] = (8, 15)
    browse_seconds: tuple[float, float] = (20.0, 60.0)


@dataclass
class PacePlan:
    wait_seconds: float
    browse: bool = False
    micro_break: bool = False
    reason: str = ""


@dataclass
class _State:
    day: str = ""
    done_today: int = 0
    daily_target: int = 0


class HumanPace:
    """Decides how long to wait before the next read.

    All randomness comes from the injected ``rng`` so behaviour is testable.
    """

    def __init__(
        self,
        config: PaceConfig | None = None,
        state_path: str | Path | None = None,
        rng: random.Random | None = None,
        sleeper=time.sleep,
        logger=None,
    ) -> None:
        self.config = config or PaceConfig()
        self.state_path = Path(state_path) if state_path else None
        self.rng = rng or random.Random()
        self._sleep = sleeper
        self._log = logger or (lambda *_a, **_k: None)
        self._state = self._load_state()
        self.session_target = self.rng.randint(*self.config.session_items)
        self.session_done = 0
        # Countdowns (not modulo) so two breaks can never land back to back.
        self._breaks_left = self.rng.randint(*self.config.micro_break_every)
        self._browse_left = self.rng.randint(*self.config.browse_every)

    # ── budgets ─────────────────────────────────────────────────
    def _today(self) -> str:
        return date.today().isoformat()

    def _load_state(self) -> _State:
        st = _State(day=self._today())
        if self.state_path and self.state_path.exists():
            try:
                raw = json.loads(self.state_path.read_text(encoding="utf-8"))
                st = _State(
                    day=raw.get("day", st.day),
                    done_today=int(raw.get("done_today", 0)),
                    daily_target=int(raw.get("daily_target", 0)),
                )
            except (OSError, ValueError, TypeError):
                pass
        if st.day != self._today():
            st = _State(day=self._today())
        if not st.daily_target:
            st.daily_target = self.rng.randint(*self.config.daily_items)
        return st

    def save_state(self) -> None:
        if not self.state_path:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "day": self._state.day,
            "done_today": self._state.done_today,
            "daily_target": self._state.daily_target,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.state_path)

    def remaining_today(self) -> int:
        return max(0, self._state.daily_target - self._state.done_today)

    # ── pacing ───────────────────────────────────────────────────
    def before_item(self) -> PacePlan:
        """Return the wait to apply before the next read.

        Raises PaceStop when this session or today's budget is used up.
        """
        if self.remaining_today() <= 0:
            raise PaceStop(f"daily_limit_reached:{self._state.daily_target}")
        if self.session_done >= self.session_target:
            raise PaceStop(f"session_limit_reached:{self.session_target}")

        wait = self.rng.uniform(self.config.min_gap, self.config.max_gap)
        plan = PacePlan(wait_seconds=wait, reason="base_gap")

        self.session_done += 1
        self._breaks_left -= 1
        self._browse_left -= 1
        if self._breaks_left <= 0:
            plan.wait_seconds = self.rng.uniform(*self.config.micro_break_seconds)
            plan.micro_break = True
            plan.reason = "micro_break"
            self._breaks_left = self.rng.randint(*self.config.micro_break_every)

        if self._browse_left <= 0:
            plan.browse = True
            plan.browse_seconds = self.rng.uniform(*self.config.browse_seconds)
            self._browse_left = self.rng.randint(*self.config.browse_every)
        return plan

    def apply(self, plan: PacePlan) -> None:
        self._log(f"pace wait {plan.wait_seconds:.0f}s ({plan.reason})")
        self._sleep(plan.wait_seconds)

    def after_item(self, success: bool = True) -> None:
        self._state.done_today += 1
        self.save_state()


def single_keyword(title: str, tags: list[str] | None = None, author: str = "") -> str:
    """Pick exactly one fallback search phrase for notes Bridge could not read.

    Kept to one attempt on purpose: every search request is the most heavily
    rate-limited surface, so retrying several phrases multiplies risk.
    """
    tags = tags or []
    if tags and title:
        return f"{tags[0]} {title[:24]}"
    if title:
        return title[:40]
    return author or ""
