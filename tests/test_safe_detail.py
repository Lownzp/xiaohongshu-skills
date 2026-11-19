"""safe_detail 的离线测试。

FakePage 按当前实现的调用顺序作答：
1) is_rate_limited -> 读取 {url, t}
2) 卡片检索        -> 含 querySelectorAll 的脚本
3) 详情轮询        -> 含 location.href 的状态脚本
"""
import pytest

from xhs.safe_detail import SafeDetailError, is_rate_limited, open_from_search_card

NOTE = "6afc0667000000002b01bcd6"


class FakePage:
    def __init__(self, found: bool, body: str = "正文", url: str | None = None):
        self.found = found
        self.body = body
        self.url = url or f"https://www.xiaohongshu.com/explore/{NOTE}"
        self.wheels = 0

    def trusted_wheel(self, x, y, dy):
        self.wheels += 1

    def evaluate(self, expression: str):
        if "slice(0,200)" in expression:  # is_rate_limited probe
            return {"url": "https://www.xiaohongshu.com/search_result", "t": ""}
        if "detail-desc" in expression:  # detail poll (it also uses querySelectorAll)
            return {"url": self.url, "title": "t", "body": self.body, "tags": []}
        if "search_result" in expression:  # card lookup
            return {"found": self.found, "href": "x"}
        return {"url": self.url, "title": "t", "body": self.body, "tags": []}


def test_rate_limit_detection_is_false_on_normal_page():
    assert is_rate_limited(FakePage(True)) is False


def test_missing_card_raises():
    with pytest.raises(SafeDetailError, match="card_not_in_dom"):
        open_from_search_card(FakePage(False), NOTE, "标题", max_scrolls=1)


def test_card_found_returns_detail():
    got = open_from_search_card(FakePage(True), NOTE, "标题")
    assert got["body"] == "正文"


def test_mismatched_note_id_is_rejected():
    wrong = FakePage(True, url="https://www.xiaohongshu.com/explore/5d230d8d00000000270007b5")
    with pytest.raises(SafeDetailError, match="note_id_mismatch"):
        open_from_search_card(wrong, NOTE, "标题")
