import json
from pathlib import Path
from datetime import datetime
from config import BASE_DIR

MEMORY_FILE = BASE_DIR / "data" / "memory.json"

class MemorySystem:
    def __init__(self):
        self.memories = []
        self.load()
    
    def load(self):
        if MEMORY_FILE.exists():
            try:
                self.memories = json.loads(MEMORY_FILE.read_text(encoding="utf-8"))
            except:
                self.memories = []
    
    def save(self):
        MEMORY_FILE.parent.mkdir(exist_ok=True)
        MEMORY_FILE.write_text(json.dumps(self.memories, ensure_ascii=False, indent=2), encoding="utf-8")
    
    def add_memory(self, content: str, tags: list[str] = None):
        """新しい記憶を追加"""
        self.load() # Reload before adding to ensure latest state
        memory = {
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "tags": tags or []
        }
        self.memories.append(memory)
        self.save()
        print(f"[Memory] Added: {content}")
        
    def search(self, query: str, limit: int = 5) -> list[str]:
        """単純なキーワード検索（将来的にベクトル検索にできる）"""
        self.load() # Reload before searching
        results = []
        for m in reversed(self.memories):  # 新しい順
            if query in m["content"]:
                results.append(m["content"])
            # タグマッチ
            elif any(t in query for t in m["tags"]):
                results.append(m["content"])
                
        return results[:limit]

    def get_recent(self, limit: int = 5) -> list[str]:
        """直近の記憶を取得"""
        return [m["content"] for m in self.memories[-limit:]]
