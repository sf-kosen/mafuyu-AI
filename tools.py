# Tool Definitions and Executor
import json
import ipaddress
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
    CODEX_BRIDGE_DIR,
    CODEX_LOG_TAIL_LINES,
    DATA_DIR,
    ENABLE_CODEX_TOOLS,
    ENABLE_LOCAL_PYTHON_TOOL,
    FETCH_MAX_CHARS,
    FETCH_MAX_HTML_BYTES,
    FETCH_MAX_JSON_BYTES,
    FETCH_MAX_TEXT_BYTES,
    LOGS_DIR,
    WORKSPACE_DIR,
)


# ============ Path Safety ============
#
# ここでは「ローカルファイル」と「外部URL」の両方に対して境界を作る。
# - ローカルファイルは data/workspace 配下だけを許可する
# - 外部URLは public な http(s) だけを許可する
# - DNS rebinding を避けるため、検証した IP に対して直接接続する

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

SAFE_TOOL_NAMES = set(SAFE_TOOL_DESCRIPTIONS.keys())
PRIVILEGED_TOOL_NAMES = set(PRIVILEGED_TOOL_DESCRIPTIONS.keys())

WRITE_TOOL_NAMES = {"write_text"}

DESTRUCTIVE_TOOL_NAMES = {
    "delete_file",
    "delete_dir",
    "move_file",
    "copy_file",
}

CODEX_TOOL_NAMES = {
    "codex_job_start",
    "codex_job_status",
    "codex_job_stop",
    "codex_read_output",
    "codex_run_captured",
    "codex_run_sync",
    "codex_send_input",
}

RCE_TOOL_NAMES = {
    "run_python_code",
}

NEVER_MODEL_CALLABLE_TOOL_NAMES = (
    DESTRUCTIVE_TOOL_NAMES
    | CODEX_TOOL_NAMES
    | RCE_TOOL_NAMES
)


def get_allowed_tool_names(
    *,
    allow_tools: bool,
    is_owner: bool = False,
    is_dm: bool = False,
    has_allowed_role: bool = False,
    privileged_confirmed: bool = False,
) -> set[str]:
    if not allow_tools:
        return set()

    allowed = set(SAFE_TOOL_NAMES)

    if is_owner and is_dm and privileged_confirmed:
        allowed |= WRITE_TOOL_NAMES

    allowed -= NEVER_MODEL_CALLABLE_TOOL_NAMES
    return allowed


def describe_available_tools(include_privileged: bool = False) -> str:
    """モデルに見せるツール一覧を説明文付きで返す。"""
    descriptions = dict(SAFE_TOOL_DESCRIPTIONS)
    if include_privileged:
        descriptions.update(PRIVILEGED_TOOL_DESCRIPTIONS)
    return "\n".join(f"- {value}" for _, value in sorted(descriptions.items()))

def safe_path(rel_path: str) -> Path:
    """
    指定されたパスを sandboxed workspace 内の絶対パスへ解決する。

    `..` などで workspace の外へ出ようとした場合は例外にする。
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
    """
    公開URLとして扱ってよいか検証し、接続に必要な情報を返す。

    この段階では DNS を解決して、private / loopback / link-local などの
    内部向けアドレスが混ざっていないことを確認する。
    """
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
    """URL 検証だけを行いたい呼び出し元向けの薄いラッパー。"""
    resolve_public_url(url)
    return url


def fetch_public_response(url: str) -> tuple[str, urllib3.response.BaseHTTPResponse]:
    """
    検証済みの public URL に対して実際に GET を行う。

    重要なのは、`requests.get(url)` のようにホスト名へ再解決しないこと。
    `resolve_public_url()` で得た IP に直接接続し、Host/SNI だけ元ホスト名を使う。
    これで redirect SSRF と DNS rebinding の両方を抑える。
    """
    resolved = resolve_public_url(url)
    headers = {
        "Host": resolved["host"],
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Mafuyu/1.0",
    }

    last_error: Exception | None = None
    for ip_text in resolved["resolved_ips"]:
        try:
            # HTTPS の場合は「接続先IP」と「証明書検証用ホスト名」を分ける。
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
                preload_content=False,
                timeout=urllib3.Timeout(connect=10.0, read=30.0),
                retries=False,
            )
            # redirect を許すと、検証済みの URL から内部URLへ飛ばされる。
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


def _reject_oversized_response(resp: urllib3.response.BaseHTTPResponse, max_bytes: int) -> None:
    """Content-Length が明示されている場合は、受信前に大きすぎる応答を拒否する。"""
    content_length = resp.headers.get("Content-Length")
    if not content_length:
        return

    try:
        expected_size = int(content_length)
    except ValueError:
        return

    if expected_size > max_bytes:
        raise ValueError(f"Response body too large: {expected_size} bytes (limit: {max_bytes})")


def _read_limited_response_body(resp: urllib3.response.BaseHTTPResponse, max_bytes: int) -> bytes:
    """
    応答本文をストリームで読み、展開後サイズに上限を掛ける。
    gzip などで圧縮された本文も decode_content=True で展開後サイズを数える。
    """
    _reject_oversized_response(resp, max_bytes)

    body = bytearray()
    for chunk in resp.stream(64 * 1024, decode_content=True):
        if not chunk:
            continue

        remaining = max_bytes - len(body)
        if remaining <= 0:
            raise ValueError(f"Response body exceeds {max_bytes} bytes")

        if len(chunk) > remaining:
            body.extend(chunk[:remaining])
            raise ValueError(f"Response body exceeds {max_bytes} bytes")

        body.extend(chunk)

    return bytes(body)


def _codex_bridge_paths() -> tuple[Path, Path, Path]:
    """Codex bridge 用の入出力ファイルを sandbox 配下にまとめる。"""
    bridge_dir = CODEX_BRIDGE_DIR
    bridge_dir.mkdir(parents=True, exist_ok=True)
    return (
        bridge_dir,
        bridge_dir / "request.json",
        bridge_dir / "output.log",
    )


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
    """公開URLの本文を文字列として取得する。"""
    try:
        validated_url, resp = fetch_public_response(url)
        try:
            text_full = _read_limited_response_body(resp, FETCH_MAX_TEXT_BYTES).decode("utf-8", errors="replace")
        finally:
            resp.release_conn()

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
    """公開URLの JSON を取得して Python オブジェクトへ変換する。"""
    try:
        validated_url, resp = fetch_public_response(url)
        try:
            body = _read_limited_response_body(resp, FETCH_MAX_JSON_BYTES).decode("utf-8", errors="strict")
        finally:
            resp.release_conn()
        
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
    Web ページを取得し、人が読みやすい本文テキストへ整形する。

    LLM に外部コンテンツを再要約させると間接プロンプトインジェクションの
    面が増えるため、ここでは BeautifulSoup で機械的に整形するだけにしている。
    """
    try:
        from bs4 import BeautifulSoup

        validated_url, resp = fetch_public_response(url)
        try:
            html = _read_limited_response_body(resp, FETCH_MAX_HTML_BYTES).decode("utf-8", errors="replace")
        finally:
            resp.release_conn()
        soup = BeautifulSoup(html, "html.parser")
        
        # スクリプトやレイアウト要素は本文抽出のノイズになるので落とす。
        for script in soup(["script", "style", "nav", "footer", "header"]):
            script.decompose()
            
        # ページ全体からテキストだけを抜き出す。
        text = soup.get_text(separator="\n")
        
        # 改行や余白を整えて、読みやすいプレーンテキストに寄せる。
        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        text = '\n'.join(chunk for chunk in chunks if chunk)
        
        title = soup.title.string if soup.title else ""
        # 長すぎるページは deterministic に打ち切る。
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
    Codex CLI を別プロセスで起動し、ログファイルへ出力を流す。

    以前は shell=True で起動していたが、コマンドインジェクション面を減らすため
    いまは shell=False で直接実行している。
    """
    if not ENABLE_CODEX_TOOLS:
        return {
            "success": False,
            "output": "Codex tools are disabled by default.",
            "exit_code": -1,
        }

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
    if not ENABLE_CODEX_TOOLS:
        return {
            "success": False,
            "output": "Codex tools are disabled by default.",
            "exit_code": -1,
        }

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
    if not ENABLE_CODEX_TOOLS:
        return {
            "success": False,
            "output": "Codex tools are disabled by default.",
            "exit_code": -1,
        }

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
    if not ENABLE_LOCAL_PYTHON_TOOL:
        return {
            "success": False,
            "output": "run_python_code is disabled by default.",
            "exit_code": -1,
        }

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
    if not ENABLE_CODEX_TOOLS:
        return {
            "success": False,
            "output": "Codex tools are disabled by default.",
            "exit_code": -1,
        }

    _, request_file, _ = _codex_bridge_paths()

    req_data = {"prompt": prompt}
    
    try:
        with request_file.open("w", encoding="utf-8") as f:
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
    if not ENABLE_CODEX_TOOLS:
        return {
            "success": False,
            "output": "Codex tools are disabled by default.",
            "exit_code": -1,
        }

    _, _, output_file = _codex_bridge_paths()
    
    if not output_file.exists():
        return {"success": True, "output": "(No output log found yet)", "exit_code": 0}
        
    try:
        with output_file.open("r", encoding="utf-8", errors="replace") as f:
            content = f.read().splitlines()

        tail = "\n".join(content[-max(1, lines):])
        return {"success": True, "output": tail, "exit_code": 0}
    except Exception as e:
        return {"success": False, "output": f"Error reading log: {e}", "exit_code": -1}

def codex_send_input(text: str) -> dict:
    """
    Send text input (e.g. 'yes', 'no') to the running Codex task.
    """
    if not ENABLE_CODEX_TOOLS:
        return {
            "success": False,
            "output": "Codex tools are disabled by default.",
            "exit_code": -1,
        }

    bridge_dir, _, _ = _codex_bridge_paths()
    input_file = bridge_dir / "input.txt"
    
    try:
        with input_file.open("w", encoding="utf-8") as f:
            f.write(text)
        return {"success": True, "output": f"Sent input: {text}", "exit_code": 0}
    except Exception as e:
        return {"success": False, "output": f"Error sending input: {e}", "exit_code": -1}




def codex_run_sync(prompt: str, workdir: str = ".") -> dict:
    """
    新しい PowerShell ウィンドウで Codex を起動する。

    prompt はそのままコマンド文字列へ埋め込まず、Base64 で PowerShell 側へ渡して
    復元する。これで quoting 崩れや文字列連結ベースの注入リスクを抑える。
    
    Args:
        prompt: Task description for Codex
        workdir: Working directory
    
    Returns:
        {"success": bool, "output": str, "exit_code": int}
    """
    if not ENABLE_CODEX_TOOLS:
        return {
            "success": False,
            "output": "Codex tools are disabled by default.",
            "exit_code": -1,
        }

    try:
        import base64

        # 引数を PowerShell の文字列連結に直接入れないため、Base64 で渡す。
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
        
        # shell=False のまま新しいコンソールを開く。
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


def execute_tool(
    tool_name: str,
    args: dict,
    allow_privileged: bool = False,
    allowed_tool_names: Optional[set[str]] = None,
) -> str:
    """
    ツールを実行し、その結果を JSON 文字列で返す。

    通常のチャット経路では safe tools だけを公開し、privileged tools は
    明示的に許可された経路でしか使えないようにしている。
    """
    if allowed_tool_names is not None and tool_name not in allowed_tool_names:
        return json.dumps({"error": f"Tool not allowed in this context: {tool_name}"})

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
