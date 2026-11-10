#!/bin/bash
# ダブルクリックで字幕メーカーの画面を開きます
cd "$(dirname "$0")"
PLIST="$HOME/Library/LaunchAgents/com.mio.subtitle-auto.plist"

# 動いていなければ起動しておく
if ! curl -s -o /dev/null --max-time 2 http://localhost:8765/api/jobs; then
  launchctl unload "$PLIST" 2>/dev/null
  launchctl load "$PLIST" 2>/dev/null
  echo "起動中..."
  for i in $(seq 1 20); do
    sleep 1
    curl -s -o /dev/null --max-time 2 http://localhost:8765/api/jobs && break
  done
fi
open "http://localhost:8765"
sleep 1
osascript -e 'tell application "Terminal" to close (every window whose name contains "画面を開く")' >/dev/null 2>&1 &
exit 0
