# Mafuyu Configuration
import os
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = DATA_DIR / "logs"
WORKSPACE_DIR = DATA_DIR / "workspace"
CODEX_BRIDGE_DIR = WORKSPACE_DIR / "codex_bridge"

# Ensure directories exist
DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)
WORKSPACE_DIR.mkdir(exist_ok=True)
CODEX_BRIDGE_DIR.mkdir(exist_ok=True)

# Ollama
OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "gemma3:12b"

# Codex
CODEX_CMD = "codex"  # Called via PowerShell

# Discord
DISCORD_ALLOWED_USER_ID = int(os.environ.get("DISCORD_ALLOWED_USER_ID", "0"))

# Limits
FETCH_MAX_CHARS = 10000
FETCH_MAX_TEXT_BYTES = 512 * 1024
FETCH_MAX_JSON_BYTES = 512 * 1024
FETCH_MAX_HTML_BYTES = 1024 * 1024
CODEX_LOG_TAIL_LINES = 80
