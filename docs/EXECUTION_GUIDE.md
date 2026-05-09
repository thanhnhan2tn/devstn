# Execution Guide — Operating the Dev Station

## Daily operations

### Check status

```bash
# On each machine
./bin/devstation.sh status

# Via Telegram (from iPhone or any device)
/status
```

### View logs

```bash
# All services
./bin/devstation.sh logs

# Specific service
./bin/devstation.sh logs pipeline
```

### Start / stop

```bash
# On Mac Mini or Mac Studio
./bin/devstation.sh up
./bin/devstation.sh down
./bin/devstation.sh restart
```

### Run the pipeline manually

```bash
# On Mac Studio
docker exec devstation-studio-pipeline-1 python pipeline.py \
  --issue "AI-123" \
  --repo "user/repo" \
  --title "Fix login timeout" \
  --body "Description of the issue"
```

### Use OpenCode on MacBook Air

```bash
# Uses Studio's Ollama by default
opencode "fix the race condition in process.py"

# Override LLM source
opencode "add error handling" --model deepseek
opencode "refactor this module" --model claude
```

## Remote control via iPhone

Open Telegram, message your bot:

| Command | What happens |
|---|---|
| `/status` | Bot replies with current pipeline state |
| `/pause` | Pipeline stops processing new tickets |
| `/resume` | Pipeline resumes |
| `/approve` | Current task approved, PR is finalized |
| `/reject` | Current task rejected, healer retries |
| `/llm cloud` | Switch to cloud APIs |
| `/llm studio` | Switch to Studio's local Ollama |

## Backup & restore

```bash
# On Mac Mini
./bin/devstation.sh backup

# Restore
./bin/devstation.sh restore backups/db_20260427_143022.sql
```

## Update

```bash
# Pull latest code + update Docker images
./bin/devstation.sh update
```