"""Opt-in isolated Chrome launcher for CDP mode; never reuses the daily profile."""
from __future__ import annotations
import os, shutil, subprocess
from pathlib import Path
from .profile_manager import ProfileSpec

class ChromeProfileLauncher:
    def __init__(self, executable: str | None = None):
        self.executable=executable or shutil.which('chrome') or os.environ.get('CHROME_PATH')
    def command(self, spec: ProfileSpec, headless: bool=False) -> list[str]:
        if not self.executable: raise FileNotFoundError('Chrome not found; set CHROME_PATH')
        cmd=[self.executable, f'--user-data-dir={spec.profile_dir}', f'--remote-debugging-port={spec.debug_port}', '--no-first-run', '--no-default-browser-check', '--disable-background-networking']
        if headless: cmd.append('--headless=new')
        return cmd
    def launch(self, spec: ProfileSpec, headless: bool=False) -> subprocess.Popen:
        spec.profile_dir.mkdir(parents=True,exist_ok=True)
        return subprocess.Popen(self.command(spec,headless), creationflags=getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0))
