"""Offline coverage for favorites export helpers."""
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from xhs.favorites import _favorites_url, _merge_items


def test_favorites_url_preserves_token():
    url = _favorites_url('/user/profile/abc?xsec_token=abc%3D')
    assert urlparse(url).netloc == 'www.xiaohongshu.com'
    assert parse_qs(urlparse(url).query) == {'tab': ['fav'], 'subTab': ['note'], 'xsec_token': ['abc=']}


def test_merge_deduplicates():
    result = {}
    assert _merge_items(result, [{'note_id': 'a'}, {'note_id': 'a'}, {}]) == 1
    assert _merge_items(result, [{'note_id': 'a'}, {'note_id': 'b'}]) == 1
    assert list(result) == ['a', 'b']


def test_limited_export_is_not_complete():
    from xhs.favorites import export_favorites

    class Page:
        def wait_for_load(self, **kwargs):
            pass

        def wait_dom_stable(self, **kwargs):
            pass

        def evaluate(self, expression):
            if expression == 'location.href':
                return 'https://www.xiaohongshu.com/user/profile/abc?tab=fav&subTab=note'
            if expression == 'location.pathname':
                return '/user/profile/abc'
            if 'profile/' in expression and 'querySelector' in expression:
                return '/user/profile/abc'
            if 'page_state:' in expression:
                return {'items': [{'note_id': 'a'}, {'note_id': 'b'}],
                        'page_state': {'1': {'hasMore': False}}}
            return None

    result = export_favorites(Page(), max_items=1)
    assert result['total_items'] == 1
    assert result['pagination_exhausted'] is False
    assert result['stop_reason'] == 'max_items'


def test_invalid_limits_fail_before_browser_access():
    import pytest
    from xhs.favorites import export_favorites
    for options in [{'max_items': -1}, {'max_rounds': 0}, {'scroll_delay': 0.1}]:
        with pytest.raises(ValueError):
            export_favorites(None, **options)

