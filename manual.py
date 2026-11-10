#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""翻訳待ちのジョブに日本語訳を流し込んで仕上げる

  python3 manual.py list              翻訳待ち一覧
  python3 manual.py show <id>         英語の行を番号つきで出す
  python3 manual.py apply <id> <json> 訳を適用して動画まで仕上げる
                                      json は ["訳1","訳2",...] か {"0":"訳1",...}
"""
import json, shutil, sys, time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
import process as P
import jobs as J

WORK, OUT, INBOX = BASE/"work", BASE/"output", BASE/"inbox"
DONE = INBOX/"_処理済み"


def _segments(job):
    stem = job.get("stem") or Path(job.get("name", "")).stem
    f = WORK/("%s.16k.en.json" % stem)
    if not f.exists():
        raise SystemExit("聞き取りデータが見つかりません: %s" % f)
    return stem, json.loads(f.read_text(encoding="utf-8"))


def cmd_list():
    rows = [j for j in J.all_jobs() if j.get("status") == "awaiting"]
    if not rows:
        print("翻訳待ちはありません")
        return
    for j in rows:
        print("%s\t%s行\t%s" % (j["id"], j.get("lines", "?"), j.get("name")))


def cmd_show(jid):
    job = J.get(jid) or {}
    if not job:
        raise SystemExit("そのIDはありません: %s" % jid)
    _, segs = _segments(job)
    print("# %s / %d行" % (job.get("name"), len(segs)))
    for i, s in enumerate(segs):
        print("%d\t%s" % (i, s["en"]))


def cmd_apply(jid, jsonpath):
    job = J.get(jid)
    if not job:
        raise SystemExit("そのIDはありません: %s" % jid)
    stem, segs = _segments(job)

    raw = json.loads(Path(jsonpath).read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        ja = {int(k): v for k, v in raw.items()}
    else:
        ja = {i: v for i, v in enumerate(raw)}
    if len(ja) != len(segs):
        raise SystemExit("行数が合いません: 訳 %d / 原文 %d" % (len(ja), len(segs)))
    for i, s in enumerate(segs):
        s["ja"] = ja[i]

    t0 = time.time()
    P.set_job(jid)
    P.prog(status="muxing", note="字幕を貼り付け中", ratio=0.92)

    srt_ja = P.write_srt(segs, "ja", WORK/("%s.ja.srt" % stem), wrap=True)
    srt_en = P.write_srt(segs, "en", WORK/("%s.en.srt" % stem))
    for f in (srt_ja, srt_en):
        shutil.copy2(f, OUT/f.name)

    src = DONE/(job.get("source") or job.get("name") or "")
    made = []
    if src.exists() and src.suffix.lower() not in {".mp3", ".m4a", ".wav"}:
        made = P.mux(src, srt_ja, srt_en, OUT/stem)

    P.prog(status="done", note="完了", ratio=1.0,
           output=made[0].name if made else None,
           srt_ja=srt_ja.name, srt_en=srt_en.name,
           took_min=round((time.time()-t0)/60 + float(job.get("asr_min", 0) or 0), 1))
    P.set_job(None)
    print("仕上げ完了:", made[0].name if made else srt_ja.name)


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a or a[0] == "list":
        cmd_list()
    elif a[0] == "show" and len(a) > 1:
        cmd_show(a[1])
    elif a[0] == "apply" and len(a) > 2:
        cmd_apply(a[1], a[2])
    else:
        print(__doc__)
