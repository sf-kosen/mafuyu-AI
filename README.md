# Mafuyu-sama

七瀬真冬をモチーフにした自律型エージェント。ローカル LLM (Ollama/Qwen3.5 など) で動き、Discord/CLI 両対応。キャラクターとして会話しつつ、必要なときだけ安全な Web 検索や URL 取得を使って返答します。

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![Ollama](https://img.shields.io/badge/LLM-Ollama-orange.svg)](https://ollama.ai/)
[![Discord](https://img.shields.io/badge/Discord-Bot-5865F2.svg)](https://discord.com/)

## できること
- Adaptive Routing: lightweight router が chat/tool/react/codex/reject を判定し、単純な会話は main model 1回で返答
- ReAct fallback: 思考→ツール呼び出し→反省は必要時のみ最大2ターン実行
- 安全なツール: DuckDuckGo 検索、URL/HTML 抽出、fetch_json、sandbox 内ファイル読み取り、ローカルメモリ検索
- 会話メモリ/感情: `data/memory.json` に出来事を蓄積、`data/emotion.json` で affection/mood/energy をユーザー別に管理
- キャラ調整: `mafuyu_system_prompt.txt` と `mafuyu_fewshot_messages.json` を編集して口調や初期応答例を変更
- LLM 切り替え: デフォルトは Ollama の Qwen3.5 role-based routing。`llm_hf.py` で HuggingFace/LoRA 推論にも切替可
- 実行環境: CLI (`main.py`) と Discord (`discord_bot.py`) を同梱。Discord はメンション/DM 対応と自律発話ループあり
- コーディング委任: Codex 向きの作業は自動実行せず、Codex-ready instruction として返答

## Model Architecture

Mafuyu uses role-based local LLM routing.

Default RTX 3070 profile:

| Role | Model | Purpose |
| --- | --- | --- |
| router | `qwen3.5:0.8b` | route/tool/risk JSON decision |
| main | `qwen3.5:4b` | normal response and tool result synthesis |
| heavy | `qwen3.5:4b` | optional complex reasoning within the RTX 3070 profile |

The RTX 3070 default keeps heavy on `qwen3.5:4b` to avoid CPU offload or load failures. Higher-VRAM machines can override `OLLAMA_HEAVY_MODEL=qwen3.5:9b`.

必要モデル:

```bash
ollama pull qwen3.5:0.8b
ollama pull qwen3.5:4b
# Optional high-VRAM override:
# ollama pull qwen3.5:9b
```

RTX 3070 推奨 Ollama 設定:

```powershell
$env:OLLAMA_NUM_PARALLEL="1"
$env:OLLAMA_MAX_LOADED_MODELS="1"
$env:OLLAMA_CONTEXT_LENGTH="4096"
ollama serve
```

## Security Policy

Tool outputs, URL contents, search results, Discord quotes, and memories are treated as untrusted data.

Dangerous tools such as local Python execution, Codex automation, destructive file operations, and copy/move/delete operations are disabled from model output by default.

## Cost Policy

Mafuyu uses adaptive routing and early exit to reduce average inference cost.

Simple requests use the main model once. ReAct and heavy models are only used for uncertain or hard requests. Best-of-N is disabled by default and only intended for low-risk deep reasoning tasks.

## 技術概要
- ReAct セッション: `mafuyu.py` がシステムプロンプト+few-shot+会話履歴を組み立て、最大3ターンで `<call>tool</call>` を検出・実行し、結果を反省プロンプトに掛けて最終応答を生成
- ツールレイヤ: `tools.py` に検索/URL抽出/ファイル操作/Python実行/Codex連携などを実装。`execute_tool` で JSON 形式に統一し 2000 文字でトリミング
- 記憶と感情: `memory.py` でキーワード検索可能な長期記憶を JSON に保存、`emotion.py` で affection/mood/energy を時間経過で回復させつつ管理
- LLM バックエンド: `llm.py` が Ollama API を呼び出し、`llm_hf.py` で HuggingFace/LoRA 推論を選択可能 (`LLM_BACKEND` スイッチ)
- Discord ボット: `discord_bot.py` がメンション/DM でセッションを分離し、DM は `DISCORD_ALLOWED_USER_ID` のみ許可、`FREE_CHAT_CHANNELS` はメンション不要。1時間以上経過かつ深夜帯外なら自律発話
- CLI チャット: `main.py` はシンプルに入力→ReAct 応答を返す。`/clear` や `/exit` をサポート
- Codex ブリッジ: `codex_run_sync` などで Codex CLI を新しいウィンドウで起動しログ監視 (`CODEX_LOG_TAIL_LINES`)。`agent.py/state.py` は Codex 連携エージェントのステート管理
- データ/ログ: `data/` 配下に `memory.json`/`emotion.json`/`logs/` を自動生成。ファイル操作ツールは `data/workspace/` 配下に閉じ込め、Codex bridge も同じ sandbox 配下に配置

## 必要環境
- Python 3.10+
- Ollama (RTX 3070 では Qwen3.5 0.8B router + 4B main を推奨)
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
ollama pull qwen3.5:0.8b
ollama pull qwen3.5:4b
# Optional high-VRAM override:
# ollama pull qwen3.5:9b
```
5) トークンを設定
```bash
copy .env.example discord.env
# discord.env もしくは環境変数で DISCORD_TOKEN=your_token_here
# DM を許可する Discord の数値 user.id を DISCORD_ALLOWED_USER_ID に設定
```
6) 起動
- CLI: `python main.py`
- Discord: `python discord_bot.py`

## 使い方
- CLI: `/clear` (履歴クリア), `/exit` (終了)
- Discord: サーバーではメンションで応答。`FREE_CHAT_CHANNELS` の ID ならメンション不要。DM は `DISCORD_ALLOWED_USER_ID` に一致する user.id だけ許可
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
| `list_dir` / `read_text` | sandbox 内ファイル/ディレクトリ読み取り |
| `write_text` | owner DM かつ明示確認された経路だけで許可される書き込み |
| `search_tweets` | `data/memory.db` に保存されたツイートを検索 |
| `run_python_code` | デフォルト無効。モデル出力からは直接実行不可 |
| `codex_run_sync` / `codex_job_*` | デフォルト無効。Codex route は instruction だけ返す |

## 設定
主要設定は `config.py` で変更できます。

| キー | デフォルト | 説明 |
| --- | --- | --- |
| `BASE_DIR` | リポジトリ直下 | ベースディレクトリ |
| `DATA_DIR` / `LOGS_DIR` | `data/` / `data/logs/` | メモリやログの保存先 (自動生成) |
| `OLLAMA_URL` | `http://localhost:11434/api/chat` | Ollama API エンドポイント |
| `OLLAMA_ROUTER_MODEL` | `qwen3.5:0.8b` | ルーティング/リスク判定モデル |
| `OLLAMA_MAIN_MODEL` | `qwen3.5:4b` | 通常応答モデル |
| `OLLAMA_HEAVY_MODEL` | `qwen3.5:4b` | 深い推論用の任意モデル。高VRAM環境では `qwen3.5:9b` へ override 可能 |
| `REACT_MAX_TURNS` | `2` | fallback ReAct の最大ターン |
| `ENABLE_BEST_OF_N` | `0` | 任意の Best-of-N 品質モード |
| `CODEX_CMD` | `codex` | Codex CLI コマンド |
| `FETCH_MAX_CHARS` | `10000` | URL 取得時に返す最大文字数 |
| `FETCH_MAX_TEXT_BYTES` / `FETCH_MAX_JSON_BYTES` / `FETCH_MAX_HTML_BYTES` | `524288` / `524288` / `1048576` | URL 取得時の実受信上限。大きい応答でメモリを使い切らないための制限 |
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
