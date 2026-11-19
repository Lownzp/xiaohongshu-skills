"""Safe detail-opening helpers: exact note-id search-card click only."""
from __future__ import annotations
import json,re,time
from .errors import CDPError
class SafeDetailError(RuntimeError): pass

_CAPTCHA_MARKERS=('请求太频繁','安全验证','website-login/captcha','website-login/error','请一分钟后再试')

def is_rate_limited(page)->bool:
    """Detect the XHS captcha/rate-limit wall before treating a page as empty results."""
    try:
        state=page.evaluate("({url:location.href,t:(document.body?.innerText||'').slice(0,200)})") or {}
    except CDPError:
        return False
    url=state.get('url') or ''
    text=state.get('t') or ''
    return any(m in url for m in _CAPTCHA_MARKERS) or any(m in text for m in _CAPTCHA_MARKERS)

def _extract_id(url:str)->str|None:
    m=re.search(r'/(?:explore|search_result)/([0-9a-f]{24})',url or '')
    return m.group(1) if m else None

def open_from_search_card(page,note_id:str,title:str,author:str='',timeout:float=20.0,max_scrolls:int=6)->dict:
    # Exact match on href note id first. Text-based matching is only a fallback and
    # is bounded to card-sized elements, because a loose ancestor climb can match a
    # container holding many cards and click the wrong note.
    script=f'''(()=>{{const id={json.dumps(note_id)},title={json.dumps(title)},author={json.dumps(author)};
      const anchors=[...document.querySelectorAll('a[href*="/search_result/"]')];
      let a=anchors.find(x=>(x.getAttribute('href')||'').includes(id))||null;
      if(!a){{
        for(const x of anchors){{
          let e=x;for(let i=0;i<4&&e;i++,e=e.parentElement){{
            const r=e.getBoundingClientRect(),t=(e.innerText||'').trim();
            const cardLike=r.width>100&&r.width<700&&r.height>80&&r.height<900;
            const onlyOne=e.querySelectorAll('a[href*="/search_result/"]').length===1;
            if(cardLike&&onlyOne&&t.includes(title)&&(!author||t.includes(author))){{a=x;break}}
          }}
          if(a)break;
        }}
      }}
      if(!a)return {{found:false}};
      a.scrollIntoView({{block:'center'}});const r=a.getBoundingClientRect();
      setTimeout(()=>{{for(const ty of ['mousedown','mouseup','click'])a.dispatchEvent(new MouseEvent(ty,{{bubbles:true,cancelable:true,clientX:r.left+r.width/2,clientY:r.top+r.height/2,view:window}}));}},0);
      return {{found:true,href:a.href}};}})()'''
    result=None
    for _ in range(max_scrolls+1):
        if is_rate_limited(page):
            raise SafeDetailError('rate_limited')
        try:
            result=page.evaluate(script)
        except CDPError:
            time.sleep(1.5); continue
        if result and result.get('found'): break
        try: page.trusted_wheel(700,500,550)
        except CDPError: pass
        time.sleep(1.2)
    if not result or not result.get('found'): raise SafeDetailError('card_not_in_dom')
    time.sleep(1.5)
    deadline=time.monotonic()+timeout
    last={}
    while time.monotonic()<deadline:
        try:
            last=page.evaluate("({url:location.href,title:document.querySelector('#detail-title')?.innerText||document.title,body:document.querySelector('#detail-desc')?.innerText||'',tags:[...document.querySelectorAll('#detail-desc a.tag')].map(x=>x.innerText.trim())})") or {}
        except CDPError:
            last={}
        if last.get('body') and '/explore/' in (last.get('url') or ''):
            break
        time.sleep(0.6)
    got=_extract_id(last.get('url') or '')
    if got and got != note_id:
        raise SafeDetailError(f'note_id_mismatch expected={note_id} got={got}')
    if not last.get('body'):
        raise SafeDetailError('detail_overlay_timeout')
    return last
