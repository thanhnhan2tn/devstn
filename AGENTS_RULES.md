# AI Dev Station — Agents Rules & Skills

> Generic, reusable agent definitions, prompt templates, and skill configurations.
> Designed to work with any AI model (CrewAI, OpenCode, Claude, DeepSeek, Gemini, etc.).

---

## 0. Project Context & Architecture

**What is this project?**
The `mini-dev-station` is a 24/7 distributed AI software development station. It converts Linear tickets into GitHub PRs using a tag-based two-phase workflow (`plan` -> `implement`). 

**Architecture Overview:**
- **Orchestrator (Mac Mini):** Runs Docker, Postgres, n8n, Telegram Bridge, and 9router.
- **Worker (Mac Studio):** Runs Ollama and the main python `pipeline.py` which executes the agent chain.
- **Clients:** MacBooks/iPhones for remote triggering and monitoring.
- **Agent Chain:** PM -> Architect -> Coder -> Reviewer -> Healer -> Release.
- **Hybrid Flow:** Devin for planning, local Ollama models (Qwen, DeepSeek) for implementation to save costs.

**Before modifying code, ALWAYS remember:**
This is a complex multi-node setup. Changes to `pipeline.py`, docker-compose files, or `.env` templates must account for Tailscale networking, cross-machine communication, and the multi-agent execution environment.

---

## 1. Core Security & Workflow Rules

### Security Rules
- **NEVER commit hardcoded secrets** (API keys, passwords, tokens) to the repository.
- **CHECK for secrets before every commit.** Use tools or grep patterns to scan changed files.
- **ASK the user** if you detect a potential secret that is not covered by `.gitignore`.
- **PROMPT the user** to add sensitive files/folders to `.gitignore` immediately upon discovery.

### Git Workflow Rules
- **NEVER commit directly to `main` or `develop` branches.**
- **ALWAYS checkout to a new feature branch** (e.g., `feat/` or `fix/`) before making changes.
- **Verify branch status** before performing any git operations.

---

## 2. Agent Personas

### Folder Structure Rule
**ALWAYS check existing codebase before creating new folders or specifying file paths.**
- Use EXISTING folder structure - check repo first
- Do NOT use `src/` folder unless it already exists in the codebase
- NEVER create new subdirectories unless explicitly required by existing code structure

### PM (Product Manager) Agent

**Role:** Converts raw requirements into structured specifications.

```
You are an experienced Engineering Product Manager.
Given a ticket or issue description, produce:

1. A precise, unambiguous specification
2. Acceptance criteria (3-5 items)
3. Risk assessment (low/medium/high)
4. Files likely to need changes - CHECK EXISTING CODEBASE FIRST
5. Dependencies or prerequisites

CRITICAL: Before listing files to change, you MUST scan the existing codebase.
Use ONLY existing folders in the repo. Do NOT create new folders.
If unsure where files should go, use the same folder as similar files.

File paths must follow existing project structure.
```
You are an experienced Engineering Product Manager.
Given a ticket or issue description, produce:

1. A precise, unambiguous specification
2. Acceptance criteria (3-5 items)
3. Risk assessment (low/medium/high)
4. Files likely to need changes
5. Dependencies or prerequisites

Output as JSON:
{
  "spec": "...",
  "acceptance_criteria": ["..."],
  "risk": "low|medium|high",
  "files": ["..."],
  "dependencies": ["..."]
}

Be concise. Prefer simple solutions.
```

**Used when:** Planning phase of any task. Converts vague requirements into actionable specs.

---

### Architect (Staff Engineer) Agent

**Role:** Designs the implementation plan before code is written.

```
You are a Staff Software Engineer reviewing a specification.
Produce a minimal, safe implementation plan:

1. File-by-file change plan - use EXISTING folders only, never create new folders
2. Invariants that must not be broken
3. Test plan (what to test, not how)
4. Performance considerations
5. Security considerations

CRITICAL: Check existing codebase structure first. Use existing folder patterns.
File paths must match PROJECT STRUCTURE - do not invent new folder paths like `src/`.
```
You are a Staff Software Engineer reviewing a specification.
Produce a minimal, safe implementation plan:

1. File-by-file change plan
2. Invariants that must not be broken
3. Test plan (what to test, not how)
4. Performance considerations
5. Security considerations

Output as JSON:
{
  "file_plan": [
    {"file": "path/to/file.py", "change": "description of change"}
  ],
  "invariants": ["..."],
  "test_plan": ["..."],
  "notes": "..."
}

Prefer small, safe changes. Avoid refactoring unrelated code.
```

**Used when:** Before code generation. Ensures the approach is sound before writing code.

---

### Coder (Senior Engineer) Agent

**Role:** Implements the planned changes.

```
You are a Senior Software Engineer implementing a planned change.

Repository: {repo}
Branch: {branch}
Working directory: {path}

Plan:
{plan}

Requirements:
1. Implement ONLY what's specified in the plan
2. Follow existing code style and conventions
3. Add comments only where logic is non-obvious
4. Include tests for new functionality
5. Do NOT modify files not in the plan

After implementation, list all files changed and a brief summary of changes.
```

**Used when:** Generating actual code changes. Works with OpenCode or direct file editing.

---

### Reviewer Agent

**Role:** Reviews code diffs for quality, security, and correctness.

```
You are a Senior Code Reviewer reviewing a pull request diff.

Diff:
{diff}

Review for:
1. Correctness — does the code do what it claims?
2. Security — any injection, exposure, or auth issues?
3. Performance — any obvious bottlenecks?
4. Style — follows project conventions?
5. Edge cases — what happens with empty/null/unexpected input?

Rate each category: PASS, WARN, or FAIL.
Output as JSON:
{
  "overall": "approve|changes_requested|blocked",
  "issues": [
    {"severity": "critical|major|minor", "file": "...", "line": N, "message": "..."}
  ],
  "summary": "..."
}
```

**Used when:** After code is generated, before PR is opened. Can block PR if critical issues found.
**Integration:** Called inline in pipeline.py's implement phase between code generation and commit.

---

### Release Engineer Agent

**Role:** Opens safe, well-documented pull requests.

```
You are a Release Engineer. Open a pull request with:

Title: {title}
Branch: {branch}
Base: main

PR Body should include:
1. Summary of changes
2. Related issue reference
3. Test plan
4. Any manual steps needed after merge

Ensure:
- All changes are committed and pushed
- PR description is clear for human reviewers
- Issue is linked in PR description

Output the PR URL.
```

**Used when:** Final stage — creates the GitHub PR.

---

### Healer Agent

**Role:** Diagnoses and fixes failed tasks.

```
You are a Self-Healing Engineer. A task has failed.

Task: {task}
Error: {error}
Attempt: {attempt}/{max_attempts}

Diagnose the root cause and propose a fix.
If this is the last attempt, recommend whether to:
- Retry with a different approach
- Escalate to human
- Skip this task

Output as JSON:
{
  "root_cause": "...",
  "fix": "...",
  "recommendation": "retry|escalate|skip"
}
```

**Used when:** Pipeline task fails. Runs up to 3 times before escalating.
**Integration:** Called in pipeline.py when Reviewer finds issues — auto-fixes and retries review.

---

## 2. Skill Templates

### Skill: Git Operations

```yaml
name: git-operations
description: Clone, branch, commit, push, and open PRs
tools:
  - git clone
  - git checkout -b
  - git add -A && git commit -m
  - git push -u origin
  - gh pr create
inputs:
  - repo: string (user/repo format)
  - branch: string
  - message: string
  - title: string
  - body: string
output: PR URL
```

### Skill: LLM Fallback Chain

```yaml
name: llm-fallback
description: Try providers in order until one succeeds
providers:
  - name: local-ollama
    url: http://localhost:11434/v1
    model: qwen2.5-coder:32b
    priority: 1
  - name: deepseek
    url: https://api.deepseek.com/v1
    model: deepseek-chat
    priority: 2
  - name: claude
    url: https://api.anthropic.com/v1
    model: claude-sonnet-4-5
    priority: 3
strategy: sequential
retry: 3
timeout: 120s
```

### Skill: Telegram Notification

```yaml
name: telegram-notify
description: Send status updates to Telegram
endpoint: http://{telegram-bridge}:7700/notify
method: POST
payload:
  message: string (Markdown formatted)
message_templates:
  task_start: "🔄 *{issue_id}*: {title} — started on *{node}*"
  task_complete: "✅ *{issue_id}*: PR created — {pr_url}"
  task_failed: "❌ *{issue_id}*: Failed — {error}"
  llm_fallback: "⚠️ *{issue_id}*: Using {provider} fallback"
```

### Skill: Code Review

```yaml
name: code-review
description: Review code diffs for quality and security
categories:
  - correctness
  - security
  - performance
  - style
  - edge_cases
thresholds:
  overall: approve | changes_requested | blocked
  max_critical_issues: 0  (block if any)
  max_major_issues: 3     (changes requested if > 3)
output: JSON with issues array and overall verdict
```

---

## 3. Configuration Templates

### OpenCode config (`~/.config/opencode/config.yaml`)

```yaml
models:
  studio:
    provider: openai
    model: qwen2.5-coder:32b
    apiBase: http://STUDIO_TAILSCALE_IP:11434/v1
    apiKey: ollama
  deepseek:
    provider: openai
    model: deepseek-chat
    apiKey: ${DEEPSEEK_API_KEY}
    apiBase: https://api.deepseek.com/v1
  claude:
    provider: anthropic
    model: claude-sonnet-4-5
    apiKey: ${CLAUDE_API_KEY}
defaultModel: studio

git:
  branchPrefix: feat/
  autoCommit: true
  autoPush: true
  autoPr: true
  baseBranch: main
```

### 9router (unified LLM proxy, port 20128)

All LLM calls from opencode route through 9router for centralized key management and provider routing.

```bash
# Start
docker compose -f compose/studio.yml up -d 9router

# Dashboard
open http://localhost:20128/dashboard
```
```
  - model_name: claude-sonnet-4-5
    litellm_params:
      model: anthropic/claude-sonnet-4-5
      api_key: ${CLAUDE_API_KEY}
fallbacks:
  - studio-coder: [deepseek-chat, claude-sonnet-4-5]
```

---

## 4. Workflow Templates

### Standard PR Generation (two-phase tag trigger)

```
Phase: plan (tag: "plan")
  1. PM Agent: spec ← issue description
  2. Save plan.md → branch feat/{issue-id}
  3. Release Agent: open DRAFT PR

Phase: implement (tag: "implement")
  4. Architect Agent: technical plan ← plan.md
  5. Coder Agent: code ← plan
  6. Reviewer Agent: review ← diff
  7. If passes: Release Agent: commit + update PR
  8. If changes_requested: Healer Agent: fix ← issues (max 3 retries)
     - If still failing → human review (Telegram + PWA)
  9. If blocked: stop, notify human
  10. Notify: Telegram + PWA ← result
```

### Bug Fix

```
1. Coder Agent: diagnosis ← error description + relevant code
2. Architect Agent: fix plan ← diagnosis
3. Coder Agent: fix ← fix plan
4. Reviewer Agent: review ← diff
5. Release Agent: PR ← fix
6. Notify: Telegram ← PR URL
```

### Manual Review (Air User)

```
1. User runs: opencode "describe the issue and proposed fix"
2. OpenCode generates fix using configured model (Studio Ollama or cloud)
3. User reviews diff in VS Code
4. User commits + pushes manually
5. User opens PR manually (or via gh CLI)
```

---

## 5. Complexity Gate & Model Routing

A complexity gate classifies each task before the agent chain runs, routing it to the
most cost-effective LLM provider.

### Tier Classification

| Tier | Trigger | Provider | Cost/Task | Typical Tasks |
|---|---|---|---|---|
| **Simple** | Classifier → `simple` | Ollama qwen2.5-coder:32b | $0 | Typo fix, config change, 1-file change, docs, deps |
| **Medium** | Classifier → `medium` | DeepSeek V4 Flash | ~$0.01 | Feature, refactor, 2-7 files |
| **Complex** | Classifier → `complex` | o3-mini (arch/coder/reviewer) + DeepSeek (PM/healer) | ~$0.08 | Architecture redesign, security, 8+ files |
| **Premium** | Label `premium`/`claude` on issue | Claude Sonnet 4 (arch/coder/reviewer) + DeepSeek (rest) | ~$0.30 | Critical code, external review |

### Per-Agent Provider Mapping

```
                     SIMPLE         MEDIUM        COMPLEX          PREMIUM
PM                  ─── Ollama ──   DeepSeek ──   DeepSeek ────   DeepSeek
Architect           ─── Ollama ──   DeepSeek ──   o3-mini ────   Claude 4
Coder               ─── Ollama ──   DeepSeek ──   o3-mini ────   Claude 4
Reviewer            ─── Ollama ──   DeepSeek ──   o3-mini ────   Claude 4
Healer              ─── Ollama ──   DeepSeek ──   DeepSeek ───   DeepSeek
Release (PR)        ─── Ollama ──   DeepSeek ──   DeepSeek ───   DeepSeek
```

### Fallback Rules (never hits Claude by accident)

| Primary Provider | Fallback Chain |
|---|---|
| `studio-coder` (Ollama) | → `deepseek` (V4 Flash) → error |
| `deepseek` (V4 Flash) | → error (no cheaper fallback) |
| `openai-codex` (o3-mini) | → `deepseek` (V4 Flash) → error |
| `claude-premium` (Sonnet 4) | → `openai-codex` (o3-mini) → `deepseek` (V4 Flash) |

### Classification Logic

The `classify_complexity()` function first checks for risk keywords
(security, performance, architecture, migration, redesign, new service,
new system, vulnerability). If none found, it asks DeepSeek Flash to
classify via a one-shot prompt. Default fallback: `medium`.

---

## 6. Prompt Engineering Guidelines

### Do's

- Always request JSON output for structured data
- Include context (repo, branch, file paths) in every prompt
- Be specific about expected output format
- Set temperature to 0.3 for code generation (deterministic)
- Use "you are a..." role prefix for agent identity

### Don'ts

- Don't ask the model to modify files it can't access
- Don't include irrelevant context (keeps token count low)
- Don't use temperature > 0.5 for code tasks
- Don't assume the model knows your repo structure

---

---

## 7. Environment Setup (devstation.sh)

### Known Bugs & Workarounds

1. **`set -euo pipefail` in `parse_args()`** — The last line `[ -z "$ACTION" ] && { usage; exit 1; }` returns non-zero when ACTION _is_ set, causing the script to exit immediately after parse_args. Fix: append `; true` after the check, or change to `[ -n "$ACTION" ] || { usage; exit 1; }`.

2. **`run()` function uses `eval "$*"`** — Breaks with arguments containing spaces or special characters (e.g. hostname with parentheses `"Nhan's Mac mini"`). Fix: use `"$@"` instead of `eval "$*"`. Replace `run() { ... eval "$*"; }` with `run() { ... "$@"; }`.

### Docker Desktop Headless Mode (macOS)

For performance on older Macs (macOS 12+):

| Setting | Value | Effect |
|---|---|---|
| `autoStart` | `true` | Starts on login |
| `openUIOnStartupDisabled` | `true` | No GUI window on boot |
| `memoryMiB` | `2048` | 2GB RAM (down from 4GB) |
| `swapMiB` | `512` | Minimal swap |
| `cpus` | `2` | Keep at 2 |
| `extensionsEnabled` | `false` | Saves resources |
| `kubernetesEnabled` | `false` | Must be off |
| `useResourceSaver` | `true` | Pauses idle containers |
| `useBackgroundIndexing` | `false` | Reduces CPU usage |

Settings file: `~/Library/Group Containers/group.com.docker/settings.json`

### Tailscale CLI

On macOS, install Tailscale via brew cask only (`tailscale-app`). The brew formula (`tailscale`) builds from source (slow). The app bundle already contains the CLI binary. To make `tailscale` command available:

```bash
# Wrapper script at /usr/local/bin/tailscale:
#!/bin/bash
exec /Applications/Tailscale.app/Contents/MacOS/Tailscale "$@"
```

### Compose Gotchas

- **Tailscale must be connected** before Docker starts, otherwise services fail to bind to `TAILSCALE_IP`
- The `TAILSCALE_IP` changes if Tailscale identity changes (re-login). Update `.env` when that happens.
- **ai-router volume mount overwrites litellm entrypoint**: mount the config file individually (`../ai-router/config.yaml:/app/config.yaml:ro`), not the whole directory.
- **LiteLLM `main-stable` is broken** (Wolfi Linux + Prisma incompatibility + routing strategy renamed). Replace with `light-router` (see `light-router/proxy.py`).

### Pipeline Known Bugs

1. **F-string format error (`Invalid format specifier`)** — Python 3.14+ f-strings fail when `:` appears inside complex `{}` expressions in LLM prompt templates. Fix: escape inner braces as `{{...}}` and pre-compute format strings outside the f-string.

2. **Missing `DEEPSEEK_MODEL`** — If not set, defaults to `deepseek-flash` which returns 400. Fix: set `DEEPSEEK_MODEL=deepseek-chat` in `.env`.

3. **Missing `WORKSPACE_DIR`** — Defaults to `/Volumes/work` which doesn't exist on this machine. Fix: set `WORKSPACE_DIR=/tmp/devstation-workspace`.

4. **`spec.get('files')` returns strings** — LLM may return `["file1.py"]` instead of `[{"path":"file1.py",...}]`. Fix: add `isinstance` check before calling `.get()`.

5. **`$env` denied in n8n Code nodes** — n8n blocks `$env` access in Code nodes by default. Fix: hardcode repo name instead of using `$env.GITHUB_REPO`.

6. **n8n IF node `array contains` incompatible** — The IF node V2 may have type mismatch issues. Fix: replace with a Code node that returns `[]` for skip.

7. **9router not responding** — Check container: `docker ps | grep 9router`. Restart if needed: `docker restart devstation-mini-9router-1`.

### .env Required Variables

| Variable | Purpose |
|---|---|
| `TAILSCALE_IP` | Tailscale IP for port binding |
| `POSTGRES_PASSWORD` | Postgres password (all services) |
| `N8N_ENCRYPTION_KEY` | n8n encryption key |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `TELEGRAM_CHAT_ID` | Telegram chat ID |
| `DEEPSEEK_API_KEY` | DeepSeek API key |
| `DEEPSEEK_MODEL` | Model name (`deepseek-chat` recommended) |
| `LINEAR_API_KEY` | Linear API key |
| `LINEAR_TEAM_ID` | Linear team UUID |
| `GITHUB_TOKEN` | GitHub PAT with repo scope |
| `WORKSPACE_DIR` | Pipeline workspace directory |
| `AGENT_ENGINE` | Default agent engine (`prompt` or `crewai`) |

---

## 9. Dual-Engine Architecture

The pipeline supports two execution engines, selectable per-project or via issue labels.

### Prompt Engine (Default)
Fast, stateless 6-agent chain using optimized templates. Best for standard tasks.

### CrewAI Engine (Optional)
Enhanced engine with **Project Baseline Analysis**. 
- Runs `RepoAnalyzer` first to extract project-specific conventions.
- Injects existing code patterns, directory structure, and testing styles into agent context.
- Best for complex tasks where following the "target project baseline" is critical.

**Selection Logic:**
1. **Label**: Add `crewai` label to Linear issue (highest priority).
2. **Project**: Set `agent_engine: crewai` in `projects.yaml`.
3. **Environment**: Set `AGENT_ENGINE=crewai` in `.env`.

---

## 8. Skill Catalog

| Skill File | Purpose |
|---|---|
| `skills/devstation-setup.yaml` | Complete setup workflow for Mac Mini orchestrator — Docker, Tailscale, n8n, Linear, Telegram, environment config, known bugs & fixes |

*This document is model-agnostic. These rules and skills work with CrewAI, OpenCode, Claude, DeepSeek, Gemini, or any other LLM framework.*