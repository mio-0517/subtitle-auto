# subtitle-auto：英語動画 → 日本語字幕 自動生成ツール

英語の動画を画面にドラッグするだけで、**日本語字幕つきの動画**ができるローカルツールです。
英語のトレード解説動画を日本語で学ぶために、自分用に作りました。

```
動画 → 音声抽出(ffmpeg) → 英語書き起こし(Whisper) → 用語辞書つきLLM翻訳(Claude) → SRT生成 → 動画に字幕を合成(ffmpeg)
```

## 作った理由

YouTubeの自動翻訳では、FVG・order block・liquidity sweep のような専門用語が、
場面ごとにバラバラに訳されて内容が追えませんでした。
そこで **用語辞書（glossary.json）で訳を固定**し、前後の文脈も渡してLLMに翻訳させる仕組みにしました。

## 主な機能

- **ブラウザ画面からドラッグ＆ドロップで投入**（`localhost:8765` で動く自分専用の画面。外部には公開しない）
- **進み具合をリアルタイム表示**（聞き取り中／翻訳中 120/260行 など）、完了後はその場で字幕つき再生
- **フォルダ監視**：`inbox/` に動画を置くだけでも自動処理（launchdで常駐）
- **Whisperエンジンの自動切り替え**：Apple Silicon では `mlx-whisper`、それ以外は `faster-whisper`
- **用語辞書つき翻訳**：25行ずつ・前後3行の文脈つきでLLMに渡し、訳のブレとつながりの悪さを抑える
- **翻訳エンジンを選べる**：Anthropic API／Claude Code CLI／チャットでの手動翻訳（`config.json` で切り替え）
- **日本語字幕の整形**：1行20文字・最大2行に自動で折り返し・分割
- **ソフトサブ（字幕トラック埋め込み）とハードサブ（焼き付け）**に対応
- **URLから取り込み**（Google Drive／Dropbox／直リンク）。規約でダウンロードが禁止されているサイトは弾く
- 書き起こし結果をキャッシュし、翻訳だけのやり直しを高速化

## 技術スタック

Python（標準ライブラリ中心）／Whisper（mlx-whisper, faster-whisper）／ffmpeg／Anthropic Claude API／HTML + JavaScript（UI）／macOS launchd

## ファイル構成

| ファイル | 役割 |
|---|---|
| `process.py` | メイン処理（音声抽出 → 書き起こし → 翻訳 → SRT → 合成） |
| `server.py` | ローカルWebサーバー（UIとAPI、完成した動画の再生） |
| `ui.html` | ブラウザ画面 |
| `watcher.py` | `inbox/` の監視と自動処理 |
| `fetcher.py` | URLからの動画取り込み |
| `jobs.py` | ジョブ（処理状況）の管理 |
| `manual.py` | 手動翻訳モード用のコマンド |
| `glossary.json` | 用語辞書 |
| `config.json` | 設定（Whisperモデル、翻訳エンジン、字幕の形式など） |

## セットアップ

```bash
git clone https://github.com/mio-0517/subtitle-auto.git
cd subtitle-auto
bash setup.sh
```

API経由で翻訳する場合は、`.env` に `ANTHROPIC_API_KEY=...` を書きます（`.env` はGitに含めません）。
詳しい使い方は [docs/使い方.md](docs/使い方.md) にあります。

## 開発について

Claude Code を使ったAI駆動開発で作りました。
コードの多くはAIに書かせ、私は **何を作るかの設計、AIへの指示、動かないときの原因の切り分け、実際に使っての改善** を担当しました。
