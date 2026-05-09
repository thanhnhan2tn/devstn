#!/usr/bin/env python3
"""AI Dev Station — unified pipeline with 6-agent CrewAI-style chain.

HTTP server + CLI mode. Orchestrates:
  PM → Architect → Coder → Reviewer → Healer → Release Engineer

Uses projects.yaml for multi-repo config, Prometheus for metrics.

Usage:
  python pipeline.py serve --port 8001   # HTTP server (production)
  python pipeline.py --issue AI-123 ... # CLI one-shot mode
"""

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import uuid as uuid_mod
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Optional

import httpx
import yaml
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from prometheus_client import Counter, Histogram, Gauge, generate_latest

load_dotenv()


def slugify(text: str, max_len: int = 40) -> str:
    text = text.lower()
    text = re.sub(r'[^a-z0-9]+', '-', text)
    text = re.sub(r'^-|-$', '', text)
    return text[:max_len]


def _load_opencode_auth() -> dict:
    """Load credentials from opencode's auth file."""
    # Try multiple locations
    for path in [
        Path.home() / ".local/share/opencode/auth.json",
        Path("/root/.local/share/opencode/auth.json"),
        Path("/app/opencode_auth.json"),
    ]:
        try:
            if path.exists():
                import json
                with open(path) as f:
                    return json.load(f)
        except (PermissionError, OSError):
            # Path may exist but be unreadable (e.g. running as a non-root user
            # in CI). Fall through to the next candidate.
            continue
    return {}


OPENCODE_AUTH = _load_opencode_auth()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("pipeline")

# ── Prometheus Metrics ──────────────────────────────────────────────────
WEBHOOK_COUNTER = Counter("pipeline_webhooks_total", "Webhooks received", ["project", "phase", "status"])
AGENT_DURATION = Histogram("pipeline_agent_seconds", "Agent execution time", ["agent"])
REVIEW_VERDICT = Counter("pipeline_review_verdict_total", "Review verdicts", ["verdict"])
HEALER_RETRIES = Counter("pipeline_healer_retries_total", "Healer retry attempts")
LLM_CALLS = Counter("pipeline_llm_calls_total", "LLM calls by provider", ["provider"])
LLM_LATENCY = Histogram("pipeline_llm_latency_seconds", "LLM call latency", ["provider"])
PR_GAUGE = Gauge("pipeline_active_prs", "Active PRs by project", ["project"])
HEALTH_GAUGE = Gauge("pipeline_health", "Service health (1=up, 0=down)", ["service"])

HEALTH_GAUGE.labels(service="pipeline").set(1)

# ── Config ───────────────────────────────────────────────────────────────

NODE = os.environ.get("NODE_NAME", "studio")
GH_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GH_API = "https://api.github.com"
WORKSPACE = Path(os.environ.get("WORKSPACE_DIR", "/Volumes/work"))
PROJECTS_FILE = Path(os.environ.get("PROJECTS_CONFIG", "projects.yaml"))
TELEGRAM_HOST = os.environ.get("MINI_SERVER_IP") or os.environ.get("TAILSCALE_IP", "localhost")
TELEGRAM_URL = os.environ.get("TELEGRAM_BOT_TOKEN") and \
    f"http://{TELEGRAM_HOST}:7700/notify"

LIGHT_ROUTER_URL = os.environ.get("LIGHT_ROUTER_URL", "http://9router:20128")
USE_DIRECT_API = os.environ.get("USE_DIRECT_API", "true").lower() == "true"

LLM_CONFIG = {
    "studio-coder": {
        "url": os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434") + "/v1",
        "model": "qwen2.5-coder:32b-instruct-q8_0",
        "api_key": "ollama",
    },
    "deepseek": {
        "url": os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1") if USE_DIRECT_API else LIGHT_ROUTER_URL + "/v1",
        "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
        "api_key": os.environ.get("DEEPSEEK_API_KEY", "") or OPENCODE_AUTH.get("deepseek", {}).get("key", ""),
    },
    "nvidia": {
        "url": os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com") if USE_DIRECT_API else LIGHT_ROUTER_URL + "/v1",
        "model": os.environ.get("NVIDIA_MODEL", "meta/llama-3.1-70b-instruct"),
        "api_key": os.environ.get("NVIDIA_API_KEY", "") or OPENCODE_AUTH.get("nvidia", {}).get("key", ""),
    },
    "openai-codex": {
        "url": os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1") if USE_DIRECT_API else LIGHT_ROUTER_URL + "/v1",
        "model": os.environ.get("OPENAI_MODEL", "o3-mini"),
        "api_key": os.environ.get("OPENAI_API_KEY", ""),
    },
    "claude-premium": {
        "url": os.environ.get("CLAUDE_BASE_URL", "https://api.anthropic.com/v1") if USE_DIRECT_API else LIGHT_ROUTER_URL + "/v1",
        "model": os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-5"),
        "api_key": os.environ.get("CLAUDE_API_KEY", ""),
    },
    "minimax": {
        "url": os.environ.get("MINIMAX_BASE_URL", "https://api.minimax.chat/v1") if USE_DIRECT_API else LIGHT_ROUTER_URL + "/v1",
        "model": os.environ.get("MINIMAX_MODEL", "MiniMax-M2.7"),
        "api_key": os.environ.get("MINIMAX_API_KEY", "") or OPENCODE_AUTH.get("minimax-coding-plan", {}).get("key", ""),
    },
}

# How each tier routes agents to providers
TIER_MODEL = {
    "simple": {
        "pm": "studio-coder",
        "architect": "studio-coder",
        "coder": "studio-coder",
        "reviewer": "studio-coder",
        "healer": "studio-coder",
        "release": "studio-coder",
    },
    "medium": {
        "pm": "minimax",
        "architect": "minimax",
        "coder": "minimax",
        "reviewer": "minimax",
        "healer": "minimax",
        "release": "minimax",
    },
    "complex": {
        "pm": "minimax",
        "architect": "nvidia",
        "coder": "nvidia",
        "reviewer": "nvidia",
        "healer": "minimax",
        "release": "minimax",
    },
    "premium": {
        "pm": "deepseek",
        "architect": "nvidia",
        "coder": "nvidia",
        "reviewer": "nvidia",
        "healer": "deepseek",
        "release": "deepseek",
    },
}

FALLBACK_CHAIN = {
    "studio-coder": ("minimax", "deepseek"),
    "deepseek": ("minimax",),
    "minimax": ("deepseek",),
    "nvidia": ("minimax", "deepseek"),
    "openai-codex": ("minimax", "deepseek"),
    "claude-premium": ("minimax", "nvidia"),
}


def get_agent_provider(agent: str, tier: str = "medium") -> str:
    tier_map = TIER_MODEL.get(tier, TIER_MODEL["medium"])
    return tier_map.get(agent, "minimax")


def classify_complexity(title: str, description: str) -> str:
    risk_keywords = {"security", "performance", "architecture", "migration",
                     "redesign", "new service", "new system", "vulnerability", "fix critical"}
    text = (title + " " + description).lower()
    if any(k in text for k in risk_keywords):
        return "complex"
    prompt = f"""You are a task complexity classifier. Return JSON with:
- "tier": "simple" | "medium" | "complex" 
- "files": estimated number of files to change

Rules:
- simple: typo, config, 1 file -> tier=simple, files=1
- medium: feature, refactor, 2-7 files -> tier=medium, files=2-7
- complex: new system, 8+ files -> tier=complex, files=8+

Title: {title}
Description: {description}
JSON:"""
    try:
        result = _llm_call(prompt, provider="minimax")
        if not result or not result.strip():
            log.warning("Complexity classification returned empty, defaulting to medium")
            return "medium"
        data = json.loads(result)
        tier = data.get("tier", "medium")
        return tier if tier in ("simple", "medium", "complex") else "medium"
    except Exception as e:
        log.warning("Complexity classification failed: %s — defaulting to medium", e)
        return "medium"

# ── Task Queue ───────────────────────────────────────────────────────────
TASK_DIR = WORKSPACE / "tasks"
PENDING_DIR = TASK_DIR / "pending"
ACTIVE_DIR = TASK_DIR / "active"
COMPLETED_DIR = TASK_DIR / "completed"
FAILED_DIR = TASK_DIR / "failed"

def init_task_dirs():
    for d in [PENDING_DIR, ACTIVE_DIR, COMPLETED_DIR, FAILED_DIR]:
        d.mkdir(parents=True, exist_ok=True)

def enqueue_task(payload: dict) -> str:
    task_id = str(uuid_mod.uuid4())
    payload["id"] = task_id
    payload["created_at"] = datetime.now(timezone.utc).isoformat()
    tmp = PENDING_DIR / f".{task_id}.tmp"
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.rename(PENDING_DIR / f"{task_id}.json")
    return task_id

def claim_task(worker_id: str) -> Optional[dict]:
    for f in sorted(PENDING_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime):
        target = ACTIVE_DIR / f.name
        try:
            f.rename(target)
        except OSError:
            continue
        task = json.loads(target.read_text())
        task["worker_id"] = worker_id
        task["started_at"] = datetime.now(timezone.utc).isoformat()
        target.write_text(json.dumps(task, indent=2))
        return task
    return None

def complete_task(task_id: str, result: dict):
    src = ACTIVE_DIR / f"{task_id}.json"
    if not src.exists():
        return
    task = json.loads(src.read_text())
    task["result"] = result
    task["completed_at"] = datetime.now(timezone.utc).isoformat()
    (COMPLETED_DIR / f"{task_id}.json").write_text(json.dumps(task, indent=2))
    src.unlink()

def fail_task(task_id: str, error: str):
    src = ACTIVE_DIR / f"{task_id}.json"
    if not src.exists():
        return
    task = json.loads(src.read_text())
    task["error"] = error
    task["failed_at"] = datetime.now(timezone.utc).isoformat()
    (FAILED_DIR / f"{task_id}.json").write_text(json.dumps(task, indent=2))
    src.unlink()

def recover_stale_tasks():
    now = datetime.now(timezone.utc)
    for f in ACTIVE_DIR.glob("*.json"):
        task = json.loads(f.read_text())
        started = task.get("started_at", "")
        if started:
            age = now - datetime.fromisoformat(started)
            if age.total_seconds() > 300:
                f.rename(PENDING_DIR / f.name)
                log.warning("Re-queued stale task %s", f.stem)

def task_queue_depth() -> dict:
    return {
        "pending": len(list(PENDING_DIR.glob("*.json"))),
        "active": len(list(ACTIVE_DIR.glob("*.json"))),
        "completed": len(list(COMPLETED_DIR.glob("*.json"))),
        "failed": len(list(FAILED_DIR.glob("*.json"))),
    }

# In-memory state
projects_config = {}
active_tasks = []
pending_reviews = []
_tasks_lock = Lock()

# ── Projects Config ─────────────────────────────────────────────────────

def load_projects():
    global projects_config
    if not PROJECTS_FILE.exists():
        log.warning("projects.yaml not found at %s", PROJECTS_FILE)
        projects_config = {}
        return
    with open(PROJECTS_FILE) as f:
        data = yaml.safe_load(f)
    projects_config = {}
    for key, p in (data.get("projects") or {}).items():
        tid = os.path.expandvars(p.get("linear_team_id", ""))
        projects_config[tid] = {
            "name": p.get("name", key),
            "github_repo": os.path.expandvars(p.get("github_repo", "")),
            "base_branch": p.get("base_branch", "main"),
            "trigger_labels": p.get("trigger_labels", {}),
        }
    log.info("Loaded %d projects from %s", len(projects_config), PROJECTS_FILE)


def find_project(team_id: str) -> Optional[dict]:
    if team_id in projects_config:
        return projects_config[team_id]
    if "" in projects_config:
        return projects_config[""]
    for p in projects_config.values():
        return p
    return None


# ── LLM Client ──────────────────────────────────────────────────────────

OPENCODE_SERVER = os.environ.get("OPENCODE_SERVER", "http://devstation-studio-opencode-1:4096")
MINIMAX_API_KEY = os.environ.get("MINIMAX_API_KEY", "")


def _llm_via_opencode(prompt: str, model: str = "minimax-coding-plan/MiniMax-M2.7") -> str:
    """Use opencode container's opencode CLI."""
    import subprocess
    try:
        result = subprocess.run(
            ["docker", "exec", "devstation-studio-opencode-1", "opencode", "run", "--model", model, "--print-logs", prompt],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=WORKSPACE,
        )
        if result.returncode == 0:
            output = result.stdout.strip()
            lines = output.split("\n")
            for i, line in enumerate(lines):
                if line.startswith(">"):
                    if i + 1 < len(lines):
                        return lines[i + 1].strip()
            # If no ">" found, try to find JSON in output
            for line in lines:
                if line.strip().startswith("{") or line.strip().startswith("["):
                    return line.strip()
            return lines[-1].strip() if lines else ""
        raise RuntimeError(f"opencode failed: {result.stderr}")
    except subprocess.TimeoutExpired:
        raise RuntimeError("opencode timeout")
    except FileNotFoundError as e:
        # Fallback to direct API call
        raise RuntimeError(f"docker exec not available: {e}")

def _llm_direct_api(prompt: str, model: str = "MiniMax-M2.7") -> str:
    """Direct API call to LLM."""
    import requests
    MINIMAX_KEY = os.environ.get("MINIMAX_API_KEY", "")
    if not MINIMAX_KEY:
        raise RuntimeError("No MINIMAX_API_KEY")
    try:
        r = requests.post(
            "https://api.minimax.chat/v1/text/chatcompletion_v2",
            json={"model": model, "messages": [{"role": "user", "content": prompt}]},
            headers={"Authorization": f"Bearer {MINIMAX_KEY}"},
            timeout=300,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        raise RuntimeError(f"Direct API failed: {e}")


def _opencode_plan(issue_title: str, description: str, repo_path: Path) -> str:
    """Use opencode in plan mode to analyze and create a spec."""
    prompt = f"""You're an architect. Analyze this task and create a implementation plan:

## Issue: {issue_title}
{description}

## Repository: {repo_path}

1. First, explore the repository structure
2. Then create a SPEC.md with:
   - Problem statement
   - Proposed solution
   - Files to modify (list each file with purpose)
   - Acceptance criteria
   - Risk assessment

Output as JSON: {{"spec": "...", "files": [{{"path": "...", "change": "..."}}], "acceptance_criteria": ["..."], "risk": "..."}}"""
    
    try:
        return _llm_via_opencode(prompt, "minimax-coding-plan/MiniMax-M2.7")
    except Exception as e:
        raise RuntimeError(f"Plan failed: {e}")


def _opencode_implement(issue_title: str, description: str, repo_path: Path) -> dict:
    """Use opencode in build mode to implement the changes."""
    prompt = f"""You're a coder. Implement this task:

## Issue: {issue_title}
{description}

## Repository: {repo_path}

1. First explore the codebase to understand the structure
2. Make the required changes
3. Ensure tests pass
4. Output a summary of changes as JSON: {{"files": [...], "status": "ok"}}"""
    
    try:
        result = _llm_via_opencode(prompt, "minimax-coding-plan/MiniMax-M2.7")
        return {"files": [], "status": "ok", "result": result}
    except Exception as e:
        raise RuntimeError(f"Implementation failed: {e}")


def _llm_call(prompt: str, provider: str = "nvidia", json_mode: bool = False) -> str:
    # Map provider to opencode model - with fallback chain
    model_map = {
        "nvidia": "nvidia/meta/llama-3.1-70b-instruct",
        "minimax": "minimax-coding-plan/MiniMax-M2.7",
        "deepseek": "deepseek/deepseek-chat",
        "claude-premium": "github-copilot/claude-sonnet-4.5",
        "openai-codex": "github-copilot/gpt-4o",
        "studio-coder": "minimax-coding-plan/MiniMax-M2.5",
    }
    fallback_chain = {
        "nvidia": ["minimax", "deepseek", "claude-premium"],
        "minimax": ["nvidia", "deepseek"],
        "deepseek": ["nvidia", "minimax"],
        "claude-premium": ["nvidia", "minimax"],
        "openai-codex": ["nvidia", "minimax"],
        "studio-coder": ["nvidia", "minimax", "deepseek"],
    }
    
    model = model_map.get(provider, "nvidia/meta/llama-3.1-70b-instruct")
    
    # Try primary provider
    try:
        return _llm_via_opencode(prompt, model)
    except Exception as e:
        log.warning("%s failed: %s, trying fallbacks", provider, e)
        # Try fallback chain
        fallbacks = fallback_chain.get(provider, [])
        for fb in fallbacks:
            try:
                fb_model = model_map.get(fb)
                log.info("Trying fallback: %s", fb)
                return _llm_via_opencode(prompt, fb_model)
            except Exception as e2:
                log.warning("%s fallback failed: %s", fb, e2)
                continue
        raise RuntimeError("All LLM providers failed")


def llm_complete(prompt: str, provider: str = "studio-coder", json_mode: bool = False) -> str:
    return _llm_call(prompt, provider, json_mode)


# ── Git Operations ──────────────────────────────────────────────────────

def _scan_repo_structure(repo_path: Path, max_files: int = 30) -> str:
    """Scan repo and return a summary of file structure for the coder."""
    try:
        dirs = set()
        files = []
        for item in repo_path.rglob("*"):
            if item.is_file() and not any(x in str(item) for x in [".git", "node_modules", "__pycache__", ".venv"]):
                if item.suffix in [".py", ".js", ".ts", ".tsx", ".json", ".yaml", ".yml", ".md", ".toml", ".sh"]:
                    dirs.add(item.parent.relative_to(repo_path))
                    files.append(str(item.relative_to(repo_path)))
        
        lines = ["## Existing Directories", ""]
        for d in sorted(dirs):
            if d != Path("."):
                lines.append("- " + str(d) + "/")
        lines.extend(["## Files (first " + str(max_files) + ")", ""])
        
        for f in sorted(files)[:max_files]:
            lines.append("- " + f)
        
        if not files:
            return "## Repo Empty or No Code Files\n"
        return "\n".join(lines)
    except Exception as e:
        log.warning("Repo scan failed: %s", e)
        return ""


def ensure_repo(repo: str, task_dir: Optional[Path] = None) -> Path:
    # Sanitize repo: if it's an absolute path, extract just the last two components (user/repo)
    if repo.startswith("/"):
        parts = repo.strip("/").split("/")
        if len(parts) >= 2:
            repo = "/".join(parts[-2:])
    
    name = repo.split("/")[-1]
    path = (task_dir / name) if task_dir else (WORKSPACE / name)
    path.mkdir(parents=True, exist_ok=True)
    if not (path / ".git").exists():
        log.info("Cloning %s into %s (GH_TOKEN present: %s)", repo, path, bool(GH_TOKEN))
        # Ensure repo doesn't start with / for the URL
        clean_repo = repo.strip("/")
        result = subprocess.run(
            ["git", "clone", f"https://{GH_TOKEN}@github.com/{clean_repo}.git", str(path)],
            check=True, capture_output=True,
        )
        if result.returncode != 0:
            log.error("Clone failed: %s", result.stderr.decode())
    else:
        try:
            subprocess.run(
                ["git", "-C", str(path), "fetch", "origin"],
                check=True, capture_output=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            log.warning("Fetch timeout, removing and re-cloning...")
            shutil.rmtree(path)
            subprocess.run(
                ["git", "clone", f"https://{GH_TOKEN}@github.com/{repo}.git", str(path)],
                check=True, capture_output=True,
            )
    return path


def create_branch(repo: str, branch: str, base: str = "main", task_dir: Optional[Path] = None) -> Path:
    path = ensure_repo(repo, task_dir)
    subprocess.run(["git", "-C", str(path), "reset", "--hard", "HEAD"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "checkout", base], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "pull", "origin", base], check=True, capture_output=True)
    result = subprocess.run(
        ["git", "-C", str(path), "checkout", branch], capture_output=True
    )
    if result.returncode != 0:
        track = subprocess.run(
            ["git", "-C", str(path), "checkout", "--track", f"origin/{branch}"], capture_output=True
        )
        if track.returncode != 0:
            subprocess.run(["git", "-C", str(path), "checkout", "-b", branch], check=True, capture_output=True)
        else:
            subprocess.run(["git", "-C", str(path), "pull", "origin", branch], check=True, capture_output=True)
    return path


def commit_and_push(
    repo: str, branch: str, message: str, task_dir: Optional[Path] = None,
) -> str:
    """Stage everything, commit, push the branch, and return the new HEAD SHA."""
    path = ensure_repo(repo, task_dir)
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", message, "--allow-empty"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "push", "-u", "origin", branch],
        check=True, capture_output=True,
    )
    sha = subprocess.check_output(
        ["git", "-C", str(path), "rev-parse", "HEAD"], text=True,
    ).strip()
    log.info("Pushed %s/%s @ %s", repo, branch, sha[:8])
    return sha


def get_file_commit_sha(
    repo: str, branch: str, relpath: str, task_dir: Optional[Path] = None,
) -> Optional[str]:
    """Return the commit SHA on `branch` that last touched `relpath`, or None."""
    try:
        path = ensure_repo(repo, task_dir)
        sha = subprocess.check_output(
            ["git", "-C", str(path), "log", "-1", "--format=%H", branch, "--", relpath],
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
        return sha or None
    except Exception:
        return None


def build_plan_doc_url(repo: str, ref: str, issue_id: str) -> str:
    """Build a GitHub blob URL for `docs/plans/{issue_id}.md`.

    `ref` can be either a branch name or a commit SHA; pass a commit SHA for a
    permalink that survives branch deletion. `issue_id` is URL-encoded so
    UUID-style identifiers (e.g. `aa308dc1-...`) render correctly.
    """
    from urllib.parse import quote

    return (
        f"https://github.com/{repo}/blob/{quote(ref, safe='')}"
        f"/docs/plans/{quote(issue_id, safe='')}.md"
    )


def get_branch_diff(repo: str, branch: str, base: str = "main", task_dir: Optional[Path] = None) -> str:
    try:
        path = ensure_repo(repo, task_dir)
        return subprocess.check_output(
            ["git", "-C", str(path), "diff", f"{base}...{branch}"], text=True, stderr=subprocess.DEVNULL,
        )
    except Exception:
        return ""


def branch_exists(repo: str, branch: str, task_dir: Optional[Path] = None) -> bool:
    path = ensure_repo(repo, task_dir)
    r = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--verify", branch],
        capture_output=True,
    )
    return r.returncode == 0


# ── PR Operations ───────────────────────────────────────────────────────

def find_existing_pr(repo: str, branch: str) -> Optional[str]:
    headers = {"Authorization": f"Bearer {GH_TOKEN}", "Accept": "application/vnd.github+json"}
    r = httpx.get(
        f"{GH_API}/repos/{repo}/pulls?head={repo.split('/')[0]}:{branch}&state=open",
        headers=headers, timeout=15,
    )
    if r.status_code == 200 and r.json():
        return r.json()[0]["html_url"]
    return None


def open_pr(repo: str, branch: str, title: str, body: str, draft: bool = False) -> str:
    existing = find_existing_pr(repo, branch)
    if existing:
        log.info("PR already exists: %s", existing)
        return existing
    payload = {
        "title": title,
        "head": branch,
        "base": "main",
        "body": body,
        "draft": draft,
    }
    headers = {"Authorization": f"Bearer {GH_TOKEN}", "Accept": "application/vnd.github+json"}
    r = httpx.post(f"{GH_API}/repos/{repo}/pulls", json=payload, headers=headers, timeout=30)
    r.raise_for_status()
    pr_url = r.json()["html_url"]
    log.info("PR opened: %s", pr_url)
    PR_GAUGE.labels(project=repo.split("/")[-1]).inc()
    return pr_url


def update_pr_body(repo: str, branch: str, body: str):
    headers = {"Authorization": f"Bearer {GH_TOKEN}", "Accept": "application/vnd.github+json"}
    r = httpx.get(
        f"{GH_API}/repos/{repo}/pulls?head={repo.split('/')[0]}:{branch}&state=open",
        headers=headers, timeout=15,
    )
    if r.status_code != 200 or not r.json():
        return
    pr_number = r.json()[0]["number"]
    httpx.patch(
        f"{GH_API}/repos/{repo}/pulls/{pr_number}",
        json={"body": body},
        headers=headers, timeout=15,
    )


def comment_on_pr(repo: str, branch: str, comment: str):
    headers = {"Authorization": f"Bearer {GH_TOKEN}", "Accept": "application/vnd.github+json"}
    r = httpx.get(
        f"{GH_API}/repos/{repo}/pulls?head={repo.split('/')[0]}:{branch}&state=open",
        headers=headers, timeout=15,
    )
    if r.status_code != 200 or not r.json():
        return
    pr_number = r.json()[0]["number"]
    httpx.post(
        f"{GH_API}/repos/{repo}/issues/{pr_number}/comments",
        json={"body": comment},
        headers=headers, timeout=15,
    )


# ── Telegram ─────────────────────────────────────────────────────────────

def notify(message: str):
    if not TELEGRAM_URL:
        log.info("[notify] %s", message)
        return
    try:
        httpx.post(TELEGRAM_URL, json={"message": message}, timeout=5)
    except Exception as e:
        log.warning("telegram notify failed: %s", e)


# ── 6-Agent Chain ───────────────────────────────────────────────────────

RULES_FILE = Path(__file__).parent / "AGENTS_RULES.md"


def _load_rules() -> str:
    try:
        return RULES_FILE.read_text(encoding="utf-8")[:8000]
    except Exception:
        return ""


AGENT_PROMPTS = {
    "pm": """You are an Engineering PM. Convert this task into a precise spec.

RULES:
- Be concise. Focus on acceptance criteria and file changes.
- Output valid JSON: {{"spec": "...", "acceptance_criteria": ["..."], "files": [{"path": "..."}], "risk": "low|medium|high"}}

Task: {{issue_title}}
Description: {{issue_body}}
Output:""",

    "architect": """You are a Staff Architect. Plan the implementation.

RULES:
- Keep plan minimal and safe  
- Use EXISTING directories when possible - DO NOT create new folders without strong justification
- List existing files first: run `find . -type f | head -30`
- Output valid JSON: {{"file_plan": [{"path": "...", "change": "modify|create|delete", "summary": "..."}], "invariants": ["..."], "test_plan": ["..."]}}

Spec: {{spec}}
Task ID: {{issue_id}}
Output:""",

    "coder": """You are a Senior Software Engineer. Implement the changes.

RULES:- List files in {{repo_path}} first to understand structure: run `find . -type f | head -50`
- ALWAYS check existing files before creating new ones - DO NOT create duplicates- Follow existing code STYLE and DIRECTORY STRUCTURE from repo- Write COMPLETE working code (no stubs)
- Keep changes minimal and focused
- DO NOT create new folders unless absolutely necessary
- If new folder needed, explain why in summary
- Output valid JSON: {{"files": [{"path": "...", "content": "..."}], "commands": ["..."], "summary": "..."}}

Repo: {{repo_path}}
Plan: {{plan}}
Task ID: {{issue_id}}
Output:""",

    "reviewer": """You are a Senior Code Reviewer. Review this diff.

RULES:
- Check: correctness, security, performance, style, edge cases
- Output valid JSON: {{"overall": "approve|changes_requested|blocked", "issues": [{"severity": "critical|major|minor", "file": "...", "message": "..."}], "summary": "..."}}

Diff:
{{diff}}
Output:""",

    "healer": """You are a Healer Agent. Fix the review issues.

RULES:
- Fix ONLY the issues listed. Do not change anything else.
- Output valid JSON: {{"files": [{"path": "...", "content": "..."}], "summary": "..."}}

Issues to fix:
{{issues}}

Original Diff:
{{diff}}
Output:""",
}


def _load_template(agent: str, **kwargs) -> str:
    base_prompt = AGENT_PROMPTS.get(agent, "Task: {{issue_title}}\nDescription: {{issue_body}}")
    for key, val in kwargs.items():
        base_prompt = base_prompt.replace("{{" + key + "}}", str(val))
    return base_prompt


def _safe_str(value) -> str:
    if isinstance(value, str):
        return value
    try:
        return str(value)
    except Exception:
        return ""


def _safe_json_parse(text: str) -> dict:
    import re
    raw = None
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r'\{.*\}', text, re.DOTALL)
        if m:
            try:
                raw = json.loads(m.group())
            except json.JSONDecodeError:
                pass
    if isinstance(raw, dict):
        return raw
    return {"spec": _safe_str(text), "acceptance_criteria": [], "files": [], "risk": "medium"}


def _validate_dict(result: dict, required: list) -> dict:
    out = {}
    for key in required:
        val = result.get(key)
        if val is None:
            if key in ("files", "file_plan", "issues", "acceptance_criteria", "invariants", "test_plan", "commands"):
                val = []
            elif key in ("risk", "overall"):
                val = "unknown"
            else:
                val = ""
        out[key] = val
    return out


def agent_pm(issue_title: str, issue_body: str, provider: str = "studio-coder") -> dict:
    prompt = _load_template("pm", issue_title=issue_title, issue_body=issue_body)
    result = llm_complete(prompt, provider=provider, json_mode=True)
    parsed = _safe_json_parse(result)
    return _validate_dict(parsed, ["spec", "acceptance_criteria", "files", "risk"])


def agent_architect(spec: str, issue_id: str, provider: str = "studio-coder") -> dict:
    prompt = _load_template("architect", issue_id=issue_id, spec=spec)
    result = llm_complete(prompt, provider=provider, json_mode=True)
    parsed = _safe_json_parse(result)
    return _validate_dict(parsed, ["file_plan", "invariants", "test_plan", "implementation_notes"])


def agent_coder(plan: str, repo_path: str, issue_id: str, provider: str = "deepseek") -> dict:
    prompt = _load_template("coder", issue_id=issue_id, repo_path=repo_path, plan=plan)
    result = llm_complete(prompt, provider=provider, json_mode=True)
    parsed = _safe_json_parse(result)
    return _validate_dict(parsed, ["files", "commands", "summary"])


def agent_reviewer(diff: str, provider: str = "studio-coder") -> dict:
    prompt = _load_template("reviewer", diff=diff)
    result = llm_complete(prompt, provider=provider, json_mode=True)
    parsed = _safe_json_parse(result)
    return _validate_dict(parsed, ["overall", "issues", "summary"])


def agent_healer(diff: str, issues: list, provider: str = "deepseek") -> dict:
    issues_text = json.dumps(issues, indent=2) if isinstance(issues, list) else str(issues)
    prompt = _load_template("healer", diff=diff, issues=issues_text)
    result = llm_complete(prompt, provider=provider, json_mode=True)
    parsed = _safe_json_parse(result)
    return _validate_dict(parsed, ["files", "summary"])


def agent_release(repo: str, branch: str, title: str, pr_body: str, draft: bool = False) -> str:
    return open_pr(repo, branch, title, pr_body, draft=draft)


# ── Phase Execution ──────────────────────────────────────────────────────

def add_task(task: dict):
    with _tasks_lock:
        active_tasks.append(task)


def remove_task(issue_id: str):
    with _tasks_lock:
        active_tasks[:] = [t for t in active_tasks if t["issue_id"] != issue_id]


def update_task(issue_id: str, updates: dict):
    with _tasks_lock:
        for t in active_tasks:
            if t["issue_id"] == issue_id:
                t.update(updates)


def resolve_tier(labels: list) -> str:
    if "premium" in labels or "claude" in labels:
        return "premium"
    return ""


def run_phase_plan(project: dict, issue_id: str, title: str, description: str, labels: list = None, repo: str = "", task_dir: Optional[Path] = None, repo_path: Optional[Path] = None) -> dict:
    project_repo = os.path.expandvars(project.get("github_repo", ""))
    repo = os.path.expandvars(repo) if repo else ""
    if not repo or repo.startswith("$"):
        repo = project_repo
    log.info("run_phase_plan: repo=%s, project_repo=%s, task_dir=%s", repo, project_repo, task_dir)
    title_slug = slugify(title, max_len=30)
    branch = f"feat/{issue_id}-{title_slug}"
    base = project.get("base_branch", "main")

    add_task({"issue_id": issue_id, "title": title, "phase": "plan", "status": "running"})
    notify("🔄 *" + issue_id + "*: Planning — " + str(title))

    try:
        tier = resolve_tier(labels or [])
        if not tier:
            tier = classify_complexity(title, description)
            log.info("[Complexity Gate] Tier: %s", tier)

        notify("📋 *" + issue_id + "*: PM generating spec...")
        pm_provider = get_agent_provider("pm", tier)

        log.info("[PM] Spec generation (provider=%s)...", pm_provider)
        with AGENT_DURATION.labels(agent="pm").time():
            spec = agent_pm(title, description, provider=pm_provider)

        notify("✅ *" + issue_id + "*: Spec generated — " + str(title)[:50])

        files_raw = spec.get('files', [])
        if not isinstance(files_raw, list):
            files_raw = []
        files_md = chr(10).join(
            '- ' + (f.get('path', str(f)) if isinstance(f, dict) else str(f)) +
            ': ' + (f.get('change', '') if isinstance(f, dict) else '')
            for f in files_raw
        )
        ac_raw = spec.get('acceptance_criteria', [])
        if not isinstance(ac_raw, list):
            ac_raw = []
        ac_md = chr(10).join('- ' + c for c in ac_raw)
        plan_md = (
            "# Implementation Plan: " + title + "\n\n"
            "## Spec\n" + spec.get('spec', '') + "\n\n"
            "## Acceptance Criteria\n" + ac_md + "\n\n"
            "## Files to Change\n" + files_md + "\n\n"
            "## Risk\n" + spec.get('risk', 'unknown') + "\n\n"
            "---\nGenerated by AI Dev Station (" + NODE + ")\n"
        )

        create_branch(repo, branch, base, task_dir)
        repo_path = ensure_repo(repo, task_dir)

        plan_dir = repo_path / "docs" / "plans"
        plan_dir.mkdir(parents=True, exist_ok=True)
        plan_path = plan_dir / f"{issue_id}.md"
        plan_path.write_text(plan_md)
        plan_sha = commit_and_push(
            repo, branch, "docs: add plan for " + issue_id + " — " + title, task_dir,
        )
        plan_url = build_plan_doc_url(repo, plan_sha or branch, issue_id)

        pr_body = (
            "## " + title + "\n\n"
            + description + "\n\n"
            + "### Plan\n" + spec.get('spec', '') + "\n\n"
            + "### Files\n" + files_md + "\n\n"
            + "### Risk\n" + spec.get('risk', 'unknown') + "\n\n"
            + "### Plan doc\n" + plan_url + "\n\n"
            + "---\nGenerated by AI Dev Station (" + NODE + ")\n"
        )
        pr_url = agent_release(repo, branch, title, pr_body, draft=True)

        update_task(
            issue_id,
            {"status": "completed", "pr_url": pr_url, "plan_url": plan_url},
        )
        notify("✅ *" + issue_id + "*: Plan PR created — " + str(pr_url))
        WEBHOOK_COUNTER.labels(project=project.get("name", "?"), phase="plan", status="success").inc()

        return {
            "status": "ok",
            "phase": "plan",
            "pr_url": pr_url,
            "plan_url": plan_url,
            "spec": spec,
        }

    except Exception as e:
        log.exception("Plan phase failed")
        remove_task(issue_id)
        notify("❌ *" + issue_id + "*: Plan failed — " + str(e))
        WEBHOOK_COUNTER.labels(project=project.get("name", "?"), phase="plan", status="failed").inc()
        return {"status": "error", "phase": "plan", "error": str(e)}


def run_phase_implement(project: dict, issue_id: str, title: str, description: str, labels: list = None, repo: str = "", task_dir: Optional[Path] = None) -> dict:
    repo = repo or project["github_repo"]
    title_slug = slugify(title, max_len=30)
    branch = f"feat/{issue_id}-{title_slug}"
    base = project.get("base_branch", "main")

    add_task({"issue_id": issue_id, "title": title, "phase": "implement", "status": "running"})

    notify("🔄 *" + issue_id + "*: Implementing — " + str(title))

    try:
        tier = resolve_tier(labels or [])
        if not tier:
            tier = classify_complexity(title, description)
            log.info("[Complexity Gate] Tier: %s", tier)

        arch_provider = get_agent_provider("architect", tier)
        coder_provider = get_agent_provider("coder", tier)
        reviewer_provider = get_agent_provider("reviewer", tier)
        healer_provider = get_agent_provider("healer", tier)

        repo_path = ensure_repo(repo, task_dir)

        plan_text = ""
        plan_url: Optional[str] = None
        plan_relpath = f"docs/plans/{issue_id}.md"
        plan_file = repo_path / "docs" / "plans" / f"{issue_id}.md"
        
        # Check if description contains a Devin plan or markers
        if "## Files to Change" in description or "Generated by Devin" in description:
            log.info("Detected Devin plan in description/comments")
            plan_text = description
        
        if plan_file.exists():
            plan_text = plan_file.read_text()
            log.info("Found existing plan docs/plans/%s.md", issue_id)
            plan_sha = get_file_commit_sha(repo, branch, plan_relpath, task_dir)
            plan_url = build_plan_doc_url(repo, plan_sha or branch, issue_id)

        if not plan_text:
            plan_text = "Title: " + str(title) + "\nDescription: " + str(description) + "\n"
        elif plan_url:
            plan_text = "Plan doc: " + plan_url + "\n\n" + plan_text

        log.info("[Agent Chain] Running tier=%s", tier)

        notify("🔄 *" + issue_id + "*: Implementing — " + str(title) + " (tier=" + tier + ")")

        if tier != "simple":
            log.info("[Architect] Technical planning (provider=%s)...", arch_provider)
            notify("📐 *" + issue_id + "*: Architect planning...")
            with AGENT_DURATION.labels(agent="architect").time():
                arch_plan = agent_architect(plan_text, issue_id, provider=arch_provider)
        else:
            arch_plan = {"file_plan": [], "invariants": [], "test_plan": []}
            log.info("[Architect] Skipped (simple tier)")

        log.info("[Coder] Scanning repo structure...")
        notify("📂 *" + issue_id + "*: Scanning repo...")
        file_listing = _scan_repo_structure(repo_path, max_files=30)
        
        # Show existing structure to help coder
        existing_count = len([f for f in repo_path.rglob("*") if f.is_file()])
        notify("📁 *" + issue_id + "*: Found " + str(existing_count) + " files in repo")
        
        full_plan = json.dumps(arch_plan) + "\n\n## Repo Structure\n" + file_listing

        log.info("[Coder] Code generation (provider=%s)...", coder_provider)
        notify("💻 *" + issue_id + "*: Generating code...")
        with AGENT_DURATION.labels(agent="coder").time():
            code_result = agent_coder(full_plan, str(repo_path), issue_id, provider=coder_provider)

        code_files = code_result.get("files", [])
        if not isinstance(code_files, list):
            code_files = []
        
        # Scan existing directories first
        existing_dirs = set()
        for item in repo_path.rglob("*"):
            if item.is_dir():
                existing_dirs.add(item.relative_to(repo_path))
        
        for f in code_files:
            if not isinstance(f, dict):
                continue
            file_path = str(f.get("path", ""))
            path = repo_path / file_path
            parent = path.parent
            
            # Warn if creating in non-existent directory
            rel_parent = parent.relative_to(repo_path)
            if rel_parent != repo_path and rel_parent not in existing_dirs:
                log.warning("Creating file in new directory: %s", rel_parent)
                notify("⚠️ *" + issue_id + "*: New directory: " + str(rel_parent))
            
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(f.get("content", "")))
            log.info("  Wrote %s", f.get("path", "?"))

        notify("📝 *" + issue_id + "*: " + str(len(code_files)) + " files written")

        code_cmds = code_result.get("commands", [])
        if not isinstance(code_cmds, list):
            code_cmds = []
        for cmd in code_cmds:
            log.info("  Running: %s", cmd)
            subprocess.run(cmd, shell=True, cwd=str(repo_path), capture_output=True)

        if tier == "simple":
            review = {"overall": "approve", "issues": [], "summary": "Auto-approved (simple tier)"}
            log.info("[Reviewer] Skipped (simple tier)")
        else:
            log.info("[Reviewer] Code review (provider=%s)...", reviewer_provider)
            notify("🔍 *" + issue_id + "*: Reviewer checking code...")
            diff = get_branch_diff(repo, branch, base, task_dir)
            with AGENT_DURATION.labels(agent="reviewer").time():
                review = agent_reviewer(diff, provider=reviewer_provider)

        REVIEW_VERDICT.labels(verdict=review.get("overall", "unknown")).inc()

        # Only proceed if review passes
        if review.get("overall") != "approve":
            log.warning("Review %s - not committing or creating PR", review.get("overall"))
            notify("⛔ *" + issue_id + "*: Review " + review.get("overall") + " — code NOT committed")
            return {"status": "error", "phase": "implement", "verdict": review.get("overall"), "issues": review.get("issues", [])}

        healer_attempts = 0
        max_healer = 3 if tier == "complex" else 0

        # Healer loop for changes_requested
        if review.get("overall") == "changes_requested":
            log.info("Changes requested — Healer agent starting...")
            while healer_attempts < max_healer:
                HEALER_RETRIES.inc()
                healer_attempts += 1
                notify("🔧 *" + issue_id + "*: Healer attempt " + str(healer_attempts) + "/" + str(max_healer))
                with AGENT_DURATION.labels(agent="healer").time():
                    healer_result = agent_healer(diff, review.get("issues", []), provider=healer_provider)

                healer_files = healer_result.get("files", [])
                if not isinstance(healer_files, list):
                    healer_files = []
                for f in healer_files:
                    if not isinstance(f, dict):
                        continue
                    path = repo_path / str(f.get("path", ""))
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(str(f.get("content", "")))

                diff = get_branch_diff(repo, branch, base, task_dir)
                with AGENT_DURATION.labels(agent="reviewer").time():
                    review = agent_reviewer(diff, provider=reviewer_provider)
                REVIEW_VERDICT.labels(verdict=review.get("overall", "unknown")).inc()

                if review.get("overall") == "approve":
                    log.info("Healer fixed all issues!")
                    notify("✅ *" + issue_id + "*: Healer fixed all issues after " + str(healer_attempts) + " attempt(s)")
                    break
            else:
                log.warning("Healer exhausted %d attempts", max_healer)
                update_task(issue_id, {"status": "needs_review", "verdict": "changes_requested"})
                pending_reviews.append({
                    "id": issue_id,
                    "issue_id": issue_id,
                    "verdict": "changes_requested",
                    "summary": review.get("summary", ""),
                    "issues": review.get("issues", []),
                })
                notify("👀 *" + issue_id + "*: Needs human review — Healer exhausted\n" + _safe_str(review.get('summary', ''))[:200])

        # Only commit and create PR after approval
        commit_and_push(repo, branch, "feat: implement " + issue_id + " — " + str(title), task_dir)

        plan_doc_md = "### Plan doc\n" + plan_url + "\n\n" if plan_url else ""

        pr_url = find_existing_pr(repo, branch)
        if not pr_url:
            code_files = code_result.get('files', [])
            if not isinstance(code_files, list):
                code_files = []
            code_files_md = chr(10).join(
                '- ' + (f.get('path', str(f)) if isinstance(f, dict) else str(f))
                for f in code_files
            )
            pr_body = (
                "## " + title + "\n\n"
                + description + "\n\n"
                + plan_doc_md
                + "### Implementation\n" + code_result.get('summary', '') + "\n"
                + code_files_md + "\n\n"
                + "### Review\n" + review.get('summary', '') + "\n\n"
                + "---\nGenerated by AI Dev Station (" + NODE + ")\n"
            )
            pr_url = agent_release(repo, branch, title, pr_body)
        else:
            update_pr_body(
                repo, branch,
                "## " + title + "\n\n" + description + "\n\n"
                + plan_doc_md
                + "### Review\n" + review.get('summary', ''),
            )

        update_task(issue_id, {
            "status": "completed",
            "pr_url": pr_url,
            "verdict": review.get("overall"),
            "plan_url": plan_url,
        })
        notify("💻 *" + issue_id + "*: Implementation pushed — " + _safe_str(pr_url))
        WEBHOOK_COUNTER.labels(project=project.get("name", "?"), phase="implement", status="success").inc()

        return {
            "status": "ok",
            "phase": "implement",
            "pr_url": pr_url,
            "plan_url": plan_url,
            "verdict": review.get("overall"),
            "issues": review.get("issues", []),
        }

    except Exception as e:
        log.exception("Implement phase failed")
        remove_task(issue_id)
        notify("❌ *" + issue_id + "*: Implementation failed — " + str(e))
        WEBHOOK_COUNTER.labels(project=project.get("name", "?"), phase="implement", status="failed").inc()
        return {"status": "error", "phase": "implement", "error": str(e)}


# ── FastAPI Server ────────────────────────────────────────────────────

IS_DISPATCHER = False
app = FastAPI(title="AI Dev Station Pipeline")


class WebhookPayload(BaseModel):
    issue_id: str
    issue_uuid: str = ""
    title: str
    description: str = ""
    phase: str = "plan"
    repo: str = ""
    team_id: str = ""
    labels: list = []


class ReviewAction(BaseModel):
    action: str


@app.on_event("startup")
async def startup():
    load_projects()


@app.post("/webhook")
async def webhook(p: WebhookPayload):
    log.info("Webhook: %s phase=%s labels=%s", p.issue_id, p.phase, p.labels)

    project = find_project(p.team_id)
    if not project:
        raise HTTPException(404, f"No project found for team_id={p.team_id}")

    phase = p.phase
    if p.labels:
        trigger = project.get("trigger_labels", {})
        if trigger.get("implement") in p.labels or "implement" in p.labels:
            phase = "implement"
        elif trigger.get("plan") in p.labels or "plan" in p.labels:
            phase = "plan"

    if IS_DISPATCHER:
        task_id = enqueue_task({
            "issue_id": p.issue_id,
            "title": p.title,
            "description": p.description,
            "phase": phase,
            "repo": p.repo or project["github_repo"],
            "team_id": p.team_id,
            "labels": p.labels or [],
        })
        return {"status": "queued", "task_id": task_id, "phase": phase}

    if phase == "plan":
        result = run_phase_plan(project, p.issue_id, p.title, p.description, labels=p.labels, repo=p.repo)
    elif phase == "implement":
        result = run_phase_implement(project, p.issue_id, p.title, p.description, labels=p.labels, repo=p.repo)
    else:
        return {"status": "ignored", "phase": phase}

    return result


@app.get("/healthz")
async def healthz():
    with _tasks_lock:
        tasks = list(active_tasks)
    queue = task_queue_depth() if IS_DISPATCHER else {}
    return {
        "status": "ok",
        "node": NODE,
        "mode": "dispatcher" if IS_DISPATCHER else "server",
        "tasks": tasks,
        "pending_reviews": len(pending_reviews),
        "projects": list(projects_config.keys()),
        **({"queue": queue} if queue else {}),
    }


@app.get("/metrics")
async def metrics():
    return generate_latest()


@app.get("/pending-reviews")
async def get_pending_reviews():
    return pending_reviews


@app.post("/review/{review_id}/{action}")
async def handle_review(review_id: str, action: str):
    global pending_reviews
    if action not in ("approve", "reject"):
        raise HTTPException(400, "action must be approve or reject")
    pending_reviews = [r for r in pending_reviews if r["id"] != review_id]
    notify(f"👤 *{review_id}*: Human {action}d the review")
    with _tasks_lock:
        for t in active_tasks:
            if t.get("issue_id") == review_id and t.get("status") == "needs_review":
                t["status"] = f"human_{action}d"
    return {"status": "ok", "action": action}


@app.post("/projects/reload")
async def reload_projects():
    load_projects()
    return {"status": "ok", "projects": len(projects_config)}


@app.post("/notify")
async def telegram_notify(payload: dict):
    message = payload.get("message", "")
    notify(message)
    return {"status": "ok"}


# ── CLI Mode ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="AI Dev Station Pipeline")
    parser.add_argument("--serve", action="store_true", help="Run HTTP server (legacy)")
    parser.add_argument("--dispatcher", action="store_true", help="Run as dispatcher (queues tasks)")
    parser.add_argument("--worker", action="store_true", help="Run as worker (processes tasks)")
    parser.add_argument("--port", type=int, default=8001, help="HTTP server port")
    parser.add_argument("--host", default="0.0.0.0", help="HTTP server host")
    parser.add_argument("--issue", help="Linear issue ID")
    parser.add_argument("--repo", help="GitHub repo (user/repo)")
    parser.add_argument("--title", help="PR title")
    parser.add_argument("--body", default="", help="PR body")
    parser.add_argument("--phase", choices=["plan", "implement", "full"], default="full")
    parser.add_argument("--team-id", default="", help="Linear team ID")

    args = parser.parse_args()

    if args.dispatcher:
        global IS_DISPATCHER
        IS_DISPATCHER = True
        init_task_dirs()
        load_projects()
        log.info("Starting dispatcher on %s:%d", args.host, args.port)
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
        return

    if args.worker:
        worker_id = f"worker-{os.uname().nodename}-{os.getpid()}"
        log.info("Starting worker: %s", worker_id)
        init_task_dirs()
        load_projects()
        recover_stale_tasks()
        while True:
            task = claim_task(worker_id)
            if not task:
                time.sleep(2)
                continue
            task_dir = WORKSPACE / task["id"]
            task_dir.mkdir(parents=True, exist_ok=True)
            try:
                project = find_project(task.get("team_id", ""))
                if not project:
                    project = {
                        "github_repo": task["repo"],
                        "base_branch": "main",
                        "name": task["repo"].split("/")[-1],
                    }
                if task["phase"] == "plan":
                    result = run_phase_plan(
                        project, task["issue_id"], task["title"],
                        task.get("description", ""), labels=task.get("labels", []),
                        repo=task["repo"], task_dir=task_dir,
                    )
                elif task["phase"] == "implement":
                    result = run_phase_implement(
                        project, task["issue_id"], task["title"],
                        task.get("description", ""), labels=task.get("labels", []),
                        repo=task["repo"], task_dir=task_dir,
                    )
                else:
                    result = {"status": "ignored"}
                complete_task(task["id"], result)
                log.info("Task %s completed: %s", task["id"], result.get("status"))
            except Exception as e:
                log.exception("Task %s failed", task["id"])
                fail_task(task["id"], str(e))
            finally:
                shutil.rmtree(task_dir, ignore_errors=True)
        return

    if args.serve:
        log.info("Starting HTTP server on %s:%d", args.host, args.port)
        load_projects()
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
        return

    if args.issue:
        load_projects()
        project = find_project(args.team_id)
        if not project:
            project = {"github_repo": args.repo, "base_branch": "main", "name": "cli"}
        if args.phase == "plan" or args.phase == "full":
            result = run_phase_plan(project, args.issue, args.title, args.body)
            print(f"Plan result: {json.dumps(result, indent=2)}")
        if args.phase == "implement" or args.phase == "full":
            result = run_phase_implement(project, args.issue, args.title, args.body)
            print(f"Implement result: {json.dumps(result, indent=2)}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
