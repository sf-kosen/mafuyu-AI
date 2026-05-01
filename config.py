import os
from pathlib import Path

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = DATA_DIR / "logs"
WORKSPACE_DIR = DATA_DIR / "workspace"
CODEX_BRIDGE_DIR = WORKSPACE_DIR / "codex_bridge"

DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)
WORKSPACE_DIR.mkdir(exist_ok=True)
CODEX_BRIDGE_DIR.mkdir(exist_ok=True)

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/chat")

OLLAMA_ROUTER_MODEL = os.environ.get("OLLAMA_ROUTER_MODEL", "qwen3.5:0.8b")
OLLAMA_MAIN_MODEL = os.environ.get("OLLAMA_MAIN_MODEL", "qwen3.5:4b")
OLLAMA_HEAVY_MODEL = os.environ.get("OLLAMA_HEAVY_MODEL", "qwen3.5:9b")

OLLAMA_ROUTER_CTX = int(os.environ.get("OLLAMA_ROUTER_CTX", "2048"))
OLLAMA_MAIN_CTX = int(os.environ.get("OLLAMA_MAIN_CTX", "4096"))
OLLAMA_HEAVY_CTX = int(os.environ.get("OLLAMA_HEAVY_CTX", "4096"))

OLLAMA_ROUTER_PREDICT = int(os.environ.get("OLLAMA_ROUTER_PREDICT", "128"))
OLLAMA_MAIN_PREDICT = int(os.environ.get("OLLAMA_MAIN_PREDICT", "512"))
OLLAMA_HEAVY_PREDICT = int(os.environ.get("OLLAMA_HEAVY_PREDICT", "768"))

OLLAMA_ROUTER_KEEP_ALIVE = os.environ.get("OLLAMA_ROUTER_KEEP_ALIVE", "5m")
OLLAMA_MAIN_KEEP_ALIVE = os.environ.get("OLLAMA_MAIN_KEEP_ALIVE", "10m")
OLLAMA_HEAVY_KEEP_ALIVE = os.environ.get("OLLAMA_HEAVY_KEEP_ALIVE", "0")

ENABLE_ADAPTIVE_ROUTING = os.environ.get("ENABLE_ADAPTIVE_ROUTING", "1") == "1"
ROUTER_CONFIDENCE_EARLY_EXIT = float(os.environ.get("ROUTER_CONFIDENCE_EARLY_EXIT", "0.85"))
ROUTER_CONFIDENCE_VERIFY = float(os.environ.get("ROUTER_CONFIDENCE_VERIFY", "0.65"))
ROUTER_CONFIDENCE_HEAVY = float(os.environ.get("ROUTER_CONFIDENCE_HEAVY", "0.45"))

REACT_MAX_TURNS = int(os.environ.get("REACT_MAX_TURNS", "2"))

ENABLE_BEST_OF_N = os.environ.get("ENABLE_BEST_OF_N", "0") == "1"
BEST_OF_N_MAX = int(os.environ.get("BEST_OF_N_MAX", "3"))

ENABLE_LOCAL_PYTHON_TOOL = os.environ.get("ENABLE_LOCAL_PYTHON_TOOL", "0") == "1"
ENABLE_CODEX_TOOLS = os.environ.get("ENABLE_CODEX_TOOLS", "0") == "1"
ENABLE_CODEX_BRIDGE_AUTOSTART = os.environ.get("ENABLE_CODEX_BRIDGE_AUTOSTART", "0") == "1"

CODEX_CMD = os.environ.get("CODEX_CMD", "codex")

DISCORD_ALLOWED_USER_ID = int(os.environ.get("DISCORD_ALLOWED_USER_ID", "0"))

def parse_int_set(env_name: str) -> set[int]:
    raw = os.environ.get(env_name, "")
    return {
        int(x.strip())
        for x in raw.split(",")
        if x.strip().isdigit()
    }

ALLOWED_ROLE_IDS = parse_int_set("ALLOWED_ROLE_IDS")
FREE_CHAT_CHANNELS = parse_int_set("FREE_CHAT_CHANNELS")

OLLAMA_MODEL = OLLAMA_MAIN_MODEL

FETCH_MAX_CHARS = int(os.environ.get("FETCH_MAX_CHARS", "6000"))
FETCH_MAX_TEXT_BYTES = int(os.environ.get("FETCH_MAX_TEXT_BYTES", str(512 * 1024)))
FETCH_MAX_JSON_BYTES = int(os.environ.get("FETCH_MAX_JSON_BYTES", str(512 * 1024)))
FETCH_MAX_HTML_BYTES = int(os.environ.get("FETCH_MAX_HTML_BYTES", str(1024 * 1024)))
CODEX_LOG_TAIL_LINES = int(os.environ.get("CODEX_LOG_TAIL_LINES", "80"))
