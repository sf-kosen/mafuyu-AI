# Mafuyu Configuration
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = DATA_DIR / "logs"

# Ensure directories exist
DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

# Ollama
OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "gemma3:12b"

# Codex
CODEX_CMD = "codex"  # Called via PowerShell

# Limits
FETCH_MAX_CHARS = 10000
CODEX_LOG_TAIL_LINES = 80
