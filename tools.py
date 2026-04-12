# Tool Definitions and Executor
import json
import ipaddress
import os
import socket
import subprocess
import threading
import time
import sqlite3
import urllib3
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote_plus, urlparse

from config import (
    CODEX_CMD,
    CODEX_LOG_TAIL_LINES,
    DATA_DIR,
    FETCH_MAX_CHARS,
    LOGS_DIR,
    WORKSPACE_DIR,
)


# ============ Path Safety ============

BLOCKED_HOSTNAMES = {
    "localhost",
    "127.0.0.1",
    "::1",
    "metadata.google.internal",
    "169.254.169.254",
}

SAFE_TOOL_DESCRIPTIONS = {
    "fetch_json": "fetch_json(url) - Fetch JSON from a public HTTP(S) host.",
    "fetch_url": "fetch_url(url) - Fetch text from a public HTTP(S) host.",
    "list_dir": "list_dir(path) - List files inside the sandboxed workspace.",
    "read_text": "read_text(path) - Read a UTF-8 text file inside the sandboxed workspace.",
    "read_url": "read_url(url) - Read a public web page as text.",
    "search_tweets": "search_tweets(query, limit=5) - Search the local tweet memory database.",
    "search_web": "search_web(query) - Search the public web.",
}

PRIVILEGED_TOOL_DESCRIPTIONS = {
    "copy_file": "copy_file(src, dst) - Copy a file or directory inside the sandboxed workspace.",
    "codex_job_start": "codex_job_start(prompt, workdir) - Start a Codex subprocess.",
    "codex_job_status": "codex_job_status(job_id) - Read Codex job status and logs.",
    "codex_job_stop": "codex_job_stop(job_id) - Stop a Codex subprocess.",
    "codex_read_output": "codex_read_output(lines=20) - Read Codex bridge output.",
    "codex_run_captured": "codex_run_captured(prompt, workdir) - Forward a prompt to the Codex bridge.",
    "codex_run_sync": "codex_run_sync(prompt, workdir) - Spawn Codex in a new terminal window.",
    "codex_send_input": "codex_send_input(text) - Send input to the Codex bridge.",
    "delete_dir": "delete_dir(path) - Delete a directory inside the sandboxed workspace.",
    "delete_file": "delete_file(path) - Delete a file inside the sandboxed workspace.",
    "move_file": "move_file(src, dst) - Move a file or directory inside the sandboxed workspace.",
    "run_python_code": "run_python_code(code) - Execute arbitrary local Python code.",
    "write_text": "write_text(path, content) - Write a UTF-8 text file inside the sandboxed workspace.",
}


def describe_available_tools(include_privileged: bool = False) -> str:
    descriptions = dict(SAFE_TOOL_DESCRIPTIONS)
    if include_privileged:
        descriptions.update(PRIVILEGED_TOOL_DESCRIPTIONS)
    return "\n".join(f"- {value}" for _, value in sorted(descriptions.items()))

def safe_path(rel_path: str) -> Path:
    """
    Resolve a path inside the sandboxed workspace.
    """
    path = Path(rel_path)
    if not path.is_absolute():
        path = WORKSPACE_DIR / rel_path

    resolved = path.resolve()
    workspace_root = WORKSPACE_DIR.resolve()

    try:
        resolved.relative_to(workspace_root)
    except ValueError as exc:
        raise ValueError(f"Path escapes workspace: {rel_path}") from exc

    return resolved


def resolve_public_url(url: str) -> dict[str, Any]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only http:// and https:// URLs are allowed")
    if not parsed.hostname:
        raise ValueError("URL must include a hostname")

    host = parsed.hostname.lower()
    if host in BLOCKED_HOSTNAMES or host.endswith(".local"):
        raise ValueError(f"Blocked host: {host}")

    try:
        addrinfo = socket.getaddrinfo(host, parsed.port or None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(f"Failed to resolve host: {host}") from exc

    public_ips = []
    for _, _, _, _, sockaddr in addrinfo:
        ip_text = sockaddr[0]
        ip = ipaddress.ip_address(ip_text)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise ValueError(f"Blocked non-public address: {ip_text}")
        if ip_text not in public_ips:
            public_ips.append(ip_text)

    target = parsed.path or "/"
    if parsed.query:
        target += f"?{parsed.query}"

    return {
        "url": url,
        "parsed": parsed,
        "host": host,
        "port": parsed.port or (443 if parsed.scheme == "https" else 80),
        "scheme": parsed.scheme,
        "target": target,
        "resolved_ips": public_ips,
    }


def validate_public_url(url: str) -> str:
    resolve_public_url(url)
    return url


def fetch_public_response(url: str) -> tuple[str, urllib3.response.BaseHTTPResponse]:
    resolved = resolve_public_url(url)
    headers = {
        "Host": resolved["host"],
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Mafuyu/1.0",
    }

    last_error: Exception | None = None
    for ip_text in resolved["resolved_ips"]:
        try:
            if resolved["scheme"] == "https":
                pool = urllib3.HTTPSConnectionPool(
                    host=ip_text,
                    port=resolved["port"],
                    assert_hostname=resolved["host"],
                    server_hostname=resolved["host"],
                )
            else:
                pool = urllib3.HTTPConnectionPool(
                    host=ip_text,
                    port=resolved["port"],
                )

            response = pool.request(
                "GET",
                resolved["target"],
                headers=headers,
                redirect=False,
                timeout=urllib3.Timeout(connect=10.0, read=30.0),
                retries=False,
            )
            if 300 <= response.status < 400:
                location = response.headers.get("Location", "")
                raise ValueError(f"HTTP redirects are not allowed: {location}")
            if response.status >= 400:
                raise urllib3.exceptions.HTTPError(f"HTTP {response.status}")
            return resolved["url"], response
        except Exception as exc:
            last_error = exc
            continue

    if last_error is None:
        raise ValueError("No resolved public IPs were available")
    raise last_error


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
        validated_url, resp = fetch_public_response(url)
        text_full = resp.data.decode("utf-8", errors="replace")

        text = text_full[:FETCH_MAX_CHARS]
        truncated = len(text_full) > FETCH_MAX_CHARS
        
        return {
            "url": validated_url,
            "status": resp.status,
            "content": text,
            "truncated": truncated
        }
    except Exception as e:
        return {"error": f"fetch_url failed: {e}"}


def fetch_json(url: str) -> dict:
    """HTTP GET, parse as JSON."""
    try:
        validated_url, resp = fetch_public_response(url)
        body = resp.data.decode("utf-8", errors="strict")
        
        return {
            "url": validated_url,
            "status": resp.status,
            "data": json.loads(body)
        }
    except json.JSONDecodeError as e:
        return {"error": f"JSON parse failed: {e}"}
    except (ValueError, UnicodeDecodeError, urllib3.exceptions.HTTPError) as e:
        return {"error": f"fetch_json failed: {e}"}


def read_url(url: str) -> dict:
    """
    Fetch URL and extract main text content using BeautifulSoup.
    Use this to read the details of a search result.
    """
    try:
        from bs4 import BeautifulSoup

        validated_url, resp = fetch_public_response(url)
        
        html = resp.data.decode("utf-8", errors="replace")
        soup = BeautifulSoup(html, "html.parser")
        
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
        if len(text) > 3000:
            text = text[:3000] + "...(truncated)"
        
        # Auto-summarize if content is too long (> 3000 chars)
        if False and len(text) > 3000:
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
            "url": validated_url,
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
            shell=False,
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
        import base64

        prompt_b64 = base64.b64encode(prompt.encode("utf-8")).decode("ascii")
        codex_cmd_literal = CODEX_CMD.replace("'", "''")
        ps_script = (
            "$prompt = [System.Text.Encoding]::UTF8.GetString("
            f"[System.Convert]::FromBase64String('{prompt_b64}')); "
            f"$codex = '{codex_cmd_literal}'; "
            "Start-Process -FilePath $codex "
            "-ArgumentList @('-a', 'never', $prompt) "
            "-NoNewWindow -Wait; "
            "Write-Host 'Done! You can close this window.' -ForegroundColor Green"
        )
        
        print(f"\n{'='*50}")
        print(f"[Codex] Spawning new window for task: {prompt[:80]}...")
        print(f"{'='*50}\n")
        
        subprocess.Popen(
            ["powershell", "-NoExit", "-Command", ps_script],
            cwd=workdir,
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        )
        
        return {
            "success": True,
            "output": "Codexを新しいウィンドウで起動したよ！そっちを確認してね。",
            "exit_code": 0,
        }
        
    except Exception as e:
        return {"success": False, "output": f"Codex launch error: {e}", "exit_code": -1}



SAFE_TOOLS = {
    "list_dir": list_dir,
    "read_text": read_text,
    "fetch_url": fetch_url,
    "fetch_json": fetch_json,
    "read_url": read_url,
    "search_web": search_web,
    "search_tweets": search_tweets,
}

PRIVILEGED_TOOLS = {
    "write_text": write_text,
    "delete_file": delete_file,
    "delete_dir": delete_dir,
    "move_file": move_file,
    "copy_file": copy_file,
    "codex_job_start": codex_job_start,
    "codex_job_status": codex_job_status,
    "codex_job_stop": codex_job_stop,
    "codex_run_sync": codex_run_sync,
    "codex_run_captured": codex_run_captured,
    "codex_read_output": codex_read_output,
    "codex_send_input": codex_send_input,
    "run_python_code": run_python_code,
}

ALL_TOOLS = {**SAFE_TOOLS, **PRIVILEGED_TOOLS}
TOOLS = SAFE_TOOLS


def execute_tool(tool_name: str, args: dict, allow_privileged: bool = False) -> str:
    """
    Execute a tool and return result as JSON string.
    """
    registry = ALL_TOOLS if allow_privileged else SAFE_TOOLS
    if not allow_privileged and tool_name in PRIVILEGED_TOOLS:
        return json.dumps({"error": f"Tool not available in chat context: {tool_name}"})
    if tool_name not in registry:
        return json.dumps({"error": f"Unknown tool: {tool_name}"})
    
    try:
        result = registry[tool_name](**args)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except TypeError as e:
        return json.dumps({"error": f"Invalid arguments for {tool_name}: {e}"})
    except Exception as e:
        return json.dumps({"error": f"Tool execution failed: {e}"})
