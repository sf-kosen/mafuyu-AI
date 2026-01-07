# Mafuyu-sama

七瀬真冬をモチーフにした自律型 ReAct エージェント。ローカル LLM (Ollama/Gemma3 など) で動き、Discord/CLI 両対応。キャラクターとして会話しつつ Web 検索や URL 取得、ファイル/コード系ツールを自動で使い分けて返答します。

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![Ollama](https://img.shields.io/badge/LLM-Ollama-orange.svg)](https://ollama.ai/)
[![Discord](https://img.shields.io/badge/Discord-Bot-5865F2.svg)](https://discord.com/)

## できること
- ReAct ループ: 思考→ツール呼び出し→反省を最大3ターン繰り返し、足りない情報を自動で補完
- 豊富なツール: DuckDuckGo 検索、URL/HTML 抽出、fetch_json、ファイル入出力、Python 実行、Codex ブリッジ、ローカルメモリ検索
- 会話メモリ/感情: `data/memory.json` に出来事を蓄積、`data/emotion.json` で affection/mood/energy をユーザー別に管理
- キャラ調整: `mafuyu_system_prompt.txt` と `mafuyu_fewshot_messages.json` を編集して口調や初期応答例を変更
- LLM 切り替え: デフォルトは Ollama `gemma3:12b`。`llm_hf.py` で HuggingFace/LoRA 推論にも切替可
- 実行環境: CLI (`main.py`) と Discord (`discord_bot.py`) を同梱。Discord はメンション/DM 対応と自律発話ループあり
- コーディング委任: 複雑な開発タスクを Codex CLI で別プロセス実行する `codex_*` ツール群を用意

## 技術概要
- ReAct セッション: `mafuyu.py` がシステムプロンプト+few-shot+会話履歴を組み立て、最大3ターンで `<call>tool</call>` を検出・実行し、結果を反省プロンプトに掛けて最終応答を生成
- ツールレイヤ: `tools.py` に検索/URL抽出/ファイル操作/Python実行/Codex連携などを実装。`execute_tool` で JSON 形式に統一し 2000 文字でトリミング
- 記憶と感情: `memory.py` でキーワード検索可能な長期記憶を JSON に保存、`emotion.py` で affection/mood/energy を時間経過で回復させつつ管理
- LLM バックエンド: `llm.py` が Ollama API を呼び出し、`llm_hf.py` で HuggingFace/LoRA 推論を選択可能 (`LLM_BACKEND` スイッチ)
- Discord ボット: `discord_bot.py` がメンション/DM でセッションを分離し、`ALLOWED_USER` の DM のみ許可、`FREE_CHAT_CHANNELS` はメンション不要。1時間以上経過かつ深夜帯外なら自律発話
- CLI チャット: `main.py` はシンプルに入力→ReAct 応答を返す。`/clear` や `/exit` をサポート
- Codex ブリッジ: `codex_run_sync` などで Codex CLI を新しいウィンドウで起動しログ監視 (`CODEX_LOG_TAIL_LINES`)。`agent.py/state.py` は Codex 連携エージェントのステート管理
- データ/ログ: `data/` 配下に `memory.json`/`emotion.json`/`logs/` を自動生成。ファイル操作ツールは任意パスを書き換えるので運用環境では権限制御に注意

## 必要環境
- Python 3.10+
- Ollama (Gemma 3 12B を想定。16GB RAM 以上推奨)
- Discord Bot Token (Discord Developer Portal で取得)
- ネットワーク: Web検索/URL取得を使う場合に必要
- 追加ライブラリ: `pip install -r requirements.txt` で主要依存を導入。`requests` が無い場合は `pip install requests`

## セットアップ
1) リポジトリを取得
```bash
git clone https://github.com/yourusername/mafuyu-sama.git
cd mafuyu-sama
```
2) (任意) 仮想環境を作成
```bash
python -m venv .venv
./.venv/Scripts/activate  # Windows の例
```
3) 依存パッケージをインストール
```bash
pip install -r requirements.txt
pip install requests  # 必要なら
```
4) モデルを準備 (Ollama)
```bash
ollama pull gemma3:12b
```
5) トークンを設定
```bash
copy .env.example discord.env
# discord.env もしくは環境変数で DISCORD_TOKEN=your_token_here
```
6) 起動
- CLI: `python main.py`
- Discord: `python discord_bot.py`

## 使い方
- CLI: `/clear` (履歴クリア), `/exit` (終了)
- Discord: サーバーではメンションで応答。`FREE_CHAT_CHANNELS` の ID ならメンション不要。DM は `ALLOWED_USER` の名前だけ許可 (デフォルト `mikan.1111`)
- 自律発話: 最後の会話から1時間以上経過かつ 0-6 時を除くとき、DM チャンネルに一言投下する場合あり
- ツール利用例:
```text
@Mafuyu 今日の天気教えて       (search_web)
@Mafuyu このURL読んで https://example.com   (read_url)
@Mafuyu data/memo.txtの中身見せて            (read_text)
```
エージェントがキーワードから必要なツールを自動選択します。

### 代表的なツール
| ツール | 内容 |
| --- | --- |
| `search_web` | DuckDuckGo で上位結果を取得 |
| `read_url` / `fetch_url` / `fetch_json` | Webページ本文抽出 / テキスト取得 / JSON 取得 |
| `list_dir` / `read_text` / `write_text` | 任意パスのファイル/ディレクトリ操作 |
| `run_python_code` | 簡易な Python スニペット実行 |
| `search_tweets` | `data/memory.db` に保存されたツイートを検索 |
| `codex_run_sync` / `codex_job_*` | Codex CLI を別ウィンドウで起動・監視 |

## 設定
主要設定は `config.py` で変更できます。

| キー | デフォルト | 説明 |
| --- | --- | --- |
| `BASE_DIR` | リポジトリ直下 | ベースディレクトリ |
| `DATA_DIR` / `LOGS_DIR` | `data/` / `data/logs/` | メモリやログの保存先 (自動生成) |
| `OLLAMA_URL` | `http://localhost:11434/api/chat` | Ollama API エンドポイント |
| `OLLAMA_MODEL` | `gemma3:12b` | 使用モデル |
| `CODEX_CMD` | `codex` | Codex CLI コマンド |
| `FETCH_MAX_CHARS` | `10000` | URL 取得時の最大文字数 |
| `CODEX_LOG_TAIL_LINES` | `80` | Codex ログ tail 行数 |

キャラクターや Few-shot は `mafuyu_system_prompt.txt` / `mafuyu_fewshot_messages.json` を編集。長期記憶と感情は `data/memory.json` / `data/emotion.json` に保存されます。

## 構成
```
mafuyu-sama/
├── main.py                 # CLI チャットエントリ
├── discord_bot.py          # Discord ボット
├── mafuyu.py               # ReAct セッション/ツール判定/メモリ・感情管理
├── llm.py                  # Ollama 連携
├── llm_hf.py               # HuggingFace/LoRA バックエンド (オプション)
├── tools.py                # ツール定義 (検索/URL/ファイル/Codex 等)
├── memory.py               # 長期記憶ストア
├── emotion.py              # 感情状態ストア
├── agent.py, state.py      # Codex 向けエージェント基盤
├── chat.py                 # シンプルチャット用ラッパー
├── mafuyu_system_prompt.txt
├── mafuyu_fewshot_messages.json
├── help.txt                # ボット内ヘルプ
└── requirements.txt
```

## ライセンス
MIT License
