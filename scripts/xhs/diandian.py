"""点点（小红书站内 AI）问答与引用追踪。

复用 BridgePage 驱动用户已登录的 Chrome，只读：不点赞、不收藏、不评论、不发布。

能力：
- ask_diandian: 打开 /ai_chat，提问，拿到聚合回答与引用篇数
- dig_diandian: 在 ask 基础上，从回答里抽出被引用的笔记标题，逐篇搜索定位并打开原文
"""

from __future__ import annotations

import json
import logging
import re
import time

from .cdp import Page
from .urls import make_search_url

logger = logging.getLogger(__name__)

AI_CHAT_URL = "https://www.xiaohongshu.com/ai_chat"

_STATE_JS = r"""(()=>{
  const ta=[...document.querySelectorAll('textarea')];
  const composer=ta.find(x=>(x.placeholder||'').includes('如何快速提升'))||ta[0]||null;
  // welcome state uses a 'sendable' button; an existing conversation uses a submit div
  const send=[...document.querySelectorAll('button')].find(b=>String(b.className).includes('sendable'))
    || document.querySelector('.bottom-box-right-submit-button') || null;
  const r=t=>{if(!t)return null;const b=t.getBoundingClientRect();return {x:b.x+b.width/2,y:b.y+b.height/2}};
  return {url:location.href,conversation:/conversationId=/.test(location.href),
    composer:r(composer),send:r(send),composer_value:composer?composer.value:null,
    body_len:(document.body?.innerText||'').length,
    captcha:/website-login\/captcha|请求太频繁|安全验证/.test(location.href+(document.body?.innerText||'').slice(0,200))};
})()"""

_ANSWER_JS = r"""(()=>{
  // 优先取带「参考N篇笔记」的那条，避免抓到过渡消息
  const msgs=[...document.querySelectorAll('.ai-message')];
  const msg=msgs.filter(m=>/参考\s*\d+\s*篇笔记/.test(m.innerText||'')).pop()
    || msgs[msgs.length-1] || null;
  const md=msg?msg.querySelector('.xhs-ai-md-container'):null;
  const text=(md?md.innerText:(msg?msg.innerText:'')).trim();
  const m=(msg?msg.innerText:'').match(/参考\s*(\d+)\s*篇笔记/);
  // scope to the latest answer only, otherwise earlier turns leak in
  const root=msg||document;
  const sel=['.xhs-spark-multi-note','.xhs-spark-note-open',
             '[class*=spark] [aria-label]','[data-answer-block-type=spark] [aria-label]'];
  const seen=new Set(),cards=[];
  for(const s of sel){for(const e of root.querySelectorAll(s)){
    const t=e.getAttribute('aria-label')||'';
    if(t&&t.length>3&&!seen.has(t)){seen.add(t);cards.push(t)}
  }}
  return {url:location.href,text,ref_count:m?Number(m[1]):null,cards};
})()"""

_SCROLL_CHAT_JS = r"""(()=>{
  let n=document.querySelector('.ai-message')||document.querySelector('.xhs-ai-md-container');
  let e=n;
  while(e){
    const st=getComputedStyle(e);
    if(/(auto|scroll)/.test(st.overflowY)&&e.scrollHeight>e.clientHeight+50){
      e.scrollTop=e.scrollHeight;return 'inner';
    }
    e=e.parentElement;
  }
  window.scrollTo(0,document.body.scrollHeight);return 'window';
})()"""

_DETAIL_JS = r"""(()=>{
  const q=s=>document.querySelector(s)?.innerText?.trim()||'';
  const d=document.querySelector('#detail-desc');
  return {url:location.href,has_detail:!!d,
    login_redirect:/\/login(\?|$)/.test(location.href),
    risk:/300013|安全验证|请求太频繁|访问频繁/.test(document.body?.innerText||''),
    title:q('#detail-title')||document.title,
    author:q('.author-container .username')||q('.author-wrapper .username'),
    desc:(d?.innerText||'').trim(),
    tags:[...document.querySelectorAll('#detail-desc a.tag')].map(x=>x.innerText.trim()),
    interact:{like:q('.like-wrapper .count'),collect:q('.collect-wrapper .count'),
              comment:q('.chat-wrapper .count'),share:q('.share-wrapper .count')}};
})()"""


class DiandianError(RuntimeError):
    """点点交互失败。"""


def _norm(text: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", (text or "").lower())


def titles_match(a: str, b: str) -> bool:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    if na in nb or nb in na:
        return True
    sa, sb = set(na), set(nb)
    return len(sa & sb) / max(1, min(len(sa), len(sb))) >= 0.7


def titles_from_answer(answer: str) -> list[str]:
    """从回答正文抽引用标题。

    实测两种排版：
      title / "+N"                行内引用
      title / author / count      回答末尾的卡片列表
    """
    lines = [l.strip() for l in (answer or "").splitlines()]
    out: list[str] = []

    def add(candidate: str) -> None:
        t = (candidate or "").strip()
        if len(t) < 4 or len(t) > 60:
            return
        if re.match(r"^[#\-*\d\s]", t) or re.fullmatch(r"\+\d+", t):
            return
        # 句末标点说明是正文，不是标题
        if re.search(r"[。，、；]$", t):
            return
        # 引号说明这句话在转述，不是笔记标题
        if re.search(r'["\u201c\u201d]', t):
            return
        if t not in out:
            out.append(t)

    for i, line in enumerate(lines[:-1]):
        nxt = lines[i + 1]
        if re.fullmatch(r"\+\d+", nxt):
            add(line)
            continue
        if i + 2 < len(lines):
            nxt2 = lines[i + 2]
            author_like = 2 <= len(nxt) <= 20 and not re.fullmatch(r"\d+", nxt)
            if re.fullmatch(r"\d{1,6}", nxt2) and author_like:
                add(line)
    return out


def _locate_composer(page: Page) -> dict:
    for _ in range(20):
        state = page.evaluate(_STATE_JS) or {}
        if state.get("captcha"):
            raise DiandianError("点点页面触发安全验证，请人工完成后重试")
        if state.get("composer"):
            return state
        time.sleep(0.8)
    raise DiandianError("点点输入框没有出现（页面结构可能已改版）")


def ask_diandian(page: Page, question: str, wait: float = 90.0) -> dict:
    """打开点点提问，返回 {question, url, answer, ref_count, cards}。"""
    page.navigate(AI_CHAT_URL)
    try:
        page.wait_for_load(timeout=45)
    except Exception:
        pass

    state = _locate_composer(page)
    before = state.get("body_len", 0)

    page.mouse_click(state["composer"]["x"], state["composer"]["y"])
    time.sleep(0.6)
    page.type_text(question, delay_ms=60)
    time.sleep(0.8)

    state = page.evaluate(_STATE_JS) or {}
    if not (state.get("composer_value") or "").strip():
        # React 兼容注入兜底
        page.evaluate(
            "(()=>{const t=[...document.querySelectorAll('textarea')].find(x=>(x.placeholder||'').includes('如何快速提升'))||document.querySelector('textarea');"
            "if(!t)return false;const s=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set;"
            f"s.call(t,{json.dumps(question)});t.dispatchEvent(new Event('input',{{bubbles:true}}));return true}})()"
        )
        time.sleep(0.6)
        state = page.evaluate(_STATE_JS) or {}

    if state.get("send"):
        page.mouse_click(state["send"]["x"], state["send"]["y"])
    else:
        page.press_key("Enter")

    deadline = time.monotonic() + wait
    answer: dict = {}
    last_len = 0
    stable = 0
    while time.monotonic() < deadline:
        time.sleep(2.0)
        state = page.evaluate(_STATE_JS) or {}
        if state.get("captcha"):
            raise DiandianError("点点返回安全验证，请稍后再试")
        cur = state.get("body_len", 0)
        if state.get("conversation") and cur > before + 50:
            answer = page.evaluate(_ANSWER_JS) or {}
            if (answer.get("text") or "").strip():
                if cur == last_len:
                    stable += 1
                else:
                    stable = 0
                last_len = cur
                if stable >= 2:
                    for _ in range(2):
                        page.evaluate(_SCROLL_CHAT_JS)
                        time.sleep(1.2)
                    answer = page.evaluate(_ANSWER_JS) or answer
                    break
    if not answer:
        raise DiandianError("点点没有返回回答（可能仍在生成或输入未提交）")

    return {
        "question": question,
        "url": answer.get("url"),
        "answer": (answer.get("text") or "").strip(),
        "ref_count": answer.get("ref_count"),
        "cards": answer.get("cards", []),
    }


def open_note(page: Page, url: str, wait: float = 20.0) -> dict:
    """打开一篇笔记并读取正文、标签、互动。"""
    page.navigate(url)
    deadline = time.monotonic() + wait
    detail: dict = {}
    while time.monotonic() < deadline:
        detail = page.evaluate(_DETAIL_JS) or {}
        if detail.get("has_detail") or detail.get("login_redirect") or detail.get("risk"):
            break
        time.sleep(1.0)
    if detail.get("login_redirect"):
        raise DiandianError("登录态失效，请先登录小红书")
    if detail.get("risk"):
        raise DiandianError("触发安全验证，请稍后再试")
    if not detail.get("has_detail"):
        raise DiandianError(f"未打开笔记详情：{url[:80]}")
    m = re.search(r"/(?:explore|search_result)/([0-9a-f]{24})", detail.get("url") or "")
    detail["note_id"] = m.group(1) if m else None
    return detail


def search_note_by_title(page: Page, title: str, limit: int = 6) -> dict:
    """只按标题搜索定位，不打开详情。

    返回标题/作者/热度/链接，用于先给用户看候选清单。
    """
    page.navigate(make_search_url(title))
    try:
        page.wait_for_load(timeout=40)
    except Exception:
        pass
    time.sleep(1.5)

    items: list[dict] = []
    for _ in range(2):
        raw = page.evaluate(
            r"""(()=>{const out=[],seen=new Set();
              for(const a of document.querySelectorAll('a[href*="/search_result/"]')){
                const h=a.getAttribute('href')||'';
                const m=h.match(/\/search_result\/([0-9a-f]{24})/);
                if(!m||seen.has(m[1]))continue;
                let c=a;
                for(let i=0;i<6&&c;i++,c=c.parentElement){
                  const r=c.getBoundingClientRect();
                  if(r.width>100&&r.height>80&&(c.innerText||'').trim())break;
                }
                if(!c)continue;
                const lines=(c.innerText||'').split('\n').map(s=>s.trim()).filter(Boolean);
                seen.add(m[1]);
                out.push({note_id:m[1],url:new URL(h,location.origin).href,
                          title:lines[0]||'',author:lines[1]||''});
              }return out})()"""
        ) or []
        items = raw
        if any(titles_match(title, it.get("title", "")) for it in items):
            break
        page.dispatch_wheel_event(900)
        time.sleep(1.4)

    hit = next((it for it in items if titles_match(title, it.get("title", ""))), None)
    if not hit:
        raise DiandianError(f"搜索结果里没有匹配的笔记：{title[:24]}")
    m = re.search(r"/(?:explore|search_result)/([0-9a-f]{24})", hit.get("url", ""))
    return {
        "cited_title": title,
        "title": hit.get("title", ""),
        "author": hit.get("author", ""),
        "note_id": m.group(1) if m else None,
        "url": hit["url"],
        "title_matched": titles_match(title, hit.get("title", "")),
    }


def resolve_by_title(page: Page, title: str, limit: int = 6, wait: float = 20.0) -> dict:
    """按标题搜索定位并打开详情（仅在用户确认要看时调用）。"""
    hit = search_note_by_title(page, title, limit=limit)
    detail = open_note(page, hit["url"], wait=wait)
    return {
        "card_title": title,
        "search_title": hit.get("title", ""),
        "title_matched": titles_match(title, detail.get("title", "")),
        **detail,
    }


_EXPAND_JS = r"""(()=>{
  const msgs=[...document.querySelectorAll('.ai-message')];
  const last=msgs[msgs.length-1];
  if(!last)return {clicked:0,collected:0};
  // 展开组内「+N」更多卡片
  let clicked=0;
  for(const b of last.querySelectorAll('.xhs-spark-multi-note__more')){
    if(b.dataset.xhsExpanded==='1')continue;
    const r=b.getBoundingClientRect();
    if(r.width<=0||r.height<=0)continue;
    b.dataset.xhsExpanded='1';
    b.click();clicked++;
  }
  const titles=[...new Set([...last.querySelectorAll('.xhs-spark-note-open,.xhs-spark-multi-note')]
    .map(e=>e.getAttribute('aria-label')||'').filter(Boolean))];
  return {clicked,collected:titles.length,titles};
})()"""


def expand_references(page: Page, rounds: int = 6, wait: float = 1.6) -> dict:
    """尽力展开最后一条回答里的引用卡片。

    完整来源未必都会渲染，因此返回值带 complete 标记，由调用方如实说明。
    """
    seen: list[str] = []
    clicked_total = 0
    for _ in range(rounds):
        page.evaluate(
            "(()=>{const m=[...document.querySelectorAll('.ai-message')];"
            "if(m.length)m[m.length-1].scrollIntoView({block:'end'});return true})()"
        )
        time.sleep(0.8)
        step = page.evaluate(_EXPAND_JS) or {}
        clicked_total += int(step.get("clicked") or 0)
        titles = step.get("titles") or []
        before = len(seen)
        for t in titles:
            if t not in seen:
                seen.append(t)
        grew = len(seen) > before
        if not grew and not step.get("clicked"):
            break
        time.sleep(wait)
    return {"titles": seen, "clicked": clicked_total}


_DRAWER_JS = r"""(()=>{
  const drawer=document.querySelector('.xhs-drawer-reference');
  if(!drawer)return {open:false};
  const cards=[...drawer.querySelectorAll('.xhs-sq-source-card')].map(c=>{
    const t=s=>c.querySelector(s)?.innerText?.trim()||'';
    const lines=(c.innerText||'').split('\n').map(s=>s.trim()).filter(Boolean);
    return {author:t('.xhs-sq-source-card__name')||lines[0]||'',
            title:t('.xhs-sq-source-card__title'),
            excerpt:t('.xhs-sq-source-card__content')||t('.xhs-rich-excerpt'),
            footer:t('.xhs-sq-source-card__footer'),
            likes:t('.xhs-sq-source-card__likes')};
  });
  const tabs=[...drawer.querySelectorAll('[class*=tab]')].map(e=>e.innerText.trim()).filter(Boolean).slice(0,4);
  return {open:true,count:cards.length,tabs,cards};
})()"""


def open_source_drawer(page: Page, wait: float = 8.0) -> bool:
    """点开最后一条回答的「参考N篇内容」，展开右侧参考来源抽屉。"""
    box = page.evaluate("""(()=>{
      const msgs=[...document.querySelectorAll('.ai-message')];
      for(let i=msgs.length-1;i>=0;i--){
        const b=msgs[i].querySelector('.xhs-spark-source-header');
        if(b){b.scrollIntoView({block:'center'});const r=b.getBoundingClientRect();
          return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)}}
      }
      return null})()""")
    if not box:
        return False
    page.mouse_click(box["x"], box["y"])
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if page.evaluate("!!document.querySelector('.xhs-drawer-reference')"):
            time.sleep(1.0)
            return True
        time.sleep(0.6)
    return False


def read_source_drawer(page: Page) -> dict:
    """读取参考来源抽屉里的全部条目（不打开笔记）。"""
    return page.evaluate(_DRAWER_JS) or {"open": False}


def list_references(page: Page, question: str, top: int = 5, gap: float = 4.0,
                    wait: float = 90.0, expand_all: bool = False) -> dict:
    """问点点后只列引用清单（标题/作者/链接），不打开任何笔记。

    用于先给用户看「这几篇要不要看」，由用户决定是否继续 dig。
    """
    res = ask_diandian(page, question, wait=wait)
    titles = titles_from_answer(res["answer"])
    if res.get("cards"):
        titles = list(dict.fromkeys(list(res["cards"]) + titles))

    drawer = {"open": False, "cards": []}
    if expand_all:
        if open_source_drawer(page):
            drawer = read_source_drawer(page)
            drawer_titles = [c.get("title", "") for c in (drawer.get("cards") or []) if c.get("title")]
            titles = list(dict.fromkeys(drawer_titles + titles))

    refs: list[dict] = []
    for title in titles[:top]:
        try:
            refs.append(search_note_by_title(page, title))
        except DiandianError as exc:
            refs.append({"cited_title": title, "error": str(exc)})
        time.sleep(gap)

    ref_count = res.get("ref_count")
    complete = bool(ref_count) and len(titles) >= ref_count
    return {
        "question": question,
        "answer": res["answer"],
        "ref_count": ref_count,
        "cited_titles": titles,
        "references": refs,
        "resolved": len([r for r in refs if r.get("url")]),
        "drawer_open": drawer.get("open", False),
        "drawer_tabs": drawer.get("tabs", []),
        "drawer_sources": drawer.get("cards", []),
        "complete": complete,
        "opened_any_note": False,
    }


def dig_diandian(page: Page, question: str, top: int = 3, gap: float = 6.0,
                 wait: float = 90.0) -> dict:
    """问点点，并把回答里引用的前 top 篇笔记逐篇打开。"""
    res = ask_diandian(page, question, wait=wait)
    titles = titles_from_answer(res["answer"])
    if res.get("cards"):
        # 卡片标题优先，再补正文里解析到的
        titles = list(dict.fromkeys(list(res["cards"]) + titles))

    notes: list[dict] = []
    for title in titles[:top]:
        try:
            notes.append(resolve_by_title(page, title))
        except DiandianError as exc:
            notes.append({"card_title": title, "error": str(exc)})
        time.sleep(gap)

    return {
        "question": question,
        "answer": res["answer"],
        "ref_count": res.get("ref_count"),
        "cited_titles": titles,
        "notes": notes,
    }
