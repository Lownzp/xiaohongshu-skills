"""Read-only collection safety: cooldown, circuit breaker, and result quality checks."""
from __future__ import annotations
import json, time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

@dataclass
class SafetyState:
    strikes: int = 0
    blocked_until: float = 0.0
    last_error: str = ""
    processed_this_run: int = 0

class CollectionSafety:
    def __init__(self, path: str | Path, min_gap: float = 25.0, hourly_limit: int = 60):
        self.path = Path(path); self.min_gap = min_gap; self.hourly_limit = hourly_limit
        self.state = self._load(); self._timestamps: list[float] = []
        self._last_start = 0.0
    def _load(self) -> SafetyState:
        try: return SafetyState(**json.loads(self.path.read_text(encoding='utf-8')))
        except (OSError, ValueError, TypeError): return SafetyState()
    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp=self.path.with_suffix('.tmp'); tmp.write_text(json.dumps(asdict(self.state),ensure_ascii=False,indent=2),encoding='utf-8'); tmp.replace(self.path)
    def wait_if_needed(self) -> float:
        now=time.time(); recent=[t for t in self._timestamps if now-t<3600]; self._timestamps=recent
        wait=max(0.0,self.state.blocked_until-now, self.min_gap-(now-self._last_start) if self._last_start else 0.0)
        if len(recent)>=self.hourly_limit: wait=max(wait,3600-(now-recent[0]))
        if wait: time.sleep(wait)
        self._last_start=time.time(); self._timestamps.append(self._last_start); return wait
    def record_error(self, error: str, cooldown_base: float = 1200.0) -> None:
        self.state.strikes += 1; self.state.last_error=error
        self.state.blocked_until=time.time()+min(3600.0,cooldown_base*(2**(self.state.strikes-1))); self.save()
    def record_success(self) -> None:
        self.state.processed_this_run += 1; self.save()
    def reset_strikes(self) -> None:
        self.state.strikes=0; self.state.blocked_until=0; self.state.last_error=''; self.save()

def classify_access_error(error: str) -> str:
    text=str(error).lower()
    if '300013' in text or '异常访问' in text or '安全限制' in text: return 'risk_300013'
    if '验证码' in text or '扫码' in text or 'captcha' in text: return 'verification_required'
    if '超时' in text or 'timeout' in text: return 'navigation_timeout'
    if '404' in text or '/404' in text: return 'not_found_or_blocked'
    return 'other'

def validate_detail(data: dict[str, Any], expected_id: str) -> tuple[bool,str]:
    detail=data.get('detail') or {}; note=detail.get('note') or {}
    if note.get('noteId') and note.get('noteId') != expected_id: return False,'note_id_mismatch'
    if not note: return False,'empty_detail'
    if not (note.get('title') or note.get('desc') or data.get('dom_body')): return False,'empty_content'
    return True,''
