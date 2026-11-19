"""读取当前登录用户的小红书收藏列表。"""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from .urls import EXPLORE_URL


_EXTRACT_PROFILE_HREF_JS = """
(() => document.querySelector('a[href*="/user/profile/"]')?.getAttribute('href') || '')()
"""

_EXTRACT_FAVORITES_JS = r"""
(() => {
  const unwrap = (value) => value && value._value !== undefined ? value._value : value;
  const state = window.__INITIAL_STATE__ || {};
  const notesState = unwrap(state.user?.notes);
  const groups = Array.isArray(notesState) ? notesState : Object.values(notesState || {});
  const rows = [];
  const asArray = (value) => Array.isArray(value) ? value : Object.values(value || {});
  for (const group of [groups[1] || []]) {
    for (const entry of asArray(group)) {
      const card = entry?.noteCard || entry;
      if (!card || !card.noteId) continue;
      const cover = card.cover || {};
      const info = card.interactInfo || {};
      const author = card.user || {};
      rows.push({
        note_id: card.noteId || entry.id,
        xsec_token: card.xsecToken || entry.xsecToken || '',
        title: card.displayTitle || card.title || '',
        note_type: card.type || '',
        author: author.nickname || '',
        author_id: author.userId || '',
        author_xsec_token: author.xsecToken || '',
        cover: cover.urlDefault || cover.urlPre || '',
        liked_count: info.likedCount || '',
        liked: !!info.liked,
        url: `${location.origin}/explore/${card.noteId}${card.xsecToken ? `?xsec_token=${encodeURIComponent(card.xsecToken)}&xsec_source=pc_user` : ''}`,
      });
    }
  }
  const unique = new Map();
  for (const row of rows) if (row.note_id && !unique.has(row.note_id)) unique.set(row.note_id, row);
  return {
    url: location.href,
    user_id: (location.pathname.match(/\/user\/profile\/([^/?]+)/) || [])[1] || '',
    items: Array.from(unique.values()),
    page_state: unwrap(state.user?.noteQueries),
    requests: window.__xhsFavoritesAudit?.requests || [],
  };
})()
"""


def _favorites_url(profile_href: str) -> str:
    parsed = urlparse(profile_href)
    path = parsed.path or profile_href.split("?", 1)[0]
    query = parse_qs(parsed.query)
    query.update({"tab": ["fav"], "subTab": ["note"]})
    return urlunparse(("https", "www.xiaohongshu.com", path, "", urlencode(query, doseq=True), ""))


def _merge_items(target: dict[str, dict[str, Any]], items: list[dict[str, Any]]) -> int:
    before = len(target)
    for item in items:
        note_id = item.get("note_id")
        if note_id:
            target.setdefault(note_id, item)
    return len(target) - before


def export_favorites(page, max_items: int = 0, scroll_delay: float = 1.2, max_rounds: int = 200, trusted_wheel: bool = False) -> dict[str, Any]:
    """导航到个人收藏并通过页面自身加载机制采集收藏卡片。

    max_items=0 表示尽可能采集全部当前可加载收藏。
    """
    if max_items < 0 or max_rounds < 1 or scroll_delay < 0.5:
        raise ValueError("max_items 必须 >= 0，max_rounds 必须 >= 1，scroll_delay 必须 >= 0.5")

    # 尽量复用当前已打开的小红书页，避免首页资源加载超时。
    current_url = page.evaluate("location.href") or ""
    profile_href = page.evaluate(_EXTRACT_PROFILE_HREF_JS) if "xiaohongshu.com" in current_url else ""
    if not profile_href:
        page.navigate(EXPLORE_URL)
        try:
            page.wait_for_load(timeout=30)
        except Exception:
            # 页面可能已经可交互，继续尝试读取导航栏。
            pass
        profile_href = page.evaluate(_EXTRACT_PROFILE_HREF_JS)
    if not profile_href:
        raise RuntimeError("未找到当前用户主页入口，请确认已登录")

    if current_url != _favorites_url(profile_href):
        page.navigate(_favorites_url(profile_href))
    try:
        page.wait_for_load(timeout=30)
    except Exception:
        pass
    page.wait_dom_stable(timeout=15)

    # Observe only collection-list responses; never save cookies or request headers.
    page.evaluate(r"""(() => {
      if (window.__xhsFavoritesAudit) return;
      const audit = window.__xhsFavoritesAudit = {requests: []};
      const record = (url, status, payload) => {
        if (!String(url).includes('/note/collect/page')) return;
        const data = payload?.data || {};
        audit.requests.push({status, code: payload?.code, success: payload?.success,
          count: (data.notes || data.items || []).length,
          cursor: data.cursor, has_more: data.has_more ?? data.hasMore});
      };
      const open = XMLHttpRequest.prototype.open;
      XMLHttpRequest.prototype.open = function(method, url, ...rest) {
        if (String(url).includes('/note/collect/page')) {
          this.addEventListener('load', () => {
            try { record(url, this.status, this.responseType === 'json' ? this.response : JSON.parse(this.responseText)); }
            catch (_) { record(url, this.status, null); }
          }, {once:true});
        }
        return open.call(this, method, url, ...rest);
      };
      const fetchOriginal = window.fetch;
      window.fetch = async function(...args) {
        const response = await fetchOriginal.apply(this, args);
        const url = args[0]?.url || String(args[0]);
        if (url.includes('/note/collect/page')) {
          response.clone().json().then(data => record(url, response.status, data)).catch(() => {});
        }
        return response;
      };
    })()""")
    collected: dict[str, dict[str, Any]] = {}
    unchanged_rounds = 0
    rounds = 0
    while rounds < max_rounds:
        rounds += 1
        snapshot = page.evaluate(_EXTRACT_FAVORITES_JS) or {}
        added = _merge_items(collected, snapshot.get("items") or [])
        if max_items and len(collected) >= max_items:
            break
        pagination = snapshot.get("page_state") or {}
        current_query = pagination[1] if isinstance(pagination, list) and len(pagination) > 1 else pagination.get("1", {}) if isinstance(pagination, dict) else {}
        if current_query.get("hasMore") is False:
            break

        scroll_state = page.evaluate(r"""(() => {
          const candidates = [document.querySelector('.user-page'), document.scrollingElement]
            .filter(Boolean);
          const target = candidates.find((el) => el.scrollHeight > el.clientHeight + 20)
            || document.scrollingElement;
          const before = {top: target.scrollTop, height: target.scrollHeight, client: target.clientHeight};
          // Use incremental movement: the profile page uses virtual scrolling and
          // may ignore a single jump to scrollHeight before its sentinel mounts.
          target.scrollTop = Math.min(target.scrollTop + Math.max(240, target.clientHeight * 0.85), target.scrollHeight);
          target.dispatchEvent(new Event('scroll', {bubbles: true}));
          target.dispatchEvent(new WheelEvent('wheel', {bubbles: true, deltaY: Math.max(240, target.clientHeight * 0.85)}));
          window.scrollTo(0, document.documentElement.scrollHeight);
          return before;
        })()""") or {}
        if trusted_wheel:
            page._call("trusted_wheel", {"deltaY": 600})
        time.sleep(scroll_delay)
        after_state = page.evaluate(r"""(() => {
          const candidates = [document.querySelector('.user-page'), document.scrollingElement]
            .filter(Boolean);
          const target = candidates.find((el) => el.scrollHeight > el.clientHeight + 20)
            || document.scrollingElement;
          return {top: target.scrollTop, height: target.scrollHeight, client: target.clientHeight};
        })()""") or {}
        before_height = scroll_state.get("height", 0)
        after_height = after_state.get("height", 0)
        if added == 0 and after_height <= before_height and after_state.get("top", 0) <= scroll_state.get("top", 0):
            unchanged_rounds += 1
        else:
            unchanged_rounds = 0
        pagination = snapshot.get("page_state") or {}
        current_query = pagination[1] if isinstance(pagination, list) and len(pagination) > 1 else pagination.get("1", {}) if isinstance(pagination, dict) else {}
        has_more = current_query.get("hasMore", True)
        if not has_more:
            break
        if unchanged_rounds >= 8:
            # Do not claim completeness; give the caller the partial result and
            # preserve the page state for a later retry.
            break

    # Always merge the final response, including data arriving after the last scroll.
    snapshot = page.evaluate(_EXTRACT_FAVORITES_JS) or {}
    _merge_items(collected, snapshot.get("items") or [])
    state = snapshot.get("page_state") or {}
    query = state[1] if isinstance(state, list) and len(state) > 1 else state.get("1", {}) if isinstance(state, dict) else {}
    exhausted = query.get("hasMore") is False
    items = list(collected.values())
    truncated = bool(max_items and len(items) > max_items)
    if max_items:
        items = items[:max_items]
    complete = exhausted and not truncated
    return {
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source_url": page.evaluate("location.href"),
        "user_id": (page.evaluate("location.pathname") or "").split("/")[-1],
        "total_items": len(items),
        "rounds": rounds,
        "pagination_exhausted": complete,
        "stop_reason": "max_items" if truncated else "has_more_false" if complete else "limit_or_stalled",
        "final_query": query,
        "pagination_responses": snapshot.get("requests") or [],
        "scope": "web_visible_favorite_notes_only",
        "items": items,
    }


def save_favorites(data: dict[str, Any], output: str) -> str:
    with open(output, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    return output
