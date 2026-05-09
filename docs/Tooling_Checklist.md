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