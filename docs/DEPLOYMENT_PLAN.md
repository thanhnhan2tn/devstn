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