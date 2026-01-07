# Tool Definitions and Executor
import json
import os
import subprocess
import threading
import requests
import sqlite3
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote_plus

from config import DATA_DIR, LOGS_DIR, CODEX_CMD, FETCH_MAX_CHARS, CODEX_LOG_TAIL_LINES


# ============ Path Safety (DISABLED - Full Access Mode) ============

def safe_path(rel_path: str) -> Path:
    """
    Resolve path. Full access mode - no restrictions.
    """
    # Convert to absolute path
    path = Path(rel_path)
    if not path.is_absolute():
        path = Path.cwd() / rel_path
    return path.resolve()


# ============ File Tools (Full Access Mode) ============

def list_dir(path: str = ".") -> dict:
    """List files in any directory."""
    try:
        target = safe_path(path)
        if not target.exists():
            return {"error": f"Directory not found: {path}"}
        if not target.is_dir():
            return {"error": f"Not a directory: {path}"}
        
        items = []
        for item in target.iterdir():
            items.append({
                "name": item.name,
                "type": "dir" if item.is_dir() else "file",
                "size": item.stat().st_size if item.is_file() else None
            })
        
        return {"path": str(target), "items": items}
    except Exception as e:
        return {"error": f"list_dir failed: {e}"}


def read_text(path: str) -> dict:
    """Read text file from any location."""
    try:
        target = safe_path(path)
        if not target.exists():
            return {"error": f"File not found: {path}"}
        if not target.is_file():
            return {"error": f"Not a file: {path}"}
        
        content = target.read_text(encoding="utf-8")
        return {"path": str(target), "content": content}
    except Exception as e:
        return {"error": f"read_text failed: {e}"}


def write_text(path: str, content: str) -> dict:
    """Write text file to any location."""
    try:
        target = safe_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"path": str(target), "written": len(content), "success": True}
    except Exception as e:
        return {"error": f"write_text failed: {e}"}


def delete_file(path: str) -> dict:
    """Delete a file."""
    try:
        target = safe_path(path)
        if not target.exists():
            return {"error": f"File not found: {path}"}
        if target.is_dir():
            return {"error": f"Use delete_dir for directories: {path}"}
        target.unlink()
        return {"path": str(target), "deleted": True}
    except Exception as e:
        return {"error": f"delete_file failed: {e}"}


def delete_dir(path: str) -> dict:
    """Delete a directory and all contents."""
    import shutil
    try:
        target = safe_path(path)
        if not target.exists():
            return {"error": f"Directory not found: {path}"}
        if not target.is_dir():
            return {"error": f"Not a directory: {path}"}
        shutil.rmtree(target)
        return {"path": str(target), "deleted": True}
    except Exception as e:
        return {"error": f"delete_dir failed: {e}"}


def move_file(src: str, dst: str) -> dict:
    """Move/rename a file or directory."""
    import shutil
    try:
        src_path = safe_path(src)
        dst_path = safe_path(dst)
        if not src_path.exists():
            return {"error": f"Source not found: {src}"}
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src_path), str(dst_path))
        return {"src": str(src_path), "dst": str(dst_path), "moved": True}
    except Exception as e:
        return {"error": f"move_file failed: {e}"}


def copy_file(src: str, dst: str) -> dict:
    """Copy a file."""
    import shutil
    try:
        src_path = safe_path(src)
        dst_path = safe_path(dst)
        if not src_path.exists():
            return {"error": f"Source not found: {src}"}
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        if src_path.is_dir():
            shutil.copytree(str(src_path), str(dst_path))
        else:
            shutil.copy2(str(src_path), str(dst_path))
        return {"src": str(src_path), "dst": str(dst_path), "copied": True}
    except Exception as e:
        return {"error": f"copy_file failed: {e}"}


# ============ Fetch Tools ============

def fetch_url(url: str) -> dict:
    """HTTP GET, return text (truncated to max chars)."""
    try:
        resp = requests.get(url, timeout=30, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Mafuyu/1.0"
        })
        resp.raise_for_status()
        
        text = resp.text[:FETCH_MAX_CHARS]
        truncated = len(resp.text) > FETCH_MAX_CHARS
        
        return {
            "url": url,
            "status": resp.status_code,
            "content": text,
            "truncated": truncated
        }
    except requests.RequestException as e:
        return {"error": f"fetch_url failed: {e}"}


def fetch_json(url: str) -> dict:
    """HTTP GET, parse as JSON."""
    try:
        resp = requests.get(url, timeout=30, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Mafuyu/1.0"
        })
        resp.raise_for_status()
        
        return {
            "url": url,
            "status": resp.status_code,
            "data": resp.json()
        }
    except requests.RequestException as e:
        return {"error": f"fetch_json failed: {e}"}
    except json.JSONDecodeError as e:
        return {"error": f"JSON parse failed: {e}"}


def read_url(url: str) -> dict:
    """
    Fetch URL and extract main text content using BeautifulSoup.
    Use this to read the details of a search result.
    """
    try:
        import requests
        from bs4 import BeautifulSoup
        
        resp = requests.get(url, timeout=30, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Mafuyu/1.0"
        })
        resp.raise_for_status()
        
        soup = BeautifulSoup(resp.text, "html.parser")
        
        # Remove script and style elements
        for script in soup(["script", "style", "nav", "footer", "header"]):
            script.decompose()
            
        # Get text
        text = soup.get_text(separator="\n")
        
        # Clean up whitespace
        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        text = '\n'.join(chunk for chunk in chunks if chunk)
        
        title = soup.title.string if soup.title else ""
        
        # Auto-summarize if content is too long (> 3000 chars)
        if len(text) > 3000:
            try:
                from llm import call_ollama
                summary_prompt = f"""以下のWebページの内容を、重要なポイントを抽出して300字以内で要約せよ。
事実のみ、簡潔に。

---
{text[:6000]}
---

要約:"""
                messages = [{"role": "user", "content": summary_prompt}]
                summary = call_ollama(messages)
                text = f"[要約]\n{summary}\n\n[元の文字数: {len(text)}文字]"
            except Exception as e:
                # Fallback: just truncate
                text = text[:3000] + "...(truncated)"
            
        return {
            "url": url,
            "title": title,
            "content": text
        }
    except ImportError:
        return {"error": "bs4 not installed. Run `pip install beautifulsoup4`"}
    except Exception as e:
        return {"error": f"read_url failed: {e}"}


def search_web(query: str) -> dict:
    """
    Search the web using duckduckgo-search library.
    Returns structured results: [{title, url, snippet}, ...]
    """
    try:
        # Sanity Check
        query = query.strip()
        if not query:
            return {"error": "Empty query"}
        if len(query) < 2 and not query.isascii(): # Single kana is ok? Maybe too risky
             pass # Let it slide for now, but watch out
        
        # Self-referential loop prevention
        # e.g. "search_web: search_web: ..."
        if "search_web:" in query or "tools." in query:
             clean_query = query.replace("search_web:", "").strip()
             if not clean_query:
                 return {"error": "Invalid query (self-reference)"}
             query = clean_query # Auto-fix
        
        # Temporal Awareness: Add current date to time-sensitive queries
        from datetime import datetime
        time_keywords = ['現在', '今', '最新', '今日', '首相', '大統領', '総裁', 
                         'current', 'now', 'latest', 'president', 'prime minister']
        query_lower = query.lower()
        if any(kw in query_lower or kw in query for kw in time_keywords):
            current_date = datetime.now().strftime("%Y年%m月")
            if current_date not in query:
                query = f"{query} {current_date}"
                print(f"[Smart Search] Added date: {query}")
        
        from ddgs import DDGS
        
        results = []
        with DDGS() as ddgs:
            # region="jp-jp" prioritizes Japanese results
            ddg_gen = ddgs.text(query, region="jp-jp", max_results=5)
            if ddg_gen:
                for r in ddg_gen:
                    results.append({
                        "title": r.get('title', ''),
                        "url": r.get('href', ''),
                        "snippet": r.get('body', '')
                    })

        return {
            "query": query,
            "results": results
        }
    except Exception as e:
        return {"error": f"search_web failed: {e}"}


def search_tweets(query: str, limit: int = 5) -> dict:
    """
    Search past tweets in the local database.
    RAG (Retrieval-Augmented Generation) function.
    """
    try:
        db_path = DATA_DIR / "memory.db"
        if not db_path.exists():
            return {"error": "Tweet database not found. Has ingestion been run?"}

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Simple LIKE search
        sql = "SELECT date, text, likes, retweets FROM tweets WHERE text LIKE ? ORDER BY date DESC LIMIT ?"
        cursor.execute(sql, (f"%{query}%", limit))
        rows = cursor.fetchall()
        
        results = []
        for row in rows:
            date, text, likes, retweets = row
            results.append(f"[{date}] {text} (Fav:{likes})")
        
        conn.close()
        
        if not results:
            return {"results": [], "summary": f"No tweets found for '{query}'"}
            
        return {
            "query": query,
            "count": len(results),
            "results": results,
            "formatted": "\n".join(results)
        }
    except Exception as e:
        return {"error": f"search_tweets failed: {e}"}


# ============ Codex Job Tools ============

# Global job registry
_codex_jobs: dict[str, dict] = {}


def codex_job_start(prompt: str, workdir: str = ".") -> dict:
    """
    Start Codex CLI as subprocess.
    Returns job_id for tracking.
    """
    import uuid
    
    job_id = uuid.uuid4().hex[:8]
    log_path = LOGS_DIR / f"codex_{job_id}.log"
    
    try:
        # Build command with non-interactive mode
        cmd = [CODEX_CMD, "-a", "never", prompt]
        
        # Open log file
        log_file = open(log_path, "w", encoding="utf-8")
        
        # Start process
        process = subprocess.Popen(
            cmd,
            cwd=workdir,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            shell=True,  # For PowerShell on Windows
            text=True,
        )
        
        _codex_jobs[job_id] = {
            "process": process,
            "log_file": log_file,
            "log_path": str(log_path),
            "prompt": prompt,
            "workdir": workdir,
        }
        
        return {
            "job_id": job_id,
            "log_path": str(log_path),
            "started": True
        }
    except Exception as e:
        return {"error": f"codex_job_start failed: {e}"}


def codex_job_status(job_id: str) -> dict:
    """Get status and last N lines of Codex job."""
    if job_id not in _codex_jobs:
        return {"error": f"Job not found: {job_id}"}
    
    job = _codex_jobs[job_id]
    process = job["process"]
    log_path = Path(job["log_path"])
    
    # Check if running
    poll = process.poll()
    state = "running" if poll is None else "done"
    exit_code = poll
    
    # Read last N lines
    last_lines = []
    if log_path.exists():
        try:
            lines = log_path.read_text(encoding="utf-8").splitlines()
            last_lines = lines[-CODEX_LOG_TAIL_LINES:]
        except Exception:
            pass
    
    return {
        "job_id": job_id,
        "state": state,
        "exit_code": exit_code,
        "last_lines": last_lines
    }


def codex_job_stop(job_id: str) -> dict:
    """Stop Codex job."""
    if job_id not in _codex_jobs:
        return {"error": f"Job not found: {job_id}"}
    
    job = _codex_jobs[job_id]
    process = job["process"]
    
    try:
        process.terminate()
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
    
    # Close log file
    try:
        job["log_file"].close()
    except Exception:
        pass
    
    return {
        "job_id": job_id,
        "stopped": True
    }



def run_python_code(code: str) -> dict:
    """
    Execute a snippet of Python code and capture the output.
    Useful for calculations, logic verification, or data processing.
    """
    try:
        # Run safely? Well, it's local execution.
        print(f"[Python] Executing code:\n{code[:80]}...")
        
        result = subprocess.run(
            ["python", "-c", code],
            capture_output=True,
            text=True,
            timeout=30 # Safety timeout
        )
        
        output = result.stdout
        if result.stderr:
            output += f"\n[STDERR]\n{result.stderr}"
            
        success = (result.returncode == 0)
        
        return {
            "success": success,
            "output": output,
            "exit_code": result.returncode
        }
    except Exception as e:
        return {"success": False, "output": f"Python execution failed: {e}", "exit_code": -1}




def codex_run_captured(prompt: str, workdir: str = ".") -> dict:
    """
    Start a new task on the Codex Bridge (Interactive Mode).
    Returns immediately after sending the request.
    Use 'codex_read_output' to see progress.
    """
    base_dir = r"c:\Users\Yukic\Desktop\mafuyu-sama"
    bridge_dir = os.path.join(base_dir, "codex_bridge")
    
    request_file = os.path.join(bridge_dir, "request.json")
    
    if not os.path.exists(bridge_dir):
        try: os.makedirs(bridge_dir)
        except: pass

    req_data = {"prompt": prompt}
    
    try:
        with open(request_file, "w", encoding="utf-8") as f:
            json.dump(req_data, f, ensure_ascii=False, indent=2)
        
        # We wait a brief moment to let Bridge pick it up?
        time.sleep(1)
        return {"success": True, "output": "Task sent to Bridge. Check output with 'codex_read_output'.", "exit_code": 0}
        
    except Exception as e:
        return {"success": False, "output": f"Error sending task: {e}", "exit_code": -1}

def codex_read_output(lines: int = 20) -> dict:
    """
    Read the latest output from the Codex Bridge.
    """
    base_dir = r"c:\Users\Yukic\Desktop\mafuyu-sama"
    bridge_dir = os.path.join(base_dir, "codex_bridge")
    output_file = os.path.join(bridge_dir, "output.log")
    
    if not os.path.exists(output_file):
        return {"success": True, "output": "(No output log found yet)", "exit_code": 0}
        
    try:
        # Read full file? Efficient enough for logs.
        with open(output_file, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
            
        # Get last N lines? Or just return all if not huge?
        # Let's return all.
        return {"success": True, "output": content, "exit_code": 0}
    except Exception as e:
        return {"success": False, "output": f"Error reading log: {e}", "exit_code": -1}

def codex_send_input(text: str) -> dict:
    """
    Send text input (e.g. 'yes', 'no') to the running Codex task.
    """
    base_dir = r"c:\Users\Yukic\Desktop\mafuyu-sama"
    bridge_dir = os.path.join(base_dir, "codex_bridge")
    input_file = os.path.join(bridge_dir, "input.txt")
    
    try:
        with open(input_file, "w", encoding="utf-8") as f:
            f.write(text)
        return {"success": True, "output": f"Sent input: {text}", "exit_code": 0}
    except Exception as e:
        return {"success": False, "output": f"Error sending input: {e}", "exit_code": -1}




def codex_run_sync(prompt: str, workdir: str = ".") -> dict:
    """
    Spawn Codex in a new terminal window.
    Returns immediately, leaving Codex running in the new window.
    
    Args:
        prompt: Task description for Codex
        workdir: Working directory
    
    Returns:
        {"success": bool, "output": str, "exit_code": int}
    """
    try:
        # Build command with full-auto mode
        # Sanitize prompt quotes to prevent command breakage
        # PowerShell requires careful quoting. We use ' for inner quotes.
        safe_prompt = prompt.replace("'", "''").replace('"', "'") 
        # Note: -a "never" (quoted) seems more stable on Windows
        inner_cmd = f"{CODEX_CMD} -a 'never' '{safe_prompt}'"
        cmd = f'start "Mafuyu Codex Agent" powershell -NoExit -Command "{inner_cmd}; Write-Host \'Done! You can close this window.\' -ForegroundColor Green"'
        
        print(f"\n{'='*50}")
        print(f"[Codex] Spawning new window for task: {prompt[:80]}...")
        print(f"{'='*50}\n")
        
        # Run shell command to spawn window
        subprocess.run(
            cmd,
            cwd=workdir,
            shell=True,
            check=True
        )
        
        return {
            "success": True,
            "output": "Codexを新しいウィンドウで起動したよ！そっちを確認してね。",
            "exit_code": 0,
        }
        
    except Exception as e:
        return {"success": False, "output": f"Codex launch error: {e}", "exit_code": -1}



TOOLS = {
    "list_dir": list_dir,
    "read_text": read_text,
    "write_text": write_text,
    "delete_file": delete_file,
    "delete_dir": delete_dir,
    "move_file": move_file,
    "copy_file": copy_file,
    "fetch_url": fetch_url,
    "fetch_json": fetch_json,
    "read_url": read_url,
    "search_web": search_web,
    "codex_job_start": codex_job_start,
    "codex_job_status": codex_job_status,
    "codex_job_stop": codex_job_stop,
    "codex_run_sync": codex_run_sync,
    "codex_run_captured": codex_run_captured,
    "codex_read_output": codex_read_output,
    "codex_send_input": codex_send_input,
    "run_python_code": run_python_code,
    "search_tweets": search_tweets,
}


def execute_tool(tool_name: str, args: dict) -> str:
    """
    Execute a tool and return result as JSON string.
    """
    if tool_name not in TOOLS:
        return json.dumps({"error": f"Unknown tool: {tool_name}"})
    
    try:
        result = TOOLS[tool_name](**args)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except TypeError as e:
        return json.dumps({"error": f"Invalid arguments for {tool_name}: {e}"})
    except Exception as e:
        return json.dumps({"error": f"Tool execution failed: {e}"})
