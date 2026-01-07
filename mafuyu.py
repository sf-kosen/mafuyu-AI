# Mafuyu Simplified Response System
# JSON強制なし、キーワードでツール発動

import json
import re
from typing import Optional
from pathlib import Path
from datetime import datetime

from llm import call_ollama
from tools import execute_tool, codex_run_sync, TOOLS
from config import BASE_DIR


# ============ Prompt Loading ============

SYSTEM_PROMPT_PATH = BASE_DIR / "mafuyu_system_prompt.txt"
FEWSHOT_PATH = BASE_DIR / "mafuyu_fewshot_messages.json"

def load_system_prompt() -> str:
    """Load base system prompt from file."""
    if SYSTEM_PROMPT_PATH.exists():
        return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()
    return "あなたは真冬です。フランクに話してください。"

def load_fewshot() -> list[dict]:
    """Load few-shot examples from file."""
    if FEWSHOT_PATH.exists():
        try:
            return json.loads(FEWSHOT_PATH.read_text(encoding="utf-8"))
        except:
            pass
    return []


# ============ Tool Detection ============

# Keywords that trigger tool use
TOOL_TRIGGERS = {
    "search_web": ["調べ", "検索", "ググ", "天気", "何時", "ニュース", "search"],
    "read_url": ["サイト読", "詳しく", "中身", "詳細"],
    "write_text": ["書いて", "作成して", "保存して", "ファイル作って"],
    "read_text": ["読んで", "開いて", "見せて", "ファイルの中身"],
    "list_dir": ["一覧", "リスト", "フォルダの中"],
}

# Keywords that trigger Codex delegation (complex coding tasks)
CODEX_TRIGGERS = [
    "実装して", "コード書いて", "プログラム作って", "開発して",
    "botを作って", "bot作って", "ボット作って", "botを", "bot化",
    "discordに", "discordで", "slackに", "apiを",
    "スクリプト作って", "ツール作って",
]

def detect_tool_need(text: str) -> Optional[tuple[str, dict]]:
    """
    Detect if user wants a tool based on keywords.
    Returns (tool_name, args) or None.
    """
    text_lower = text.lower()
    
    # Search (Use full text as query for better context)
    for keyword in TOOL_TRIGGERS["search_web"]:
        if keyword in text:
            return ("search_web", {"query": text})
    
    # Write file
    for keyword in TOOL_TRIGGERS["write_text"]:
        if keyword in text:
            match = re.search(r'(.+?)\s*に\s*(.+?)\s*を?' + keyword, text)
            if match:
                return ("write_text", {"path": match.group(1).strip(), "content": match.group(2).strip()})
    
    # Read file (local)
    for keyword in TOOL_TRIGGERS["read_text"]:
        if keyword in text:
            match = re.search(r'(.+?)\s*を?' + keyword, text)
            if match:
                return ("read_text", {"path": match.group(1).strip()})
    
    # Read URL (Web)
    for keyword in TOOL_TRIGGERS["read_url"]:
        if keyword in text:
            match = re.search(r'(https?://[^\s]+)', text)
            if match:
                 return ("read_url", {"url": match.group(1).strip()})
    
    return None


def detect_codex_need(text: str) -> Optional[str]:
    """
    Detect if user wants complex coding task.
    Returns the prompt for Codex or None.
    """
    text_lower = text.lower()
    
    for keyword in CODEX_TRIGGERS:
        if keyword in text_lower or keyword in text:
            print(f"[DEBUG] Codex trigger found: '{keyword}'")
            return text
    
    print("[DEBUG] No Codex trigger found")
    return None


from memory import MemorySystem
from emotion import EmotionSystem

class MafuyuSession:
    """Mafuyu chat session - Soulful Mode with Memory & Thought."""
    
    def __init__(self):
        self.history: list[dict] = []
        self.max_history = 40  # Increased for better context retention
        self.system_prompt = load_system_prompt()
        self.fewshot = load_fewshot()
        self.memory = MemorySystem()
        self.emotion = EmotionSystem()
        self._tool_cache: dict[str, str] = {}  # Cache for tool results (query -> result)

    def respond(self, user_input: str, user_name: str = None, on_progress=None) -> str:
        """
        Generate a response using ReAct loop.
        on_progress: Optional callback function(status_text) to report progress.
        """
        # Ensure latest prompt is loaded
        self.system_prompt = load_system_prompt()
        
        # Build Context
        current_system_prompt = self.system_prompt
        
        # Inject Time
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
        current_system_prompt += f"\n\n[Current Time] {now_str}"

        # Inject Emotional State
        if user_name:
             user_key = user_name
             emo_prompt = self.emotion.get_prompt_text(user_key)
             current_system_prompt += f"\n\n{emo_prompt}"
             
             low_name = user_name.lower()
             if "mikan" in low_name:
                 current_system_prompt += f"\n\n[Active User Context] Name: {user_name} (Role: Creator/Partner)."
             else:
                 current_system_prompt += f"\n\n[Active User Context] Name: {user_name}."
        
        # 1. Retrieve relevant memories (RAG-lite)
        related_memories = self.memory.search(user_input, limit=3)
        memory_context = ""
        if related_memories:
            memory_context = "\n【長期記憶 (Memory)】\n" + "\n".join(f"- {m}" for m in related_memories)

        base_messages = [{"role": "system", "content": current_system_prompt}]
        base_messages.extend(self.fewshot)
        
        # Context Compression: If history is long, summarize older parts
        history_to_use = self.history[-self.max_history:]
        if len(self.history) > self.max_history:
            # Get compressed summary of older history
            compressed = self._get_compressed_context()
            if compressed:
                base_messages.append({"role": "system", "content": f"[会話履歴の要約]\n{compressed}"})
        
        base_messages.extend(history_to_use)
        
        # Add User Input
        user_content_list = [user_input]
        if memory_context:
            user_content_list.append(memory_context)
        
        # ReAct Loop Variables
        current_messages = base_messages.copy()
        current_messages.append({"role": "user", "content": "\n\n".join(user_content_list)})
        
        max_turns = 3
        final_response_text = ""
        
        print(f"\n--- ReAct Session Start ({user_name}) ---")
        
        for turn in range(max_turns):
            if on_progress and turn > 0:
                on_progress(f"Thinking... (Turn {turn+1})")
                
            # Call LLM
            response_text = call_ollama(current_messages)
            
            # Check for Tool Call: <call>tool: args</call>
            call_match = re.search(r'<call>(.*?): ?(.*?)</call>', response_text, re.DOTALL)
            
            # --- Parse Emotion & Memory from Thought (Always check) ---
            thought_match = re.search(r'<thought>(.*?)</thought>', response_text, re.DOTALL)
            if thought_match:
                thought_content = thought_match.group(1).strip()
                print(f"[Thought] {thought_content}")
                
                # Memory
                mem_match = re.search(r'<memory>(.*?)</memory>', thought_content, re.DOTALL)
                if mem_match:
                    self.memory.add_memory(mem_match.group(1).strip())
                
                # Emotion
                emo_match = re.search(r'<emotion>(.*?)</emotion>', thought_content, re.DOTALL)
                if emo_match:
                    self._update_emotion(user_name, emo_match.group(1).strip())

            if call_match:
                # Tool detected
                tool_name = call_match.group(1).strip()
                tool_args_str = call_match.group(2).strip()
                print(f"[Tool Call] {tool_name} -> {tool_args_str}")
                
                # Check cache for search_web
                cache_key = f"{tool_name}:{tool_args_str}"
                if tool_name == "search_web" and cache_key in self._tool_cache:
                    tool_result = self._tool_cache[cache_key]
                    print(f"[Cache Hit] Using cached result")
                else:
                    # Execute
                    tool_result = self._execute_tool_wrapper(tool_name, tool_args_str)
                    # Cache search results
                    if tool_name == "search_web":
                        self._tool_cache[cache_key] = tool_result
                
                # --- Reflection Phase: Check if result is sufficient ---
                reflection_prompt = f"""[Tool Result]
{tool_result}

[Reflection]
上記の結果でユーザーの質問に十分答えられるか判断せよ。
- 十分なら、そのまま回答を生成してください（ツール呼び出し不要）。
- 不足なら、追加のツール呼び出しを行ってください。"""
                
                # Append Assistant's thought/call and Reflection prompt
                current_messages.append({"role": "assistant", "content": response_text})
                current_messages.append({"role": "user", "content": reflection_prompt})
                
                # Continue logic loop
                continue
            else:
                # No tool call, this is the final response
                final_response_text = response_text
                break
        
        # Post-process Response (Cleanup)
        return self._clean_response(final_response_text, user_input)

    def _get_compressed_context(self) -> str:
        """Summarize older parts of history that exceed max_history."""
        # Get messages that will be dropped (older than max_history)
        if len(self.history) <= self.max_history:
            return ""
        
        old_messages = self.history[:-self.max_history]
        if not old_messages:
            return ""
        
        # Check if we already have a cached summary for this length
        cache_key = len(old_messages)
        if hasattr(self, '_compressed_cache') and self._compressed_cache.get('key') == cache_key:
            return self._compressed_cache.get('summary', '')
        
        # Build text to summarize
        history_text = ""
        for msg in old_messages[-20:]:  # Only summarize last 20 of old messages
            role = "ユーザー" if msg["role"] == "user" else "真冬"
            content = msg["content"][:200]  # Truncate long messages
            history_text += f"{role}: {content}\n"
        
        if not history_text.strip():
            return ""
        
        # Summarize using LLM
        try:
            summary_prompt = f"""以下の会話履歴を、重要なポイント（話題、約束、ユーザーの好み等）を抽出して100字以内で要約せよ。

{history_text}

要約:"""
            messages = [{"role": "user", "content": summary_prompt}]
            summary = call_ollama(messages)
            
            # Cache the result
            if not hasattr(self, '_compressed_cache'):
                self._compressed_cache = {}
            self._compressed_cache = {'key': cache_key, 'summary': summary}
            
            print(f"[Context Compression] Generated summary: {summary[:100]}...")
            return summary
        except Exception as e:
            print(f"[Context Compression] Error: {e}")
            return ""

    def _update_emotion(self, user_name, emo_text):
        """Parse and apply emotion updates like 'mood+5, affection+10'."""
        if not user_name:
            return
        
        user_key = user_name
        
        # Parse patterns like "mood+5", "affection-10", "energy+3"
        patterns = re.findall(r'(affection|mood|energy)\s*([+-])\s*(\d+)', emo_text, re.IGNORECASE)
        for param, sign, value in patterns:
            delta = int(value) if sign == '+' else -int(value)
            param_lower = param.lower()
            self.emotion.update(user_key, **{param_lower: delta})
            print(f"[Emotion Update] {user_key}: {param_lower} {sign}{value}")

    def _execute_tool_wrapper(self, name: str, raw_args: str) -> str:
        """Execute a tool and return the result as a formatted string."""
        # Convert raw_args string to proper dict based on tool type
        
        args = {}
        if name == "search_web":
            args = {"query": raw_args}
        elif name == "read_url":
            args = {"url": raw_args}
        elif name == "read_text":
            args = {"path": raw_args}
        elif name == "write_text":
            # Expect "path: content" or similar
            if ":" in raw_args:
                parts = raw_args.split(":", 1)
                args = {"path": parts[0].strip(), "content": parts[1].strip()}
            else:
                args = {"path": raw_args, "content": ""}
        elif name == "list_dir":
            args = {"path": raw_args if raw_args else "."}
        elif name == "codex_job_start" or name == "codex_run_sync" or name == "codex_run_captured":
            args = {"prompt": raw_args}
        elif name == "codex_send_input":
            args = {"text": raw_args}
        elif name == "codex_read_output":
            # Optional args? default to empty dict which uses default
            args = {} 
        elif name == "run_python_code":
            args = {"code": raw_args}
        elif name == "search_tweets":
            args = {"query": raw_args}
        else:
            # Try to parse as JSON for unknown tools
            try:
                args = json.loads(raw_args)
            except:
                args = {"arg": raw_args}
        
        try:
            res = execute_tool(name, args)
            
            # Format
            if isinstance(res, (dict, list)):
                res_str = json.dumps(res, ensure_ascii=False, indent=2)
            else:
                res_str = str(res)
                
            if len(res_str) > 2000:
                res_str = res_str[:2000] + "...(truncated)"
            return res_str
        except Exception as e:
            return f"Error: {e}"

    def _clean_response(self, text, user_input):
        """Clean LLM response: remove tags, quotes, excessive dots."""
        
        # 1. Remove Tags (Iterative to catch nested)
        for _ in range(2):
            text = re.sub(r'<thought>.*?</thought>', '', text, flags=re.DOTALL)
            text = re.sub(r'<call>.*?</call>', '', text, flags=re.DOTALL)
            text = re.sub(r'<memory>.*?</memory>', '', text, flags=re.DOTALL)
            text = re.sub(r'<emotion>.*?</emotion>', '', text, flags=re.DOTALL)
        
        text = text.strip()
        
        # 2. Strip OUTER wrapping quotes only (not in-content quotes)
        if len(text) >= 2:
            if (text[0] == '"' and text[-1] == '"') or \
               (text[0] == '"' and text[-1] == '"') or \
               (text[0] == '「' and text[-1] == '」') or \
               (text[0] == "'" and text[-1] == "'"):
                text = text[1:-1].strip()
        
        # 3. Collapse excessive dots/commas (but keep standard ellipsis)
        text = re.sub(r'、{2,}', '、', text)
        text = re.sub(r'。{2,}', '。', text)
        text = re.sub(r'\.{4,}', '...', text)
        text = re.sub(r'…{2,}', '…', text)
        
        # 4. Remove leading conjunctions (leftovers from thought tag removal)
        text = re.sub(r'^(が|でも|しかし|ですが|だけど)[、,。]?\s*', '', text).strip()
        
        # 5. Collapse excessive newlines
        text = re.sub(r'\n{3,}', '\n\n', text)
        
        # 6. Fallback: If cleanup removed everything
        if not text:
            text = "…えっと、なんだっけ？"

        # Save History
        self.history.append({"role": "user", "content": user_input})
        self.history.append({"role": "assistant", "content": text})
        
        return text

    def initiate_talk(self, user_name: str = None) -> Optional[str]:
        """
        Autonomously start a conversation.
        Only speaks if there is a 'desire' to speak.
        """
        # Build prompt for initiating talk
        current_system_prompt = self.system_prompt
        if user_name:
             user_key = user_name
             emo_prompt = self.emotion.get_prompt_text(user_key)
             current_system_prompt += f"\n\n{emo_prompt}"
        
        messages = [{"role": "system", "content": current_system_prompt}]
        messages.extend(self.fewshot)
        messages.extend(self.history[-self.max_history:])
        
        # Ask LLM if it wants to say something
        initiate_prompt = """
今、ユーザーは何も言っていない。
あなた（真冬）から話しかけたいことがあれば、一言だけ言って。
特に何もなければ、何も出力しないで（空欄で）。
"""
        messages.append({"role": "user", "content": initiate_prompt})
        
        response = call_ollama(messages)
        
        # Parse thought/emotion if present
        thought_match = re.search(r'<thought>(.*?)</thought>', response, re.DOTALL)
        if thought_match:
            thought_content = thought_match.group(1).strip()
            # Emotion
            emo_match = re.search(r'<emotion>(.*?)</emotion>', thought_content, re.DOTALL)
            if emo_match:
                self._update_emotion(user_name, emo_match.group(1).strip())
        
        # Clean response
        cleaned = re.sub(r'<thought>.*?</thought>', '', response, flags=re.DOTALL).strip()
        cleaned = re.sub(r'<emotion>.*?</emotion>', '', cleaned, flags=re.DOTALL).strip()
        
        # Strip outer quotes
        if len(cleaned) >= 2:
            if (cleaned[0] == '"' and cleaned[-1] == '"') or \
               (cleaned[0] == '"' and cleaned[-1] == '"'):
                cleaned = cleaned[1:-1].strip()
        
        if not cleaned or cleaned.isspace():
            return None
        
        # Save to history
        self.history.append({"role": "assistant", "content": cleaned})
        
        return cleaned
    
    def respond_with_codex(self, user_input: str, user_name: str = None) -> str:
        """
        Handle Codex task execution and generate response.
        """
        # Run Codex synchronously
        success, output = codex_run_sync(user_input)
        
        # Build response with Codex result
        messages = [{"role": "system", "content": self.system_prompt}]
        messages.extend(self.fewshot)
        messages.extend(self.history[-self.max_history:])
        
        status = "成功" if success else "失敗"
        user_content = f"{user_input}\n\n[Codex実行結果 ({status})]:\n{output}\n\n上記の結果を踏まえて真冬として返答して。"
        messages.append({"role": "user", "content": user_content})
        
        response = call_ollama(messages)
        
        # Save to history
        self.history.append({"role": "user", "content": user_input})
        self.history.append({"role": "assistant", "content": response})
        
        return response
    
    def clear_history(self):
        """Clear conversation history."""
        self.history = []
