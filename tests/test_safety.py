import time
from pathlib import Path
from xhs.safety import CollectionSafety, classify_access_error, validate_detail
from xhs.profile_manager import ProfileManager


def test_classifies_access_errors():
    assert classify_access_error('error_code=300013') == 'risk_300013'
    assert classify_access_error('页面加载超时') == 'navigation_timeout'
    assert classify_access_error('/404?source=x') == 'not_found_or_blocked'
    assert classify_access_error('扫码查看') == 'verification_required'


def test_quality_validation():
    assert validate_detail({'detail': {'note': {'noteId': 'a', 'title': 'x'}}}, 'a')[0]
    assert validate_detail({'detail': {'note': {'noteId': 'b', 'title': 'x'}}}, 'a')[1] == 'note_id_mismatch'
    assert validate_detail({'detail': {'note': {}}}, 'a')[1] == 'empty_detail'


def test_safety_persists_cooldown(tmp_path: Path):
    state = tmp_path / 'safety.json'
    safety = CollectionSafety(state, min_gap=0, hourly_limit=60)
    # keep the cooldown long enough that the assertion below cannot race the clock
    safety.record_error('300013', cooldown_base=30.0)
    assert safety.state.strikes == 1
    loaded = CollectionSafety(state, min_gap=0, hourly_limit=60)
    assert loaded.state.last_error == '300013'
    assert loaded.state.blocked_until >= time.time()


def test_profile_specs_are_isolated(tmp_path: Path):
    manager = ProfileManager(tmp_path, base_port=9300)
    a = manager.spec('account one', 0)
    b = manager.spec('account two', 1)
    assert a.profile_dir != b.profile_dir
    assert a.debug_port == 9300 and b.debug_port == 9301
