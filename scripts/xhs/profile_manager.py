"""Isolated Chrome profile and port planning; does not launch or log in accounts."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class ProfileSpec:
    name: str
    profile_dir: Path
    debug_port: int

class ProfileManager:
    def __init__(self, root: str | Path, base_port: int = 9222):
        self.root=Path(root); self.base_port=base_port
    def spec(self, name: str, index: int = 0) -> ProfileSpec:
        safe=''.join(c if c.isalnum() or c in '-_' else '_' for c in name).strip('_') or 'default'
        return ProfileSpec(safe, self.root/safe, self.base_port+index)
    def prepare(self, name: str, index: int = 0) -> ProfileSpec:
        spec=self.spec(name,index); spec.profile_dir.mkdir(parents=True,exist_ok=True); return spec
