# Chat Session
from pathlib import Path

from config import BASE_DIR
from llm import chat as llm_chat


# Load system prompt
SYSTEM_PROMPT_FILE = BASE_DIR / "mafuyu_system_prompt.txt"

def load_system_prompt() -> str:
    """Load Mafuyu system prompt."""
    if SYSTEM_PROMPT_FILE.exists():
        return SYSTEM_PROMPT_FILE.read_text(encoding="utf-8").strip()
    
    # Default fallback
    return """あなたは「七瀬真冬（ななせ まふゆ）」として会話する。
口調: フランクで少し煽り・ダル絡み、根は優しい。
呼び方: ユーザーは原則「オタク君」。
長さ: 1〜2文が基本。"""


class ChatSession:
    """Chat session with Mafuyu persona."""
    
    def __init__(self):
        self.system_prompt = load_system_prompt()
        self.history: list[dict] = []
    
    def reply(self, user_input: str) -> str:
        """Get reply from Mafuyu."""
        response, self.history = llm_chat(
            user_input=user_input,
            history=self.history,
            system_prompt=self.system_prompt
        )
        return response
    
    def clear(self):
        """Clear conversation history."""
        self.history = []
