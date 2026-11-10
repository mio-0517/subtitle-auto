#!/bin/bash
# 初回だけ実行するセットアップ
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
echo "======================================"
echo " 英語動画 -> 日本語字幕 自動化ツール"
echo " セットアップ: $ROOT"
echo "======================================"

# --- Python 仮想環境 ---
if [ ! -d ".venv" ]; then
  echo "[1/5] Python 仮想環境を作成..."
  python3 -m venv .venv
else
  echo "[1/5] 仮想環境はすでにあります"
fi
PY="$ROOT/.venv/bin/python"
"$PY" -m pip install -q -U pip wheel

# --- Whisper ---
echo "[2/5] Whisper を導入（数分かかります）..."
if [ "$(uname -m)" = "arm64" ]; then
  "$PY" -m pip install -q mlx-whisper || {
    echo "  mlx-whisper が入らなかったので faster-whisper を使います"
    "$PY" -m pip install -q faster-whisper
  }
else
  "$PY" -m pip install -q faster-whisper
fi

# --- ffmpeg ---
echo "[3/5] ffmpeg を確認..."
if command -v ffmpeg >/dev/null 2>&1; then
  echo "  システムの ffmpeg を使います: $(command -v ffmpeg)"
else
  echo "  見つからないので pip 版を入れます"
  "$PY" -m pip install -q imageio-ffmpeg
fi

# --- 翻訳エンジン ---
echo "[4/5] 翻訳エンジンを確認..."
ENGINE_OK=0
if [ -f ".env" ] && grep -q "ANTHROPIC_API_KEY=sk-" .env 2>/dev/null; then
  echo "  OK: .env の API キーを使います"
  ENGINE_OK=1
elif command -v claude >/dev/null 2>&1; then
  echo "  OK: Claude Code CLI を使います（$(command -v claude)）→ 翻訳の追加課金なし"
  ENGINE_OK=1
else
  echo ""
  echo "  翻訳エンジンがまだありません。"
  if command -v npm >/dev/null 2>&1; then
    echo "  Claude Code CLI を入れると、翻訳に追加課金がかかりません。"
    printf "  今インストールしますか？ [y/N]: "
    read -r ANS
    if [ "$ANS" = "y" ] || [ "$ANS" = "Y" ]; then
      echo "  インストール中..."
      if npm i -g @anthropic-ai/claude-code; then
        echo ""
        echo "  インストール完了。この後ターミナルで claude と打ってログインしてください。"
        ENGINE_OK=1
      else
        echo "  権限エラーの場合は sudo npm i -g @anthropic-ai/claude-code を試してください。"
      fi
    fi
  else
    echo "  npm が見つかりません。"
  fi
  if [ "$ENGINE_OK" = "0" ]; then
    echo ""
    echo "  ---- あとで必ずどちらかを済ませてください ----"
    echo "  (A) npm i -g @anthropic-ai/claude-code  そのあと claude でログイン"
    echo "  (B) echo 'ANTHROPIC_API_KEY=sk-ant-xxx' > $ROOT/.env"
    echo "  --------------------------------------------"
  fi
fi

# --- 自動起動 ---
echo "[5/5] フォルダ監視を常駐登録..."
PLIST="$HOME/Library/LaunchAgents/com.mio.subtitle-auto.plist"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.mio.subtitle-auto</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>$ROOT/watcher.py</string>
  </array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>$ROOT/logs/watcher.out.log</string>
  <key>StandardErrorPath</key><string>$ROOT/logs/watcher.err.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$HOME/.local/bin:$HOME/.npm-global/bin</string>
    <key>HOME</key><string>$HOME</string>
  </dict>
</dict>
</plist>
PLISTEOF
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo ""
echo "======================================"
echo " セットアップ完了"
echo ""
echo " 使い方: 英語の動画を下のフォルダに放り込むだけ"
echo "   $ROOT/inbox/"
echo " 出来上がりはここに出ます"
echo "   $ROOT/output/"
echo ""
echo " 進行状況を見る:"
echo "   tail -f $ROOT/logs/process.log"
echo "======================================"
