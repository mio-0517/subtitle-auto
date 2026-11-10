#!/bin/bash
# 調子が悪いときにダブルクリック
PLIST="$HOME/Library/LaunchAgents/com.mio.subtitle-auto.plist"
echo "再起動しています..."
launchctl unload "$PLIST" 2>/dev/null
sleep 1
launchctl load "$PLIST" 2>/dev/null
for i in $(seq 1 20); do
  sleep 1
  curl -s -o /dev/null --max-time 2 http://localhost:8765/api/jobs && break
done
echo "完了。画面を開きます。"
open "http://localhost:8765"
sleep 1
osascript -e 'tell application "Terminal" to close (every window whose name contains "再起動")' >/dev/null 2>&1 &
exit 0
