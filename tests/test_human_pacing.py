"""Pacing behaviour must be random, bounded, and persisted — without any network."""
import json
import random
from pathlib import Path

import pytest

from xhs.human_pacing import HumanPace, PaceConfig, PaceStop, single_keyword


def make(tmp_path: Path, seed: int = 7, **cfg) -> HumanPace:
    served = []
    cfg.setdefault("session_items", (3, 3))
    cfg.setdefault("daily_items", (5, 5))
    pace = HumanPace(
        PaceConfig(**cfg),
        state_path=tmp_path / "pace.json",
        rng=random.Random(seed),
        sleeper=lambda s: served.append(s),
    )
    pace.served = served
    return pace


def test_gap_within_configured_bounds(tmp_path):
    pace = make(tmp_path)
    plan = pace.before_item()
    assert 45.0 <= plan.wait_seconds <= 120.0


def test_gaps_are_not_constant(tmp_path):
    pace = make(tmp_path, session_items=(10, 10))
    # Neutralise breaks/browse so only the base gap is sampled.
    pace._next_break_at = 10**6
    pace._next_browse_at = 10**6
    gaps = [pace.before_item().wait_seconds for _ in range(10)]
    assert len(set(round(g, 3) for g in gaps)) > 1


def test_session_limit_stops(tmp_path):
    pace = make(tmp_path, session_items=(2, 2))
    pace.before_item()
    pace.before_item()
    with pytest.raises(PaceStop) as err:
        pace.before_item()
    assert "session_limit_reached" in err.value.reason


def test_daily_budget_persists_across_runs(tmp_path):
    pace = make(tmp_path, session_items=(10, 10), daily_items=(2, 2))
    pace.before_item()
    pace.after_item()
    pace.before_item()
    pace.after_item()
    # A new run on the same day must respect the earlier spend.
    second = make(tmp_path, session_items=(10, 10), daily_items=(2, 2))
    with pytest.raises(PaceStop) as err:
        second.before_item()
    assert "daily_limit_reached" in err.value.reason


def test_state_file_shape(tmp_path):
    pace = make(tmp_path, session_items=(10, 10))
    pace.before_item()
    pace.after_item()
    raw = json.loads((tmp_path / "pace.json").read_text(encoding="utf-8"))
    assert raw["done_today"] == 1
    assert raw["daily_target"] == 5
    assert raw["day"]


def test_micro_break_and_browse_are_scheduled(tmp_path):
    pace = make(
        tmp_path,
        session_items=(20, 20),
        micro_break_every=(2, 2),
        browse_every=(2, 2),
    )
    plans = [pace.before_item() for _ in range(2)]
    assert plans[1].micro_break
    assert 180.0 <= plans[1].wait_seconds <= 480.0
    assert plans[1].browse


def test_single_keyword_prefers_tag_then_title():
    assert single_keyword("钓到了什么！？", ["小野菊的海岛牧场"]) == "小野菊的海岛牧场 钓到了什么！？"
    assert single_keyword("标题", []) == "标题"
    assert single_keyword("", [], "作者") == "作者"
