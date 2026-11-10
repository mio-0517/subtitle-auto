#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""英語動画 -> 日本語字幕付き動画（1本分の処理）

  python3 process.py /path/to/video.mp4
"""
import os, sys, json, re, subprocess, shutil, time
from pathlib import Path

import jobs as J

BASE = Path(__file__).resolve().parent
WORK, OUT, LOGS = BASE/"work", BASE/"output", BASE/"logs"
for d in (WORK, OUT, LOGS):
    d.mkdir(exist_ok=True)

def _load(name):
    p = BASE/name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

CFG = _load("config.json")
GLOSSARY = {k: v for k, v in _load("glossary.json").items() if not k.startswith("_")}

def _load_env():
    """.env から ANTHROPIC_API_KEY 等を読む（launchd はシェル環境を継承しないため）"""
    f = BASE/".env"
    if not f.exists():
        return
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

NO_LINE_START = "\u3002\u3001\uff01\uff1f!?)\uff09\u300d\u300f\u3011,.:;\u30fc\u301c~\u3005\u30a1\u30a3\u30a5\u30a7\u30a9\u30c3\u30e3\u30e5\u30e7\u30ee\u3041\u3043\u3045\u3047\u3049\u3063\u3083\u3085\u3087"

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".m4v", ".webm", ".avi", ".mp3", ".m4a", ".wav"}


def log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    try:
        with open(LOGS/"process.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


_CURRENT = {"id": None}


def set_job(jid):
    _CURRENT["id"] = jid


def prog(status=None, note=None, ratio=None, **extra):
    """UI に出す進捗を書く（jidが無いときは何もしない）"""
    jid = _CURRENT.get("id")
    if not jid:
        return
    f = dict(extra)
    if status:
        f["status"] = status
    if note is not None:
        f["note"] = note
    if ratio is not None:
        f["ratio"] = max(0.0, min(1.0, float(ratio)))
    try:
        J.update(jid, **f)
    except Exception:
        pass


def make_thumb(video: Path, jid: str):
    """一覧に出すサムネイルを1枚抜く"""
    try:
        J.THUMBS.mkdir(parents=True, exist_ok=True)
        out = J.THUMBS/("%s.jpg" % jid)
        if out.exists():
            return
        subprocess.run([ffmpeg_bin(), "-y", "-ss", "3", "-i", str(video),
                        "-frames:v", "1", "-vf", "scale=480:-2", str(out)],
                       capture_output=True, timeout=60)
    except Exception:
        pass


def media_duration(video: Path):
    try:
        r = subprocess.run([ffmpeg_bin(), "-i", str(video)], capture_output=True, text=True, timeout=30)
        m = re.search(r"Duration: (\d+):(\d+):(\d+)", r.stderr)
        if m:
            h, mi, se = (int(x) for x in m.groups())
            return h*3600 + mi*60 + se
    except Exception:
        pass
    return None


def run(cmd, cwd=None):
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if r.returncode != 0:
        raise RuntimeError("失敗: %s\n%s" % (" ".join(map(str, cmd)), r.stderr[-2000:]))
    return r


def ffmpeg_bin():
    """brew の ffmpeg が無くても pip の imageio-ffmpeg で動くようにする"""
    p = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg")
    if p:
        return p
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        raise RuntimeError("ffmpeg が見つかりません。brew install ffmpeg か pip install imageio-ffmpeg を実行してください")


# ---------- 1. 音声抽出 ----------
def extract_audio(video: Path) -> Path:
    wav = WORK/(video.stem + ".16k.wav")
    if wav.exists() and wav.stat().st_size > 0:
        log("音声は抽出済み: %s" % wav.name)
        return wav
    log("音声を抽出中...")
    run([ffmpeg_bin(), "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "pcm_s16le", str(wav)])
    return wav



def load_wav16(path: Path):
    """16kHz/mono/16bit の wav を numpy に読む。Whisper が内部で ffmpeg を
    呼ばずに済むようにするため（システムに ffmpeg が無くても動く）"""
    import wave
    import numpy as np
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise RuntimeError("想定外の wav 形式です")
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def ffmpeg_on_path():
    """外部ライブラリが 'ffmpeg' を直接叩く場合の保険"""
    try:
        exe = ffmpeg_bin()
        d = BASE/"bin"
        d.mkdir(exist_ok=True)
        link = d/"ffmpeg"
        if not link.exists():
            try:
                link.symlink_to(exe)
            except OSError:
                shutil.copy2(exe, link)
                link.chmod(0o755)
        os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
    except Exception:
        pass


# ---------- 2. 文字起こし ----------
def transcribe(wav: Path):
    cache = WORK/(wav.stem + ".en.json")
    if cache.exists():
        log("文字起こしキャッシュを再利用")
        return json.loads(cache.read_text(encoding="utf-8"))

    ffmpeg_on_path()
    engine = CFG.get("whisper_engine", "auto")
    segs = None
    if engine in ("auto", "mlx"):
        try:
            import mlx_whisper
            log("mlx-whisper で文字起こし (%s)" % CFG["mlx_model"])
            res = mlx_whisper.transcribe(
                load_wav16(wav), path_or_hf_repo=CFG["mlx_model"],
                language=CFG.get("source_language", "en"), verbose=False)
            segs = [{"start": s["start"], "end": s["end"], "en": s["text"].strip()}
                    for s in res["segments"] if s["text"].strip()]
        except ImportError:
            if engine == "mlx":
                raise
            log("mlx-whisper 未導入 -> faster-whisper にフォールバック")
    if segs is None:
        from faster_whisper import WhisperModel
        log("faster-whisper で文字起こし (%s)" % CFG["faster_whisper_model"])
        model = WhisperModel(CFG["faster_whisper_model"], device="cpu", compute_type="int8")
        it, _ = model.transcribe(load_wav16(wav), language=CFG.get("source_language", "en"),
                                 vad_filter=True)
        segs = [{"start": s.start, "end": s.end, "en": s.text.strip()}
                for s in it if s.text.strip()]

    log("%d セグメント" % len(segs))
    cache.write_text(json.dumps(segs, ensure_ascii=False, indent=1), encoding="utf-8")
    return segs


# ---------- 3. 翻訳 ----------
SYS_PROMPT = """あなたはトレード解説動画の字幕翻訳者です。英語の書き起こしを日本語字幕に訳します。

ルール:
- 与えられた行と1対1で対応させて訳す。行を統合したり分割したりしない。
- 字幕なので簡潔に。話し言葉の埋め草(you know, like, um, I mean, so yeah 等)は落とす。
- 専門用語は「用語対訳表」に必ず従う。表にない専門用語はカタカナ+英語併記。
- 原文にない情報を足さない。誇張しない。
- 文脈として示した行は訳さない。翻訳対象の行だけ返す。
- 1行が空になる場合も必ずその行番号を返す(空文字列でよい)。

出力は JSON 配列のみ。説明文もコードフェンスも書かない:
[{"i": 行番号, "ja": "日本語訳"}]
"""


def _anthropic_api(prompt: str) -> str:
    import urllib.request
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    body = json.dumps({
        "model": CFG.get("anthropic_model", "claude-sonnet-4-5"),
        "max_tokens": 8000,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=body,
        headers={"content-type": "application/json", "x-api-key": key,
                 "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req, timeout=240) as r:
        d = json.loads(r.read().decode("utf-8"))
    return "".join(b.get("text", "") for b in d.get("content", []))


def _claude_cli(prompt: str) -> str:
    exe = shutil.which("claude") or "claude"
    r = subprocess.run([exe, "-p", prompt], capture_output=True, text=True, timeout=600)
    if r.returncode != 0 or not r.stdout.strip():
        raise RuntimeError("claude CLI rc=%s\n  exe=%s\n  stderr=%r\n  stdout=%r"
                           % (r.returncode, exe, (r.stderr or "")[-600:], (r.stdout or "")[-600:]))
    return r.stdout


def pick_translator():
    e = CFG.get("translate_engine", "auto")
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    if e == "anthropic_api" or (e == "auto" and has_key):
        if not has_key:
            raise RuntimeError("ANTHROPIC_API_KEY が未設定です")
        return "anthropic_api", _anthropic_api
    if e in ("auto", "claude_cli") and shutil.which("claude"):
        return "claude_cli", _claude_cli
    raise RuntimeError("翻訳エンジンがありません。ANTHROPIC_API_KEY を設定するか、"
                       "Claude Code CLI (claude) をインストールしてください")


def _parse_json_array(text: str):
    t = re.sub(r"^\s*```(?:json)?|```\s*$", "", text.strip(), flags=re.S)
    m = re.search(r"\[.*\]", t, re.S)
    if not m:
        raise ValueError("JSON配列が見つかりません: %s" % t[:300])
    return json.loads(m.group(0))


def translate(segments):
    name, call = pick_translator()
    log("翻訳エンジン: %s / 対象 %d 行" % (name, len(segments)))
    n = int(CFG.get("chunk_size", 25))
    ctx = int(CFG.get("context_lines", 3))
    glossary_txt = "\n".join("- %s -> %s" % (k, v) for k, v in GLOSSARY.items())
    result = {}

    for start in range(0, len(segments), n):
        chunk = segments[start:start+n]
        pre = segments[max(0, start-ctx):start]
        post = segments[start+n:start+n+ctx]
        blocks = []
        if pre:
            blocks.append("# 直前の文脈(訳さない)\n" + "\n".join(s["en"] for s in pre))
        blocks.append("# 翻訳対象\n" + "\n".join(
            "%d\t%s" % (start+i, s["en"]) for i, s in enumerate(chunk)))
        if post:
            blocks.append("# 直後の文脈(訳さない)\n" + "\n".join(s["en"] for s in post))
        prompt = "%s\n# 用語対訳表\n%s\n\n%s" % (SYS_PROMPT, glossary_txt, "\n\n".join(blocks))

        want = set(range(start, start+len(chunk)))
        got = {}
        for attempt in range(3):
            try:
                for row in _parse_json_array(call(prompt)):
                    i = int(row["i"])
                    if i in want:
                        got[i] = str(row.get("ja", "")).strip()
                if want <= set(got):
                    break
                log("  再試行 %d/3 (欠け %d 行)" % (attempt+1, len(want - set(got))))
            except Exception as e:
                log("  翻訳エラー(%d/3): %s" % (attempt+1, str(e)[:200]))
                time.sleep(2 + attempt * 3)
        missing = want - set(got)
        if missing:
            log("  !! %d 行は英語のまま残します" % len(missing))
            for i in missing:
                got[i] = segments[i]["en"]
        result.update(got)
        done_n = min(start+n, len(segments))
        log("  %d/%d 行 完了" % (done_n, len(segments)))
        prog(note="日本語に翻訳中 %d/%d 行" % (done_n, len(segments)),
             ratio=0.55 + 0.35 * done_n / max(1, len(segments)))

    for i, s in enumerate(segments):
        s["ja"] = result.get(i, s["en"])
    return segments


# ---------- 4. 字幕整形 & SRT ----------
_ASCII = re.compile(r"[0-9A-Za-z]")


def wrap_ja(text: str, width: int, max_lines: int) -> str:
    """行頭禁則を守って折り返す"""
    text = text.strip()
    if len(text) <= width:
        return text
    lines, cur = [], ""
    for ch in text:
        mid_word = bool(cur) and _ASCII.match(cur[-1]) and _ASCII.match(ch)
        if len(cur) >= width and ch not in NO_LINE_START and not mid_word:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        head = lines[:max_lines-1]
        head.append("".join(lines[max_lines-1:]))
        lines = head
    return "\n".join(l.strip() for l in lines if l.strip())


def split_ja(text: str, width: int, max_lines: int):
    """字幕1枚に収まらない長文を、句読点優先で複数枚に割る"""
    cap = width * max_lines
    text = text.strip()
    if len(text) <= cap:
        return [text]
    parts = [x for x in re.split(r"(?<=[。、！？!?])", text) if x]
    chunks, cur = [], ""
    for part in parts:
        while len(part) > cap:
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(part[:cap])
            part = part[cap:]
        if len(cur) + len(part) <= cap:
            cur += part
        else:
            if cur:
                chunks.append(cur)
            cur = part
    if cur:
        chunks.append(cur)
    return chunks


def srt_time(t: float) -> str:
    ms = max(0, int(round(t * 1000)))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms)


def write_srt(segments, key, path: Path, wrap=False):
    w = int(CFG.get("max_chars_per_line", 20))
    ml = int(CFG.get("max_lines", 2))
    n = 0
    with open(path, "w", encoding="utf-8") as f:
        def emit(body, start, end):
            nonlocal n
            n += 1
            f.write("%d\n%s --> %s\n%s\n\n" % (n, srt_time(start), srt_time(end), body))

        for s in segments:
            body = s.get(key, "").strip()
            if not body:
                continue
            start = float(s["start"])
            end = max(float(s["end"]), start + 0.4)
            if not wrap:
                emit(body, start, end)
                continue
            pieces = split_ja(body, w, ml)
            if len(pieces) == 1:
                emit(wrap_ja(pieces[0], w, ml), start, end)
                continue
            total = sum(len(x) for x in pieces) or 1
            t = start
            for idx, piece in enumerate(pieces):
                if idx == len(pieces) - 1:
                    e = end
                else:
                    e = min(end, t + (end - start) * len(piece) / total)
                    if e - t < 0.5:
                        e = min(end, t + 0.5)
                if e <= t:
                    e = t + 0.3
                emit(wrap_ja(piece, w, ml), t, e)
                t = e
    return path


# ---------- 5. 動画に載せる ----------
def mux(video: Path, srt_ja: Path, srt_en, out_path: Path):
    mode = CFG.get("subtitle_mode", "soft")
    made = []

    if mode in ("soft", "both"):
        soft = out_path.with_name(out_path.stem + "_ja.mp4")
        cmd = [ffmpeg_bin(), "-y", "-i", str(video), "-i", str(srt_ja)]
        maps = ["-map", "0:v:0", "-map", "0:a:0?", "-map", "1:0"]
        meta = ["-metadata:s:s:0", "language=jpn", "-metadata:s:s:0", "title=日本語"]
        if srt_en and CFG.get("keep_english_srt", True) and Path(srt_en).exists():
            cmd += ["-i", str(srt_en)]
            maps += ["-map", "2:0"]
            meta += ["-metadata:s:s:1", "language=eng", "-metadata:s:s:1", "title=English"]
        cmd += maps + ["-c:v", "copy", "-c:a", "copy", "-c:s", "mov_text"] \
            + meta + ["-movflags", "+faststart", str(soft)]
        log("字幕トラックを埋め込み中 (再エンコードなし)")
        run(cmd)
        made.append(soft)

    if mode in ("hard", "both"):
        hard = out_path.with_name(out_path.stem + "_ja_burned.mp4")
        fs = int(CFG.get("hardsub_font_size", 22))
        style = ("FontName=Hiragino Sans,FontSize=%d,PrimaryColour=&H00FFFFFF,"
                 "OutlineColour=&H90000000,BorderStyle=3,Outline=1,Shadow=0,"
                 "MarginV=28,Alignment=2" % fs)
        vf = "subtitles=%s:force_style='%s'" % (srt_ja.name, style)
        log("字幕を焼き付け中 (VideoToolboxで再エンコード)")
        try:
            run([ffmpeg_bin(), "-y", "-i", str(video.resolve()), "-vf", vf,
                 "-c:v", "h264_videotoolbox", "-b:v", "5M", "-c:a", "copy", str(hard.resolve())],
                cwd=str(srt_ja.parent))
        except RuntimeError:
            log("VideoToolbox が使えないので libx264 で再試行")
            run([ffmpeg_bin(), "-y", "-i", str(video.resolve()), "-vf", vf,
                 "-c:v", "libx264", "-crf", "20", "-preset", "medium",
                 "-c:a", "copy", str(hard.resolve())],
                cwd=str(srt_ja.parent))
        made.append(hard)

    return made


# ---------- main ----------
def process(video: Path, jid=None):
    t0 = time.time()
    if jid is None:
        jid = J.job_id(video)
    set_job(jid)
    log("=" * 60)
    log("処理開始: %s" % video.name)

    dur = media_duration(video)
    prog(status="extracting", note="音声を取り出し中", ratio=0.03,
         name=video.name, size=video.stat().st_size, duration=dur)
    make_thumb(video, jid)

    wav = extract_audio(video)

    prog(status="transcribing", note="英語を聞き取り中（ここが一番長い）", ratio=0.10)
    segments = transcribe(wav)
    if not segments:
        raise RuntimeError("音声からテキストが取れませんでした")

    if CFG.get("translate_engine") == "manual":
        srt_en = write_srt(segments, "en", WORK/(video.stem + ".en.srt"))
        shutil.copy2(srt_en, OUT/srt_en.name)
        log("聞き取りまで完了。翻訳待ちにします（%d 行）" % len(segments))
        prog(status="awaiting", note="翻訳待ち — Claudeに「訳して」と言ってください",
             ratio=0.55, lines=len(segments), stem=video.stem,
             srt_en=srt_en.name)
        set_job(None)
        return []

    prog(status="translating", note="日本語に翻訳中 0/%d 行" % len(segments),
         ratio=0.55, lines=len(segments))
    segments = translate(segments)

    prog(status="muxing", note="字幕を貼り付け中", ratio=0.92)
    srt_ja = write_srt(segments, "ja", WORK/(video.stem + ".ja.srt"), wrap=True)
    srt_en = None
    if CFG.get("keep_english_srt", True):
        srt_en = write_srt(segments, "en", WORK/(video.stem + ".en.srt"))

    made = []
    if video.suffix.lower() in {".mp3", ".m4a", ".wav"}:
        log("音声ファイルなので SRT のみ出力")
    else:
        made = mux(video, srt_ja, srt_en, OUT/video.stem)

    for f in [srt_ja] + ([srt_en] if srt_en else []):
        shutil.copy2(f, OUT/f.name)

    mins = (time.time() - t0) / 60
    log("完了 (%.1f分) -> %s" % (mins, OUT))
    for m in made:
        log("  出力: %s" % m.name)

    prog(status="done", note="完了", ratio=1.0,
         output=made[0].name if made else None,
         srt_ja=srt_ja.name, srt_en=srt_en.name if srt_en else None,
         took_min=round(mins, 1))
    set_job(None)
    return made


def main():
    if len(sys.argv) < 2:
        print("使い方: python3 process.py <動画ファイル>")
        sys.exit(1)
    video = Path(sys.argv[1]).expanduser().resolve()
    if not video.exists():
        print("ファイルがありません: %s" % video)
        sys.exit(1)
    try:
        process(video)
    except Exception as e:
        log("!! エラー: %s" % e)
        sys.exit(1)


if __name__ == "__main__":
    main()
