#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""処理状況の記録（UIが読む）"""
import hashlib, json, os, tempfile, time
from pathlib import Path

BASE = Path(__file__).resolve().parent
STORE = BASE/"work"/"jobs.json"
THUMBS = BASE/"work"/"thumbs"

STAGES = {
    "queued":      "順番待ち",
    "extracting":  "音声を取り出し中",
    "transcribing":"英語を聞き取り中",
    "translating": "日本語に翻訳中",
    "muxing":      "字幕を貼り付け中",
    "done":        "完了",
    "failed":      "失敗",
}


def job_id(path: Path) -> str:
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return hashlib.sha1(("%s:%d" % (path.name, size)).encode("utf-8")).hexdigest()[:12]


def _read():
    if not STORE.exists():
        return {}
    try:
        return json.loads(STORE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write(data):
    STORE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(STORE.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, STORE)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def all_jobs():
    data = _read()
    out = list(data.values())
    out.sort(key=lambda j: j.get("created", 0), reverse=True)
    return out


def get(jid):
    return _read().get(jid)


def update(jid, **fields):
    data = _read()
    j = data.get(jid) or {"id": jid, "created": time.time()}
    j.update(fields)
    j["updated"] = time.time()
    data[jid] = j
    _write(data)
    return j


def remove(jid):
    data = _read()
    if jid in data:
        del data[jid]
        _write(data)
