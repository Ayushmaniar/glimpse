"""Local HTTP API + static UI, bound to 127.0.0.1 only.

Every /api call must carry the per-run random token (header X-Token, or ?t= for <img>
URLs). Without it, any web page open in a browser could POST to localhost and make the
app open things.

API (all JSON unless noted):
  GET  /                        UI (index.html with the token injected)
  GET  /api/status              {state, message, done, total, backend, counts, ready, sources}
  POST /api/search              {q, sources?: [names], literal?: bool, scope?: folder} -> SearchResponse
                                (+ time: {mode, label, start, end, browse, exts} | null)
  POST /api/search_image?scope= raw image bytes (Content-Type image/*) -> SearchResponse
                                scope: only files inside that folder ("search this folder" from File Explorer)
  POST /api/similar             {id}                                  -> SearchResponse
  POST /api/run                 {id, op?: "open"|"reveal"|"copy"|...} -> {ok, toast?, error?, copy?}
  POST /api/transcribe          raw audio bytes (webm/ogg/wav)        -> {text, ms}
  GET  /api/thumb/<name>?t=     JPEG thumbnail from .data/thumbs
  GET  /api/preview/<item id>?t= PDF page or audio waveform, drawn on demand and cached
  GET  /api/preview/<id>?size=large[&page=N]  Quick Look image (headers X-Page, X-Pages for PDFs)
  GET  /api/context/<item id>     {before, text, after} around a document/code hit
  GET  /api/settings            settings dict
  POST /api/settings            partial settings dict -> saved; triggers re-index
  POST /api/reindex             -> {ok}
  POST /api/hide                hide the launcher window
  POST /api/window              {height} -> resize the launcher to fit its content (56..640 px)
                                {moved: true} remember the dragged position · {recenter: true} forget it

SearchResponse = {query, ms, sections: [{source, label, panel, rank, intent, relevance,
                  widget: null | {title, tone, rows: [[label, value, tone?]]},
                  results: [Result]}]}
Result = {id, source, title, subtitle, snippet, score, thumb, icon, badge, actions: [{label, op}]}
"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlparse

from . import config, previews
from .core import Engine

log = logging.getLogger(__name__)
TOKEN = secrets.token_urlsafe(24)


def _scope(folder) -> str | None:
    """A folder to search inside, or None for everywhere (also for anything that isn't an existing folder)."""
    if not isinstance(folder, str) or not folder or not os.path.isdir(folder):
        return None
    return os.path.normpath(os.path.abspath(folder))
MAX_BODY = 25 * 1024 * 1024


class App:
    engine: Engine
    on_hide: Callable[[], None] = staticmethod(lambda: None)
    on_resize: Callable[[int], None] = staticmethod(lambda height: None)
    on_moved: Callable[[], None] = staticmethod(lambda: None)
    on_recenter: Callable[[], None] = staticmethod(lambda: None)


def _handler(app: App):
    class H(BaseHTTPRequestHandler):
        server_version = "SmartSearch"

        def log_message(self, fmt, *args):  # quiet
            log.debug(fmt, *args)

        # ---- plumbing ----------------------------------------------------------
        def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

        def _authed(self) -> bool:
            q = parse_qs(urlparse(self.path).query)
            ok = secrets.compare_digest(self.headers.get("X-Token", "") or (q.get("t") or [""])[0], TOKEN)
            if not ok:
                self._json({"error": "forbidden"}, 403)
            return ok

        def _body(self) -> bytes:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                raise ValueError("body too large")
            return self.rfile.read(n) if n else b""

        # ---- routes ------------------------------------------------------------
        def do_GET(self):
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                with open(os.path.join(config.UI_DIR, "index.html"), encoding="utf-8") as f:
                    html = f.read().replace("{{TOKEN}}", TOKEN)
                return self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            if path.startswith("/ui/"):
                name = os.path.normpath(path[4:]).replace("\\", "/")
                full = os.path.join(config.UI_DIR, name)
                if ".." in name or not os.path.isfile(full):
                    return self._json({"error": "not found"}, 404)
                with open(full, "rb") as f:
                    return self._send(200, f.read(), mimetypes.guess_type(full)[0] or "application/octet-stream")
            if not path.startswith("/api/") or not self._authed():
                return None if path.startswith("/api/") else self._json({"error": "not found"}, 404)
            if path == "/api/status":
                return self._json(app.engine.status_json())
            if path == "/api/settings":
                return self._json(config.all_settings())
            if path.startswith("/api/thumb/"):
                name = path.rsplit("/", 1)[1]
                full = os.path.join(config.THUMB_DIR, name)
                if not re.fullmatch(r"[\w.-]+\.jpg", name) or not os.path.isfile(full):
                    return self._json({"error": "not found"}, 404)
                with open(full, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "max-age=86400")
                self.end_headers()
                return self.wfile.write(data)
            if m := re.fullmatch(r"/api/preview/(\d+)", path):
                q = parse_qs(urlparse(self.path).query)
                page = (q.get("page") or [""])[0]
                headers = {}
                try:
                    if (q.get("size") or [""])[0] == "large":
                        got = previews.large(app.engine.store, int(m.group(1)),
                                             int(page) if page.isdigit() and 0 < int(page) < 100_000 else None)
                        if got:
                            got, headers = (got[0], "image/jpeg"), got[1]
                    else:
                        got = previews.preview(app.engine.store, int(m.group(1)))
                except Exception:
                    log.exception("preview %s failed", m.group(1))
                    got = None
                if got is None:
                    return self._json({"error": "no preview"}, 404)
                data, ctype = got
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "max-age=86400")
                for k, v in headers.items():
                    self.send_header(k, v)
                self.end_headers()
                return self.wfile.write(data)
            if m := re.fullmatch(r"/api/context/(\d+)", path):
                try:
                    ctx = previews.context(app.engine.store, int(m.group(1)))
                except Exception:
                    log.exception("context %s failed", m.group(1))
                    ctx = None
                return self._json(ctx) if ctx is not None else self._json({"error": "no context"}, 404)
            return self._json({"error": "not found"}, 404)

        def do_POST(self):
            path = urlparse(self.path).path
            if not path.startswith("/api/") or not self._authed():
                return None if path.startswith("/api/") else self._json({"error": "not found"}, 404)
            try:
                body = self._body()
                if path == "/api/search_image":
                    scope = _scope((parse_qs(urlparse(self.path).query).get("scope") or [None])[0])
                    return self._json(app.engine.search_image(body, scope=scope))
                if path == "/api/transcribe":
                    from . import voice
                    return self._json(voice.transcribe(body))
                data = json.loads(body or b"{}")
                if path == "/api/search":
                    q = (data.get("q") or "").strip()
                    if not q:
                        return self._json({"query": "", "ms": 0, "sections": []})
                    return self._json(app.engine.search_text(q, data.get("sources"), literal=bool(data.get("literal")),
                                                             scope=_scope(data.get("scope")),
                                                             debug=bool(data.get("debug"))))
                if path == "/api/similar":
                    return self._json(app.engine.similar(data["id"]))
                if path == "/api/run":
                    return self._json(app.engine.run(data["id"], data.get("op", "open")))
                if path == "/api/settings":
                    config.update(data)
                    app.engine.request_reindex()
                    return self._json(config.all_settings())
                if path == "/api/reindex":
                    app.engine.request_reindex()
                    return self._json({"ok": True})
                if path == "/api/hide":
                    app.on_hide()
                    return self._json({"ok": True})
                if path == "/api/window":
                    req = json.loads(body or b"{}")
                    if req.get("moved"):  # the user dragged the window: remember where
                        app.on_moved()
                    elif req.get("recenter"):
                        app.on_recenter()
                    else:
                        app.on_resize(max(56, min(640, int(req.get("height", 0)))))
                    return self._json({"ok": True})
            except Exception as e:
                log.exception("request %s failed", path)
                return self._json({"error": str(e)}, 500)
            return self._json({"error": "not found"}, 404)

    return H


def serve(engine: Engine, port: int = 0) -> tuple[ThreadingHTTPServer, App]:
    app = App()
    app.engine = engine
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _handler(app))
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, name="http", daemon=True).start()
    return httpd, app
