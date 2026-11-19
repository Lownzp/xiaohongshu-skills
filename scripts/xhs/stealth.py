"""Conservative browser-environment consistency helpers for isolated CDP profiles.

This module does not bypass verification or alter account actions. It only exposes a
small, opt-in pre-document script for test profiles; the existing Bridge profile is
left untouched.
"""
from __future__ import annotations

STEALTH_SCRIPT = r"""(() => {
  const native = Function.prototype.toString;
  const define = (obj, key, value) => { try { Object.defineProperty(obj, key, {get: () => value, configurable: true}); } catch (_) {} };
  define(Navigator.prototype, 'webdriver', undefined); // navigator.webdriver
  define(Navigator.prototype, 'languages', ['zh-CN', 'zh', 'en-US', 'en']);
  define(Navigator.prototype, 'vendor', 'Google Inc.');
  if (!window.chrome) window.chrome = {};
  if (!window.chrome.runtime) window.chrome.runtime = {};
  window.__xhsStealthInstalled = true;
  // Keep native function stringification for patched getters where possible.
  Function.prototype.toString = function(){ return native.call(this); };
})();"""

def install(page) -> None:
    """Install before a new navigation when using an isolated CDP Page."""
    page._send_session('Page.addScriptToEvaluateOnNewDocument', {'source': STEALTH_SCRIPT})
