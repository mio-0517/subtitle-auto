#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""inbox を見張って自動処理し、ダッシュボード(8765番)も同時に立ち上げる"""
import json, os, shutil, subprocess, sys, threading, time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
import process as P
import jobs as J

INBOX = BASE/"inbox"
DONE = INBOX/"_処理済み"
FAILED = INBOX/"_失敗"
POLL = 3
STABLE_CHECKS = 3

# コードを書き換えたら自動で入れ替わるようにする（launchd が即座に立て直す）
SRC = ["watcher.py", "process.py", "server.py", "jobs.py", "config.json", "glossary.json"]
_mtime = {}


def snapshot_src():
    for n in SRC:
        f = BASE/n
        try:
            _mtime[n] = f.stat().st_mtime
        except OSError:
            _mtime[n] = None


def src_changed():
    for n in SRC:
        f = BASE/n
        try:
            m = f.stat().st_mtime
        except OSError:
            m = None
        if _mtime.get(n) != m:
            return n
    return None


def candidates():
    for p in sorted(INBOX.iterdir()):
        if p.is_dir() or p.name.startswith(".") or p.name.startswith("_"):
            continue
        if p.suffix.lower() in P.VIDEO_EXT:
            yield p


def wait_until_stable(p: Path) -> bool:
    last, same = -1, 0
    for _ in range(180):
        try:
            size = p.stat().st_size
        except FileNotFoundError:
            return False
        if size == last and size > 0:
            same += 1
            if same >= STABLE_CHECKS:
                return True
        else:
            same, last = 0, size
        time.sleep(1)
    return False


def diagnose():
    """翻訳エンジンがなぜ動かないかを1回だけ調べてログに残す"""
    flag = BASE/"work"/"diag.json"
    if flag.exists():
        return
    out = {}
    P.log("--- 翻訳エンジン診断 ---")
    try:
        mem = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=20)
        cpu = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True, timeout=20)
        gb = int(mem.stdout.strip() or 0) / (1024**3)
        P.log("Mac: %s / メモリ %.0f GB" % (cpu.stdout.strip(), gb))
        out["mem_gb"] = round(gb)
        out["cpu"] = cpu.stdout.strip()
    except Exception as e:
        P.log("スペック取得失敗: %s" % e)
    P.log("PATH = %s" % os.environ.get("PATH", ""))
    exe = shutil.which("claude")
    P.log("which claude = %s" % exe)
    out["which"] = exe
    for label, cmd in (("version", ["--version"]), ("hello", ["-p", "Reply with exactly: OK"])):
        if not exe:
            break
        try:
            r = subprocess.run([exe] + cmd, capture_output=True, text=True, timeout=180)
            P.log("  claude %s -> rc=%s" % (label, r.returncode))
            P.log("     stdout=%r" % (r.stdout or "")[:300])
            P.log("     stderr=%r" % (r.stderr or "")[:600])
            out[label] = {"rc": r.returncode, "out": (r.stdout or "")[:300],
                          "err": (r.stderr or "")[:600]}
        except Exception as e:
            P.log("  claude %s -> 例外 %s" % (label, e))
            out[label] = {"exception": str(e)[:300]}
    P.log("--- 診断ここまで ---")
    try:
        flag.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


def start_server():
    try:
        import server
        t = threading.Thread(target=server.serve, daemon=True)
        t.start()
        P.log("ダッシュボード: http://localhost:%d" % server.PORT)
    except Exception as e:
        P.log("!! ダッシュボードを起動できません: %s" % e)


def main():
    for d in (INBOX, DONE, FAILED):
        d.mkdir(parents=True, exist_ok=True)
    P.log("watcher 起動: %s を監視中" % INBOX)
    snapshot_src()
    diagnose()
    start_server()

    while True:
        try:
            changed = src_changed()
            if changed:
                P.log("%s が更新されたので再起動します" % changed)
                time.sleep(1)
                return
            for video in candidates():
                jid = J.job_id(video)
                rec = J.get(jid)
                if rec and rec.get("status") in ("done", "failed"):
                    continue
                if not rec:
                    J.update(jid, name=video.name, status="queued",
                             note="順番待ち", ratio=0.0,
                             size=video.stat().st_size)
                if not wait_until_stable(video):
                    continue
                P.log("新しいファイルを検出: %s" % video.name)
                try:
                    P.process(video, jid=jid)
                    dest = DONE/video.name
                    if dest.exists():
                        dest = DONE/("%s_%d%s" % (video.stem, int(time.time()), video.suffix))
                    shutil.move(str(video), str(dest))
                    J.update(jid, source=dest.name)
                    P.log("元ファイルを _処理済み/ へ移動")
                except Exception as e:
                    P.log("!! 失敗: %s / %s" % (video.name, e))
                    J.update(jid, status="failed", note="失敗", ratio=0.0,
                             error=str(e)[:500])
                    P.set_job(None)
                    try:
                        shutil.move(str(video), str(FAILED/video.name))
                    except Exception:
                        pass
        except Exception as e:
            P.log("!! watcher エラー: %s" % e)
        time.sleep(POLL)


if __name__ == "__main__":
    main()
