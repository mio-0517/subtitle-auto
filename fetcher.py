#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""URL から動画を取り込む（Google Drive / Dropbox / 直リンク）"""
import http.cookiejar, mimetypes, os, re, time, urllib.parse, urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
import sys
sys.path.insert(0, str(BASE))
import jobs as J
import process as P

INBOX = BASE/"inbox"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# 規約でダウンロードが禁じられている、または直接取得できないサイト
BLOCKED = {
    "youtube.com": "YouTube", "www.youtube.com": "YouTube", "m.youtube.com": "YouTube",
    "youtu.be": "YouTube", "music.youtube.com": "YouTube",
    "nicovideo.jp": "ニコニコ動画", "www.nicovideo.jp": "ニコニコ動画",
    "tiktok.com": "TikTok", "www.tiktok.com": "TikTok",
    "instagram.com": "Instagram", "www.instagram.com": "Instagram",
    "x.com": "X", "twitter.com": "X", "www.facebook.com": "Facebook",
}
VIDEO_CT = ("video/", "audio/", "application/octet-stream", "application/mp4")


class FetchError(Exception):
    pass


def check_url(raw: str):
    """使えるURLかを先に判定する。だめなら理由を返す"""
    u = (raw or "").strip()
    if not u:
        return None, "URLが空です"
    if not re.match(r"^https?://", u):
        u = "https://" + u
    host = (urllib.parse.urlparse(u).hostname or "").lower()
    for bad, label in BLOCKED.items():
        if host == bad or host.endswith("." + bad):
            return None, ("%s のリンクには対応していません。\n"
                          "動画のダウンロードが利用規約で禁止されているためです。\n"
                          "Google Drive / Dropbox / OneDrive の共有リンク、または\n"
                          "動画ファイルの直リンク(.mp4 で終わるURL)を使ってください。" % label)
    return u, None


def normalize(u: str) -> str:
    """共有リンクを実際にダウンロードできるURLに直す"""
    m = re.search(r"drive\.google\.com/file/d/([^/?#]+)", u)
    if m:
        return "https://drive.google.com/uc?export=download&id=%s" % m.group(1)
    m = re.search(r"drive\.google\.com/open\?id=([^&#]+)", u)
    if m:
        return "https://drive.google.com/uc?export=download&id=%s" % m.group(1)
    if "dropbox.com" in u:
        u = re.sub(r"([?&])dl=0", r"\1dl=1", u)
        if "dl=1" not in u:
            u += ("&" if "?" in u else "?") + "dl=1"
        return u
    if "1drv.ms" in u or "onedrive.live.com" in u:
        return u.replace("?e=", "?download=1&e=") if "download=1" not in u else u
    return u


def _opener():
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = [("User-Agent", UA)]
    return op


def _filename(resp, url, fallback="download.mp4"):
    cd = resp.headers.get("Content-Disposition", "")
    m = re.search(r"filename\*=UTF-8''([^;]+)", cd) or re.search(r'filename="?([^";]+)"?', cd)
    if m:
        name = urllib.parse.unquote(m.group(1))
    else:
        name = urllib.parse.unquote(
            os.path.basename(urllib.parse.urlparse(url).path)) or fallback
    name = re.sub(r'[/\\:\x00]', "_", name).strip() or fallback
    if "." not in name:
        ext = mimetypes.guess_extension(resp.headers.get("Content-Type", "").split(";")[0]) or ".mp4"
        name += ext
    return name


def fetch(url: str, jid: str):
    """実際に落とす。進捗は jobs に書く"""
    op = _opener()
    target = normalize(url)
    J.update(jid, status="downloading", note="接続中", ratio=0.02)

    resp = op.open(urllib.request.Request(target), timeout=60)
    ctype = (resp.headers.get("Content-Type") or "").lower()

    # Google Drive の確認画面を突破する
    if "text/html" in ctype and "drive.google" in target:
        html = resp.read(200000).decode("utf-8", "replace")
        m = (re.search(r'name="confirm"\s+value="([^"]+)"', html)
             or re.search(r"confirm=([0-9A-Za-z_-]+)", html))
        if m:
            target = target + "&confirm=" + m.group(1)
            resp = op.open(urllib.request.Request(target), timeout=60)
            ctype = (resp.headers.get("Content-Type") or "").lower()
        else:
            raise FetchError("Google Drive の共有設定を『リンクを知っている全員』にしてください")

    if "text/html" in ctype:
        raise FetchError("動画ファイルではなくWebページが返ってきました。\n"
                         "動画そのものの共有リンク、または .mp4 で終わる直リンクを貼ってください")
    if not any(ctype.startswith(c) for c in VIDEO_CT) and ctype:
        raise FetchError("動画ファイルではないようです (%s)" % ctype[:60])

    name = _filename(resp, target)
    dest = INBOX/name
    stem, suf = dest.stem, dest.suffix
    i = 2
    while dest.exists():
        dest = INBOX/("%s_%d%s" % (stem, i, suf))
        i += 1
    tmp = INBOX/("." + dest.name + ".part")

    total = int(resp.headers.get("Content-Length") or 0)
    got, last = 0, 0
    J.update(jid, status="downloading", note="ダウンロード中", ratio=0.05,
             name=name, size=total or None)
    with open(tmp, "wb") as f:
        while True:
            chunk = resp.read(262144)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if time.time() - last > 0.7:
                last = time.time()
                if total:
                    J.update(jid, ratio=min(.98, got/total),
                             note="ダウンロード中 %d%%" % int(got/total*100))
                else:
                    J.update(jid, note="ダウンロード中 %d MB" % (got//1048576))
    os.replace(tmp, dest)
    P.log("URLから取り込み完了: %s (%.1f MB)" % (dest.name, got/1048576))
    return dest
