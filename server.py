#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ローカル専用ダッシュボード（127.0.0.1 のみ・外からは繋がらない）"""
import json, mimetypes, os, re, threading, time, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = Path(__file__).resolve().parent
import sys
sys.path.insert(0, str(BASE))
import jobs as J

PORT = 8765
INBOX = BASE/"inbox"
OUT = BASE/"output"
UI = BASE/"ui.html"

SAFE = re.compile(r"^[^/\\\x00]+$")


def _safe_name(name: str):
    name = urllib.parse.unquote(name)
    if not name or not SAFE.match(name) or name in (".", ".."):
        return None
    return name


def srt_to_vtt(text: str) -> str:
    body = re.sub(r"(\d\d:\d\d:\d\d),(\d\d\d)", r"\1.\2", text)
    body = body.replace("\r\n", "\n")
    return "WEBVTT\n\n" + body


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    # ---- helpers ----
    def _head(self, code, ctype, length, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def _bytes(self, code, ctype, data: bytes):
        self._head(code, ctype, len(data))
        if self.command != "HEAD":
            self.wfile.write(data)

    def _json(self, obj, code=200):
        self._bytes(code, "application/json; charset=utf-8",
                    json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _send_file(self, path: Path, ctype=None):
        if not path.exists() or not path.is_file():
            return self._bytes(404, "text/plain; charset=utf-8", "not found".encode())
        ctype = ctype or mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        size = path.stat().st_size
        rng = self.headers.get("Range")
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            if m:
                a, b = m.group(1), m.group(2)
                if self.command == "HEAD":
                    pass
                start = int(a) if a else 0
                end = int(b) if b else size - 1
                start = max(0, min(start, size - 1))
                end = max(start, min(end, size - 1))
                length = end - start + 1
                self.send_response(206)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(length))
                self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                if self.command == "HEAD":
                    return
                with open(path, "rb") as f:
                    f.seek(start)
                    remain = length
                    while remain > 0:
                        chunk = f.read(min(262144, remain))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remain -= len(chunk)
                return
        self._head(200, ctype, size, {"Accept-Ranges": "bytes"})
        if self.command == "HEAD":
            return
        with open(path, "rb") as f:
            while True:
                chunk = f.read(262144)
                if not chunk:
                    break
                self.wfile.write(chunk)

    # ---- routes ----
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        p, q = u.path, urllib.parse.parse_qs(u.query)

        if p in ("/", "/index.html"):
            if UI.exists():
                return self._bytes(200, "text/html; charset=utf-8",
                                   UI.read_bytes())
            return self._bytes(500, "text/plain; charset=utf-8", "ui.html がありません".encode())

        if p == "/api/jobs":
            jobs = J.all_jobs()
            for j in jobs:
                j["hasThumb"] = (J.THUMBS/("%s.jpg" % j.get("id", ""))).exists()
            return self._json({"jobs": jobs})

        if p.startswith("/api/thumb/"):
            jid = _safe_name(p[len("/api/thumb/"):])
            if not jid:
                return self._bytes(400, "text/plain", b"bad")
            return self._send_file(J.THUMBS/jid, "image/jpeg")

        if p == "/api/vtt":
            name = _safe_name((q.get("name") or [""])[0])
            if not name:
                return self._bytes(400, "text/plain", b"bad")
            f = OUT/name
            if not f.exists():
                return self._bytes(404, "text/plain", b"no srt")
            data = srt_to_vtt(f.read_text(encoding="utf-8", errors="replace"))
            return self._bytes(200, "text/vtt; charset=utf-8", data.encode("utf-8"))

        if p == "/media/source":
            name = _safe_name((q.get("name") or [""])[0])
            if not name:
                return self._bytes(400, "text/plain", b"bad")
            return self._send_file(INBOX/"_処理済み"/name)

        if p == "/media":
            name = _safe_name((q.get("name") or [""])[0])
            if not name:
                return self._bytes(400, "text/plain", b"bad")
            return self._send_file(OUT/name)

        return self._bytes(404, "text/plain; charset=utf-8", "not found".encode())

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        if u.path != "/api/fetch":
            return self._bytes(404, "text/plain", b"not found")
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        except Exception:
            return self._json({"ok": False, "error": "リクエストが不正です"}, 400)

        import fetcher
        url, err = fetcher.check_url(body.get("url", ""))
        if err:
            return self._json({"ok": False, "error": err}, 400)

        jid = "url%d" % int(time.time() * 1000 % 1000000000)
        J.update(jid, status="downloading", note="接続中", ratio=0.01,
                 name=url[:80], temp=True)

        def worker():
            try:
                fetcher.fetch(url, jid)
                J.remove(jid)      # 本体は watcher が拾って処理する
            except Exception as e:
                J.update(jid, status="failed", note="取り込み失敗",
                         ratio=0, error=str(e)[:500])

        threading.Thread(target=worker, daemon=True).start()
        return self._json({"ok": True, "id": jid})

    def do_PUT(self):
        u = urllib.parse.urlparse(self.path)
        if u.path != "/api/upload":
            return self._bytes(404, "text/plain", b"not found")
        q = urllib.parse.parse_qs(u.query)
        name = _safe_name((q.get("name") or [""])[0])
        if not name:
            return self._json({"ok": False, "error": "ファイル名が不正です"}, 400)
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return self._json({"ok": False, "error": "空のファイルです"}, 400)

        INBOX.mkdir(parents=True, exist_ok=True)
        dest = INBOX/name
        stem, suf = dest.stem, dest.suffix
        i = 2
        while dest.exists():
            dest = INBOX/("%s_%d%s" % (stem, i, suf))
            i += 1
        tmp = INBOX/("." + dest.name + ".part")
        remain = length
        try:
            with open(tmp, "wb") as f:
                while remain > 0:
                    chunk = self.rfile.read(min(1048576, remain))
                    if not chunk:
                        break
                    f.write(chunk)
                    remain -= len(chunk)
            os.replace(tmp, dest)
        except Exception as e:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            return self._json({"ok": False, "error": str(e)[:200]}, 500)
        return self._json({"ok": True, "name": dest.name})


def serve():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    srv.serve_forever()


if __name__ == "__main__":
    print("http://localhost:%d" % PORT)
    serve()
