# Deployment Plan

## Hardware requirements

| Machine | Min RAM | Min Storage | Required |
|---|---|---|---|
| Mac Mini 2015 | 16 GB | 256 GB | Orchestrator (always on) |
| Mac Studio M1 Max | 64 GB | 512 GB | Worker (always on) |
| MacBook Air M1 | 8 GB | 256 GB | Client (on-demand) |

## Setup order

### Step 1: Mac Mini (orchestrator)

```bash
# Install prerequisites + start services
./bin/devstation.sh --node mini setup
./bin/devstation.sh --node mini up
./bin/devstation.sh --node mini doctor
```

### Step 2: Mac Studio (worker)

```bash
# Install Ollama + Docker + pull models + start services
./bin/devstation.sh --node studio setup
./bin/devstation.sh --node studio up
./bin/devstation.sh --node studio doctor
```

### Step 3: MacBook Air (client)

```bash
# Install OpenCode + configure
./bin/devstation.sh --node air setup
```

### Step 4: iPhone

1. Install Telegram
2. Create bot via @BotFather (see `docs/TELEGRAM_SETUP.md`)
3. Message your bot

## Network requirements

- All machines on same Tailscale network
- Mac Studio port 11434 accessible from Mini and Air
- Mac Mini ports 5432, 6379, 5678, 7700, 4000 accessible from Studio
- All machines have outbound HTTPS to GitHub, DeepSeek, Anthropic APIs

## Verification

```bash
# Run on all machines
./bin/devstation.sh doctor
```

# AI Dev Station — Tooling Checklist

## Prerequisites per machine

### Mac Mini (orchestrator)

| Tool | Install | Verify |
|---|---|---|
| Homebrew | `/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"` | `brew --version` |
| Docker | `brew install --cask docker` | `docker info` |
| Tailscale | `brew install --cask tailscale` | `tailscale status` |
| GitHub CLI | `brew install gh` | `gh --version` |

### Mac Studio (worker)

| Tool | Install | Verify |
|---|---|---|
| Homebrew | Same as above | `brew --version` |
| Docker | `brew install --cask docker` | `docker info` |
| Ollama | `brew install ollama` | `ollama --version` |
| Ollama models | `ollama pull qwen2.5-coder:32b` | `ollama list` |
| GitHub CLI | `brew install gh` | `gh --version` |

### MacBook Air (client)

| Tool | Install | Verify |
|---|---|---|
| Homebrew | Same as above | `brew --version` |
| Node.js | `brew install node` | `node --version` |
| OpenCode | `npm install -g opencode` | `opencode --version` |
| GitHub CLI | `brew install gh` | `gh --version` |

## One-command setup

```bash
# Run this on each machine — it installs everything above
./bin/devstation.sh setup
```

## Environment variables

Copy `.env.example` → `.env` on each machine. Required vars:

| Variable | Where needed | Description |
|---|---|---|
| `GITHUB_TOKEN` | All machines | GitHub personal access token |
| `DEEPSEEK_API_KEY` | Studio, Air | DeepSeek API key |
| `CLAUDE_API_KEY` | Studio, Air | Anthropic API key |
| `TELEGRAM_BOT_TOKEN` | Mini | Telegram bot token |
| `TELEGRAM_CHAT_ID` | Mini | Your Telegram chat ID |
| `LINEAR_API_KEY` | Mini | Linear API key |
| `STUDIO_TAILSCALE_IP` | Mini, Air | Studio's Tailscale IP |
| `TAILSCALE_IP` | Each node | This machine's Tailscale IP |

## Health verification

```bash
# On each machine
./bin/devstation.sh doctor
```

# 9router Setup Guide

9router is an AI router that provides:
- Token compression (RTK) - saves 20-40%
- Auto-fallback: Subscription → Cheap → Free
- Real-time quota tracking
- Web dashboard

## 1. Start 9router

```bash
# On Mac Studio
docker compose -f compose/studio.yml up -d 9router
```

Access dashboard: `http://localhost:20128/dashboard`
Password: `123456`

## 2. Add Providers via Dashboard

### Option A: Free Providers (Recommended)

**Kiro AI** (FREE unlimited - Claude 4.5 + GLM-5 + MiniMax)
1. Go to Dashboard → Providers
2. Click "Connect" on Kiro
3. OAuth login with GitHub/Google/AWS
4. Done - free unlimited!

**OpenCode Free** (No auth required)
1. Go to Dashboard → Providers
2. Click "Connect" on OpenCode Free
3. No login needed

### Option B: Paid Providers

**MiniMax** ($0.2/1M tokens)
1. Get API key from https://platform.minimax.io
2. Go to Dashboard → Providers → Add
3. Select MiniMax
4. Enter API key and base URL: `https://api.minimax.chat/v1`
5. Save

**DeepSeek** ($0.14/1M tokens)
1. Get API key from https://platform.deepseek.com
2. Go to Dashboard → Providers → Add
3. Select DeepSeek
4. Enter API key
5. Save

**NVIDIA** ($1.5/1M tokens)
1. Get API key from https://build.nvidia.com
2. Go to Dashboard → Providers → Add
3. Select NVIDIA
4. Enter API key
5. Save

## 3. Create Combo

Recommended combo for AI Dev Station:

1. Go to Dashboard → Combos → Create New
2. Name: `devstation-stack`
3. Add models in priority order:

```
1. kr/claude-sonnet-4.5    (free, unlimited)
2. minimax/MiniMax-M2.7   (cheap, $0.2/1M)
3. deepseek/deepseek-chat  (fallback, $0.14/1M)
```

## 4. Use in Pipeline

Pipeline automatically uses 9router. Configure in `pipeline.py`:

```python
LIGHT_ROUTER_URL = "http://9router:20128"

LLM_CONFIG = {
    "minimax": {
        "url": LIGHT_ROUTER_URL + "/v1",
        "model": "minimax/MiniMax-M2.7",
    },
    "deepseek": {
        "url": LIGHT_ROUTER_URL + "/v1",
        "model": "deepseek/deepseek-chat",
    },
}
```

## 5. Pipeline Tier Mapping

| Tier | Provider | Model |
|---|---|---|
| simple | studio-coder | ollama/qwen2.5-coder:32b |
| medium | minimax | minimax/MiniMax-M2.7 |
| complex | nvidia | nvidia/llama-3.1-nemotron-70b |
| premium | deepseek | deepseek/deepseek-chat |

## Cost Comparison

| Provider | Cost | Notes |
|---|---|---|
| Kiro AI | $0 | Free unlimited Claude 4.5 |
| OpenCode Free | $0 | No auth required |
| MiniMax M2.7 | $0.2/1M | Cheapest option |
| DeepSeek V4 | $0.14/1M | Good fallback |
| NVIDIA Nemotron | $1.5/1M | Premium fallback |

## Troubleshooting

**No providers available:**
```
Error: "No active credentials for provider"
```
→ Add providers in dashboard first

**Provider not working:**
1. Check dashboard → Providers → Test button
2. Verify API key is correct
3. Check account has credits/balance

**Dashboard not accessible:**
```bash
# Check 9router is running
docker ps | grep 9router

# Restart if needed
docker restart devstation-studio-9router-1
```

# Telegram Bot Setup

## 1. Create a bot

1. Open Telegram, search for `@BotFather`
2. Send `/newbot`
3. Choose a name (e.g., `AI Dev Station`)
4. Choose a username (e.g., `ai_dev_station_bot`)
5. Copy the token — it looks like: `1234567890:ABCdefGHIjklmNOPqrStuVWXyz`

## 2. Get your chat ID

1. Start a chat with your new bot
2. Send any message (e.g., `/start`)
3. Visit: `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
4. Look for `"chat":{"id":<YOUR_CHAT_ID>}` in the response

## 3. Configure

Add these to `.env` on the Mac Mini:

```bash
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklmNOPqrStuVWXyz
TELEGRAM_CHAT_ID=123456789
```

## 4. Available commands

| Command | Action |
|---|---|
| `/start` | Show welcome message + main menu |
| `/menu` | Show main interactive menu |
| `/status` | Check pipeline status, active tasks, pending reviews |
| `/pause` | Pause pipeline |
| `/resume` | Resume pipeline |
| `/approve` | Approve current pending review |
| `/reject` | Reject current pending review |
| `/llm studio` | Use Studio's Ollama |
| `/llm cloud` | Use cloud API |
| `/llm auto` | Auto-select |
| `/help` | List all commands |

The bot also has **inline keyboards** for quick actions — tap the buttons
right in the chat instead of typing commands.

