"""点点引用解析与标题匹配的离线测试（不依赖浏览器）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from xhs.diandian import titles_from_answer, titles_match  # noqa: E402

INLINE = """LoopForge 讨论挺多。

编程Agent总漏测？给它套个SOP
+3

💡 讨论方向

功能介绍类：大部分笔记在讲它怎么编排流水线。
"""

CARD_LIST = """整体热度很高。

编程Agent总漏测？给它套个SOP
康小为（AI副业搞钱实践者）
2
LoopForge：写码和审查分开
GGBot
3
GitHub热榜：LoopForge
AI实战所
44
🛠腾讯把写代码做成流水线
Alinix
0
+1
"""


def test_inline_citation():
    assert titles_from_answer(INLINE) == ["编程Agent总漏测？给它套个SOP"]


def test_card_list_layout():
    got = titles_from_answer(CARD_LIST)
    assert "编程Agent总漏测？给它套个SOP" in got
    assert "LoopForge：写码和审查分开" in got
    assert "GitHub热榜：LoopForge" in got
    assert "GGBot" not in got
    assert "44" not in got


def test_prose_is_not_a_title():
    prose = "这是一段普通正文。\n第二段继续说别的事情。\n+2\n"
    assert titles_from_answer(prose) == []


def test_quoted_prose_is_not_a_title():
    answer = '有人提到这个话题。\n有个评论说得很到位："不然省下的效率都可能变成风险\n+2\n'
    assert titles_from_answer(answer) == []


def test_title_matching():
    assert titles_match("编程Agent总漏测？给它套个SOP", "编程Agent总漏测？给它套个SOP")
    assert titles_match("GitHub热榜：LoopForge", "GitHub 热榜：LoopForge")
    assert not titles_match("编程Agent总漏测", "优衣库折扣季")
