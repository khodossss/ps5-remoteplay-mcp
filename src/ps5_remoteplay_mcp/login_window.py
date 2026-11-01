"""Linking window: Sony's own PSN sign-in page in a native webview.

Sony only redirects to its own page after sign-in, so a normal browser cannot hand the result
back to us. The embedded window watches its own navigation, takes the code from that redirect,
then shows a PIN form in the same window.
"""

from __future__ import annotations

import html

from . import console
from .backend.base import ConsoleError
from .config import Config

_STYLE = """
body { font-family: system-ui, sans-serif; background:#0d1117; color:#e6edf3; display:flex;
       align-items:center; justify-content:center; height:100vh; margin:0 }
.card { width: 360px; text-align:center }
h2 { font-weight:600 } p { color:#9da7b3; line-height:1.45 }
input { font-size:28px; letter-spacing:6px; width:220px; text-align:center; padding:8px;
        border-radius:8px; border:1px solid #30363d; background:#161b22; color:#e6edf3 }
button { margin-top:16px; font-size:16px; padding:10px 28px; border-radius:8px; border:0;
         background:#2f81f7; color:white; cursor:pointer }
.err { color:#f85149; min-height:1.4em } .ok { color:#3fb950 }
"""

_PIN_PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>{style}</style></head><body>
<div class="card">
  <h2>Signed in as {user}</h2>
  <p>On the PS5 open<br><b>Settings → System → Remote Play → Link Device</b><br>
  and type the 8-digit PIN here.</p>
  <form id="f"><input id="pin" inputmode="numeric" maxlength="9" autofocus autocomplete="off">
  <br><button type="submit">Link</button></form>
  <p id="msg" class="err"></p>
</div>
<script>
document.getElementById('f').onsubmit = async (e) => {{
  e.preventDefault();
  const msg = document.getElementById('msg');
  msg.className = ''; msg.textContent = 'Linking…';
  const r = await window.pywebview.api.submit_pin(document.getElementById('pin').value);
  if (r.ok) {{
    msg.className = 'ok'; msg.textContent = 'Linked! This window closes by itself.';
    setTimeout(() => window.pywebview.api.close(), 1500);
  }} else {{ msg.className = 'err'; msg.textContent = r.error; }}
}};
</script></body></html>"""

_ERROR_PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>{style}</style></head><body>
<div class="card"><h2>Sign-in failed</h2><p class="err">{error}</p>
<p>Close this window and run setup again.</p></div>
</body></html>"""


def run(cfg: Config, host: str, user: str | None = None) -> bool:
    """Show the window; returns True once the console accepted the PIN. Raises ImportError /
    RuntimeError when no GUI backend is available."""
    import webview

    state: dict = {"user": user, "linked": False}

    class Api:
        def submit_pin(self, pin: str) -> dict:
            try:
                console.link(cfg, host, state["user"], pin)
            except ConsoleError as err:
                return {"ok": False, "error": str(err)}
            state["linked"] = True
            return {"ok": True}

        def close(self) -> None:
            window.destroy()

    def pin_page() -> str:
        return _PIN_PAGE.format(style=_STYLE, user=html.escape(state["user"]))

    start_url = None if user else console.login_url()
    window = webview.create_window(
        "Link PS5 - PSN sign-in",
        url=start_url,
        html=pin_page() if user else None,
        js_api=Api(),
        width=520,
        height=780,
    )

    def on_loaded() -> None:
        if state["user"]:
            return
        url = window.get_current_url() or ""
        if not url.startswith(console.REDIRECT_PREFIX):
            return
        try:
            state["user"] = console.add_user_from_redirect(cfg, url)
        except ConsoleError as err:
            window.load_html(_ERROR_PAGE.format(style=_STYLE, error=html.escape(str(err))))
            return
        window.load_html(pin_page())

    window.events.loaded += on_loaded
    webview.start()
    return state["linked"]
