# LLM Integration (Ollama API)
import json
import re
import requests
from typing import Optional

from config import OLLAMA_URL, OLLAMA_MODEL


def call_ollama(messages: list[dict], stream: bool = False) -> str:
    """
    Call Ollama chat API.
    
    Args:
        messages: List of {"role": "system"|"user"|"assistant", "content": "..."}
        stream: Whether to stream response (not implemented, always False)
    
    Returns:
        Assistant's response text
    """
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
    }
    
    try:
        resp = requests.post(OLLAMA_URL, json=payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        return data.get("message", {}).get("content", "").strip()
    except requests.RequestException as e:
        raise RuntimeError(f"Ollama API error: {e}")


def chat(user_input: str, history: list[dict], system_prompt: str) -> tuple[str, list[dict]]:
    """
    Chat with Mafuyu persona.
    
    Args:
        user_input: User's message
        history: Conversation history
        system_prompt: System prompt text
    
    Returns:
        (response, updated_history)
    """
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_input})
    
    response = call_ollama(messages)
    
    new_history = history + [
        {"role": "user", "content": user_input},
        {"role": "assistant", "content": response},
    ]
    
    return response, new_history


def extract_json(text: str) -> Optional[dict]:
    """
    Extract JSON object from text.
    Uses bracket counting to handle nested objects.
    """
    # Find first {
    start = text.find('{')
    if start == -1:
        return None
    
    # Count brackets to find matching }
    depth = 0
    in_string = False
    escape_next = False
    
    for i, char in enumerate(text[start:], start):
        if escape_next:
            escape_next = False
            continue
        
        if char == '\\' and in_string:
            escape_next = True
            continue
        
        if char == '"' and not escape_next:
            in_string = not in_string
            continue
        
        if in_string:
            continue
        
        if char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0:
                json_str = text[start:i+1]
                try:
                    return json.loads(json_str)
                except json.JSONDecodeError:
                    return None
    
    return None


REPAIR_PROMPT = """The following text was supposed to be valid JSON but has errors.
Fix it and output ONLY the corrected JSON, nothing else.

Expected schema:
{"action": "tool|say|finish", "tool_name": "string", "args": {}, "message": "string", "note": "string"}

Broken text:
{text}

Output only valid JSON:"""


def repair_json(broken_text: str) -> Optional[dict]:
    """
    Try to repair broken JSON using LLM.
    """
    messages = [
        {"role": "system", "content": "You are a JSON repair assistant. Output ONLY valid JSON."},
        {"role": "user", "content": REPAIR_PROMPT.format(text=broken_text)},
    ]
    
    response = call_ollama(messages)
    return extract_json(response)


AGENT_SYSTEM_PROMPT = """You are an autonomous agent. You execute tasks step by step.

CRITICAL: Output ONLY valid JSON. No explanation, no markdown, just JSON.

Schema:
{
  "action": "tool",
  "tool_name": "<name of the tool to use>",
  "args": {"<arg_name>": "<value>"},
  "message": "",
  "note": "<your next step memo>"
}

IMPORTANT: "action" MUST be exactly one of these strings:
- "tool" - when using a tool
- "say" - when you need to tell the user something
- "finish" - when the task is complete

CORRECT EXAMPLE (using write_text):
{"action": "tool", "tool_name": "write_text", "args": {"path": "data/test.txt", "content": "Hello"}, "message": "", "note": "File created"}

WRONG EXAMPLE (DO NOT DO THIS):
{"action": "write_text", ...}  <-- WRONG! action must be "tool", not the tool name

Available tools:
- list_dir(path), read_text(path), write_text(path, content)
- delete_file(path), delete_dir(path), move_file(src, dst), copy_file(src, dst)
- fetch_url(url), fetch_json(url), search_web(query)
- codex_job_start(prompt, workdir), codex_job_status(job_id), codex_job_stop(job_id)

Rules:
1. ONE action per response
2. Use "finish" with a message when goal is complete
"""


def agent_step(goal: str, history: list[dict], pending_notes: list[str], tool_result: Optional[str] = None) -> dict:
    """
    Execute one agent step.
    
    Args:
        goal: The task goal
        history: Agent conversation history
        pending_notes: Notes from user
        tool_result: Result from previous tool execution
    
    Returns:
        Parsed JSON action dict, or error dict
    """
    messages = [{"role": "system", "content": AGENT_SYSTEM_PROMPT}]
    
    # Add goal
    goal_msg = f"GOAL: {goal}"
    if pending_notes:
        goal_msg += f"\n\nUSER NOTES:\n" + "\n".join(f"- {n}" for n in pending_notes)
    
    messages.append({"role": "user", "content": goal_msg})
    messages.extend(history)
    
    # Add tool result if any
    if tool_result is not None:
        messages.append({"role": "user", "content": f"TOOL RESULT:\n{tool_result}"})
    
    # Get response
    response = call_ollama(messages)
    
    # Try to parse JSON
    result = extract_json(response)
    if result is not None:
        return result
    
    # Repair attempt (1 time)
    result = repair_json(response)
    if result is not None:
        return result
    
    # Failed
    return {
        "action": "error",
        "raw": response,
        "message": "Failed to parse agent response as JSON"
    }
