# AI Dev Station

24/7 AI software development station. Converts Linear tickets into GitHub PRs via tag-based two-phase workflow.

## Architecture

### Node topology

| Machine | Role | Hardware | Services |
|---|---|---|---|
| **Mac Mini 2015** | Orchestrator | Intel i7, 16 GB | Docker: postgres, redis, n8n, telegram-bridge, 9router, **grafana**, **prometheus**, **iphone-pwa** |
| **Mac Studio M1 Max** | Worker | 64 GB | Ollama (brew), Docker: **pipeline** (:8001), opencode, **3 parallel workers** |
| **MacBook Air M1** | Client | 8 GB | OpenCode CLI, VS Code, git |
| **iPhone** | Remote | — | Telegram app + **PWA dashboard** |

### LLM chain (via 9router)

```
Pipeline → 9router → Kiro AI (free) → MiniMax ($0.2/M) → DeepSeek ($0.14/M)
```

### 6-Agent Pipeline Chain

```
PM → Architect → Coder → Reviewer → Healer (auto-fix ×3 max) → Release
```

## Quick Start

### 1. Setup each machine

```bash
# Mac Mini — always-on orchestrator
./bin/devstation.sh --node mini setup

# Mac Studio — always-on worker (pulls Ollama models)
./bin/devstation.sh --node studio setup

# MacBook Air — your dev machine (installs OpenCode)
./bin/devstation.sh --node air setup
```

### 2. Configure `.env`

Copy `.env.example` to `.env` on each machine. Required values:
- `GITHUB_TOKEN` — GitHub personal access token
- `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` — notifications
- `STUDIO_TAILSCALE_IP` + `TAILSCALE_IP` — Tailscale addresses
- `MINI_SERVER_IP` — Mac Mini Tailscale IP for Telegram bridge (100.117.146.122)
- `LINEAR_API_KEY` + `LINEAR_TEAM_ID` — ticket source
- `DEEPSEEK_API_KEY` + `CLAUDE_API_KEY` — cloud LLM fallbacks

### 3. Start services

```bash
# On Mac Mini and Mac Studio
./bin/devstation.sh up
```

### 4. Configure projects

Edit `projects.yaml` to map Linear teams to GitHub repos.

### 5. Import n8n workflow

In n8n UI (`http://{TAILSCALE_IP}:5678`), import `linear-pipeline/linear_to_opencode.json`.

### 6. Verify

```bash
./bin/devstation.sh doctor
```

## Usage

### Tag-based triggering

1. Create a Linear issue
2. Add tag **"plan"** → pipeline creates plan PR (draft) on branch `feat/AI-123-title-slug`
3. Add tag **"implement"** → pipeline reads plan, generates code, commits to same PR branch

### Run pipeline manually

```bash
docker exec devstation-studio-pipeline-1 python pipeline.py \
  --issue "AI-123" --repo "user/repo" \
  --title "Fix login timeout bug" --phase full
```

### Use OpenCode on Air

```bash
opencode "fix the race condition in process.py"
opencode "add error handling" --model deepseek
```

## Dashboards

| URL | Service |
|---|---|
| `http://{TAILSCALE_IP}:3000` | Grafana (admin/{GRAFANA_PASSWORD}) |
| `http://{STUDIO_TAILSCALE_IP}:8001/healthz` | Pipeline status |
| `http://{TAILSCALE_IP}:8080` | iPhone PWA dashboard |

## Documentation

| Document | Contents |
|---|---|
| [`docs/Workflow.md`](docs/Workflow.md) | Two-phase tag workflow, 6-agent chain, multi-repo config |
| [`docs/Architecture.md`](docs/Architecture.md) | Network topology, trust zones, flow diagram |
| [`docs/9router-setup.md`](docs/9router-setup.md) | 9router config, providers, combo setup |
| [`docs/Self_Healing.md`](docs/Self_Healing.md) | Healer agent, retry logic, power failure |
| [`docs/Tooling_Checklist.md`](docs/Tooling_Checklist.md) | Per-machine prerequisites, env vars |
| [`docs/DEPLOYMENT_PLAN.md`](docs/DEPLOYMENT_PLAN.md) | Hardware requirements, setup order |
| [`docs/EXECUTION_GUIDE.md`](docs/EXECUTION_GUIDE.md) | Daily ops, remote control, backup |
| [`docs/TELEGRAM_SETUP.md`](docs/TELEGRAM_SETUP.md) | Bot creation, commands |
| [`AGENTS_RULES.md`](AGENTS_RULES.md) | Reusable agent rules, skills, prompts |
| [`projects.yaml`](projects.yaml) | Multi-repo project configuration |

## Telegram Commands

| Command | Action |
|---|---|
| `/start` | Show welcome message + main menu |
| `/menu` | Show main interactive menu |
| `/status` | Check pipeline status, active tasks, pending reviews |
| `/pause` | Pause pipeline |
| `/resume` | Resume pipeline |
| `/approve` | Approve current pending review |
| `/reject` | Reject current pending review |
| `/llm studio\|cloud\|auto` | Set LLM mode |
| `/help` | List all commands |

The bot also has **inline keyboards** for quick actions. Tap the buttons
in the chat instead of typing commands.
