#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ACTION=""; NODE=""; DRY=""; SKIP_DOCKER=""; SKIP_OLLAMA=""
BRANCH="${BRANCH:-}"

log()  { printf "\033[1;36m[devstation]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[devstation]\033[0m %s\n" "$*"; }
fail() { printf "\033[1;31m[devstation] %s\033[0m\n" "$*" >&2; exit 1; }
run()  { [ -n "$DRY" ] && { echo "+ $@"; return; }; "$@"; }

usage() { cat <<'USAGE'
Usage: devstation.sh [--node mini|studio|air] [--dry-run] [--skip-docker] [--skip-ollama] <action>

Actions:
  setup         One-command setup for this machine (auto-detect or --node)
  up            Start Docker services for this node
  down          Stop Docker services
  restart       down + up
  status        Show running services and health
  logs [svc]    Tail logs (all, or one service)
  doctor        Check connectivity, ports, and health
  update        git pull + docker compose pull + up
  auto-update   Silently check for updates and apply (for cron)
  auto-fix      Detect and fix common issues (light-router, postgres, redis, pipeline)
  self-upgrade  Update devstation.sh itself from latest git
  maintenance   Collect error logs and propose AI fixes (daily)
  backup        pg_dump + volume backup
  restore <f>   Restore from backup
USAGE
}

cmd_maintenance() {
  log "Running daily AI maintenance..."
  local log_file="$ROOT/.run/maintenance.log"
  mkdir -p "$(dirname "$log_file")"
  
  {
    echo "=== Error Logs Collection: $(date) ==="
    echo "--- Docker Container Errors ---"
    docker ps -a --filter "status=exited" --format "{{.Names}}: {{.Status}}"
    
    echo -e "\n--- Pipeline Errors (Last 100 lines) ---"
    [ -f "$ROOT/.run/pipeline.log" ] && tail -n 100 "$ROOT/.run/pipeline.log" | grep -iE "error|fail|exception" || echo "No pipeline log found"
    
    echo -e "\n--- 9router Errors ---"
    docker compose -f "$(compose_file)" logs --tail=100 9router 2>&1 | grep -iE "error|fail" || echo "No 9router errors"
    
    echo -e "\n--- System Health ---"
    df -h / | grep /
    free -m 2>/dev/null || top -l 1 | head -n 10
  } > "$log_file"

  log "Logs collected at $log_file. Proposing AI fix..."
  
  if command -v opencode &>/dev/null; then
    local proposal; proposal="$(opencode run "Analyze these error logs and propose a fix plan. Output as a concise maintenance report: $(cat "$log_file")" 2>/dev/null)"
    if [ -n "$proposal" ]; then
      echo -e "\n=== AI Maintenance Proposal ===\n$proposal" >> "$log_file"
      log "AI Proposal generated. See $log_file"
      
      # Notify via Telegram if configured
      local mini_ip; mini_ip="$(grep MINI_SERVER_IP "$ROOT/.env" 2>/dev/null | cut -d= -f2)"
      if [ -n "$mini_ip" ]; then
        curl -s -X POST "http://${mini_ip}:7700/notify" -H "Content-Type: application/json" \
          -d "{\"message\": \"🛠 *Daily Maintenance Report*\n\n$(echo "$proposal" | head -c 1000)\"}" &>/dev/null
      fi
    fi
  else
    warn "opencode not found, skipping AI proposal"
  fi
}

detect_node() {
  local h; h="$(scutil --get ComputerName 2>/dev/null || hostname)"
  case "$h" in
    *[Mm]ini*)   echo mini ;;
    *[Ss]tudio*) echo studio ;;
    *[Aa]ir*)    echo air ;;
    *) fail "cannot detect node, pass --node mini|studio|air" ;;
  esac
}

parse_args() {
  ACTION=""; ARGS=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --node)        NODE="$2"; shift 2 ;;
      --dry-run)     DRY=1; shift ;;
      --skip-docker) SKIP_DOCKER=1; shift ;;
      --skip-ollama) SKIP_OLLAMA=1; shift ;;
      -h|--help)     usage; exit 0 ;;
      *)             [ -z "$ACTION" ] && ACTION="$1" || ARGS+=("$1"); shift ;;
    esac
  done
  [ -z "$NODE" ] && NODE="$(detect_node)"
  [ -z "$ACTION" ] && { usage; exit 1; }
  true
}

require_env() {
  log "Checking .env..."
  [ ! -f "$ROOT/.env" ] && fail "Missing .env"
  log ".env found"
}

check_docker() {
  log "Checking Docker..."
  docker info &>/dev/null || fail "Docker not running"
  log "Docker running"
}

compose_file() {
  case "$NODE" in
    mini)   echo "$ROOT/compose/mini.yml" ;;
    studio) echo "$ROOT/compose/studio.yml" ;;
    air)    fail "MacBook Air has no Docker services (it's a client machine)" ;;
  esac
}

current_branch() {
  git -C "$ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "main"
}

current_remote() {
  local b; b="$(current_branch)"
  git -C "$ROOT" config "branch.$b.remote" 2>/dev/null || echo "origin"
}

# ============================================================================
# ACTIONS
# ============================================================================

cmd_setup() {
  log "Setting up node: $NODE"

  # ── Prerequisites ──────────────────────────────────────────────────────
  if ! command -v brew &>/dev/null; then
    log "Installing Homebrew..."
    run /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  fi

  if ! command -v gh &>/dev/null; then
    log "Installing GitHub CLI..."
    run brew install gh
  fi

  if [ -z "$SKIP_DOCKER" ]; then
    if ! command -v docker &>/dev/null; then
      log "Installing Docker..."
      run brew install --cask docker
      warn "Open Docker.app to complete setup, then re-run this command"
      exit 0
    fi

    # ── Docker Desktop headless config ─────────────────────────────────────
    local dsettings="$HOME/Library/Group Containers/group.com.docker/settings.json"
    if [ -f "$dsettings" ]; then
      log "Configuring Docker Desktop for headless/performance..."
      run python3 -c "
  import json
  with open('$dsettings') as f: s = json.load(f)
  s['autoStart'] = True
  s['openUIOnStartupDisabled'] = True
  s['memoryMiB'] = min(s.get('memoryMiB', 2048), 2048)
  s['cpus'] = min(s.get('cpus', 4), 2)
  s['extensionsEnabled'] = False
  s['kubernetesEnabled'] = False
  s['useResourceSaver'] = True
  s['useBackgroundIndexing'] = False
  with open('$dsettings', 'w') as f: json.dump(s, f, indent=2)
  " 2>/dev/null || warn "Could not auto-configure Docker Desktop settings"
    fi

    # Ensure Docker is running
    if ! docker info &>/dev/null; then
      log "Starting Docker Desktop..."
      open -a Docker
      for i in $(seq 1 30); do
        docker info &>/dev/null && log "Docker ready" && break
        sleep 2
      done
    fi
  else
    log "Skipping Docker setup (--skip-docker)"
  fi

  # ── GitHub auth ────────────────────────────────────────────────────────
  if ! gh auth status &>/dev/null; then
    log "Authenticating with GitHub..."
    run gh auth login
  fi

  local gh_name hostname
  gh_name="$(gh api user --jq '.name // .login' 2>/dev/null || echo "AI Dev Station")"
  hostname="$(scutil --get ComputerName 2>/dev/null || hostname -s 2>/dev/null || echo "$NODE")"
  run git config --global user.name "$gh_name ($hostname)"
  run git config --global pull.rebase false

  mkdir -p "$ROOT/.run"

  # ── .env ───────────────────────────────────────────────────────────────
  if [ ! -f "$ROOT/.env" ]; then
    run cp "$ROOT/.env.example" "$ROOT/.env"
    warn ".env created from .env.example — edit it with your values, then re-run"
  fi

  case "$NODE" in
    mini)   cmd_mini_setup ;;
    studio) cmd_studio_setup ;;
    air)    cmd_air_setup ;;
  esac
  log "Setup complete for $NODE"
}

cmd_mini_setup() {
  log "Setting up Mac Mini orchestrator..."

  # ── Tailscale ──────────────────────────────────────────────────────────
  if ! command -v tailscale &>/dev/null; then
    log "Installing Tailscale..."
    run brew install --cask tailscale-app
    warn "Open Tailscale.app, sign in, then re-run this command"
    exit 0
  fi

  # Create tailscale CLI wrapper (avoid slow brew formula build)
  if ! command -v tailscale &>/dev/null || ! tailscale version &>/dev/null 2>&1; then
    log "Creating tailscale CLI wrapper..."
    run sudo tee /usr/local/bin/tailscale > /dev/null <<'WRAPPER'
#!/bin/bash
exec /Applications/Tailscale.app/Contents/MacOS/Tailscale "$@"
WRAPPER
    run sudo chmod +x /usr/local/bin/tailscale
  fi

  # Check if tailscale is logged in
  if ! tailscale status &>/dev/null; then
    log "Tailscale installed but not connected. Opening app for sign-in..."
    open -a Tailscale
    warn "Sign in to Tailscale, then re-run this command"
    exit 0
  fi

  local ts_ip
  ts_ip="$(tailscale ip -4 2>/dev/null || echo '')"
  if [ -n "$ts_ip" ]; then
    log "Tailscale IP: $ts_ip"
    # Update .env TAILSCALE_IP if it's a placeholder or wrong
    if grep -q "TAILSCALE_IP=100\.x\.x\.x\|TAILSCALE_IP=your_" "$ROOT/.env" 2>/dev/null; then
      run sed -i '' "s/^TAILSCALE_IP=.*/TAILSCALE_IP=$ts_ip/" "$ROOT/.env"
      run sed -i '' "s/^STUDIO_TAILSCALE_IP=.*/STUDIO_TAILSCALE_IP=$ts_ip/" "$ROOT/.env"
      log "Updated TAILSCALE_IP in .env"
    fi
  fi

  # ── Generate credentials if placeholders remain ──────────────────────
  if grep -q "CHANGE_ME" "$ROOT/.env" 2>/dev/null; then
    log "Generating secure credentials..."
    local pg_pass n8n_key
    pg_pass="$(openssl rand -base64 24 | tr -dc 'a-zA-Z0-9' | head -c 24)"
    n8n_key="$(openssl rand -base64 32 | tr -dc 'a-zA-Z0-9' | head -c 32)"
    run sed -i '' "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$pg_pass/" "$ROOT/.env"
    run sed -i '' "s/^N8N_ENCRYPTION_KEY=.*/N8N_ENCRYPTION_KEY=$n8n_key/" "$ROOT/.env"
    log "Generated POSTGRES_PASSWORD and N8N_ENCRYPTION_KEY"
  fi

  # ── Start Docker services ──────────────────────────────────────────────
  cmd_up

  # ── Start pipeline ─────────────────────────────────────────────────────
  if [ -f "$ROOT/pipeline.py" ] && command -v python3 &>/dev/null; then
    log "Setting up pipeline service..."
    # Install Python deps
    if [ ! -d /tmp/pipeline-venv ]; then
      python3 -m venv /tmp/pipeline-venv 2>/dev/null
      source /tmp/pipeline-venv/bin/activate
      pip install -q httpx pyyaml uvicorn fastapi python-dotenv prometheus-client 2>/dev/null || true
    fi

    # Write wrapper
    run cat > /usr/local/bin/devstation-pipeline <<'PWRAPPER'
#!/bin/bash
set -euo pipefail
cd /Users/nhanthai/Downloads/workspace/workspace/mini-dev-station
source /tmp/pipeline-venv/bin/activate
export $(grep -v '^#' .env | xargs)
exec python3 pipeline.py --serve --port 8001
PWRAPPER
    run chmod +x /usr/local/bin/devstation-pipeline

    # Start if not already running
    if ! curl -s http://127.0.0.1:8001/healthz &>/dev/null; then
      run nohup /usr/local/bin/devstation-pipeline > "$ROOT/.run/pipeline.log" 2>&1 &
      local pid=$!
      echo "$pid" > "$ROOT/.run/pipeline.pid"
      sleep 2
      if curl -s http://127.0.0.1:8001/healthz &>/dev/null; then
        log "Pipeline started (PID $pid)"
      else
        warn "Pipeline may not have started — check .run/pipeline.log"
      fi
    else
      log "Pipeline already running"
    fi
  fi
}

cmd_studio_setup() {
  log "Setting up Mac Studio worker..."

  if [ -z "$SKIP_OLLAMA" ]; then
    # Ollama
    if ! command -v ollama &>/dev/null; then
      log "Installing Ollama..."
      run brew install ollama
      run ollama serve &>/dev/null &
      sleep 2
    fi

    log "Pulling Ollama models (this will take a while first time)..."
    run ollama pull qwen2.5-coder:32b-instruct-q8_0 || warn "Could not pull 32b model, trying 7b..."
    run ollama pull qwen2.5-coder:7b 2>/dev/null || true
    run ollama pull nomic-embed-text 2>/dev/null || true

    # Start as service
    run brew services start ollama 2>/dev/null || true
  else
    log "Skipping Ollama install and model pulls (--skip-ollama)"
  fi

  # Start Docker services
  if [ -z "$SKIP_DOCKER" ]; then
    cmd_up
  else
    log "Skipping Docker services (--skip-docker)"
  fi
}

cmd_air_setup() {
  log "Setting up MacBook Air client..."
  log "Installing OpenCode..."
  if ! command -v node &>/dev/null; then
    run brew install node
  fi
  run npm install -g opencode 2>/dev/null || warn "OpenCode install failed, try: npm install -g opencode"

  # Create OpenCode config directory
  run mkdir -p "$HOME/.config/opencode"
  if [ ! -f "$HOME/.config/opencode/config.yaml" ]; then
    run cp "$ROOT/opencode-air-config.yaml" "$HOME/.config/opencode/config.yaml"
    log "OpenCode config installed to ~/.config/opencode/config.yaml"
    warn "Edit ~/.config/opencode/config.yaml with your API keys and Studio IP"
  fi

  log "Air setup done. You can now use: opencode 'describe your fix'"
}

cmd_up() {
  local file
  file="$(compose_file)"
  run docker compose -f "$file" --env-file "$ROOT/.env" up -d
  log "Services started for $NODE"
}

cmd_down() {
  local file
  file="$(compose_file)"
  run docker compose -f "$file" down
  log "Services stopped for $NODE"
}

cmd_restart() {
  cmd_down
  cmd_up
}

cmd_status() {
  log "Node: $NODE"
  log "---"

  case "$NODE" in
    mini)
      docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null || true
      log "---"
      log "Tailscale IP: $(tailscale ip -4 2>/dev/null || echo 'unknown')"
      ;;
    studio)
      docker ps --format "table {{.Names}}\t{{.Status}}" 2>/dev/null || true
      log "---"
      log "Workers: $(docker ps --filter name=worker --format '{{.Names}}' 2>/dev/null | tr '\n' ' ' || echo 'none')"
      log "Ollama: $(ollama list 2>/dev/null | head -5 || echo 'not running')"
      log "Models: $(ollama ps 2>/dev/null | tail -n +2 | awk '{print $1}' | tr '\n' ' ' || echo 'none loaded')"
      ;;
    air)
      log "OpenCode: $(command -v opencode &>/dev/null && echo 'installed' || echo 'not installed')"
      log "Git User: $(git config --global user.name 2>/dev/null || echo 'not set')"
      log "Node: $(node --version 2>/dev/null || echo 'not installed')"
      ;;
  esac
}

cmd_logs() {
  local file svc
  file="$(compose_file)"
  svc="${ARGS[0]:-}"
  if [ -n "$svc" ]; then
    run docker compose -f "$file" logs -f "$svc"
  else
    run docker compose -f "$file" logs -f
  fi
}

cmd_doctor() {
  log "Running diagnostics for $NODE..."
  case "$NODE" in
    mini)
      check_docker
      local ts_ip; ts_ip="$(grep TAILSCALE_IP "$ROOT/.env" 2>/dev/null | cut -d= -f2)"

      log "Pinging Studio Tailscale..."
      local studio_ip; studio_ip="$(grep STUDIO_TAILSCALE_IP "$ROOT/.env" 2>/dev/null | cut -d= -f2)"
      ping -c 1 -W 2 "$studio_ip" &>/dev/null && log "  Studio reachable" || warn "  Studio not reachable"

      log "Checking Docker containers..."
      docker compose -f "$(compose_file)" ps 2>/dev/null || warn "  No compose file found"

      log "Checking Light Router..."
      if curl -s "http://${ts_ip}:4000/healthz" | grep -q ok; then
        log "  Light Router OK"
      else
        warn "  Light Router not responding — rebuilding..."
        docker compose -f "$(compose_file)" --env-file "$ROOT/.env" up -d --build light-router 2>/dev/null && log "  Light Router rebuilt" || warn "  Light Router rebuild failed"
      fi

      log "Checking Grafana..."
      curl -sI "http://${ts_ip}:3000" &>/dev/null && log "  Grafana OK" || warn "  Grafana not responding"
      log "Checking Prometheus..."
      curl -s "http://${ts_ip}:9090/-/healthy" &>/dev/null && log "  Prometheus OK" || warn "  Prometheus not responding"
      log "Checking PWA..."
      curl -sI "http://${ts_ip}:8080" &>/dev/null && log "  PWA OK" || warn "  PWA not responding"
      ;;
    studio)
      check_docker
      log "Checking Ollama..."
      curl -s http://localhost:11434/api/tags | grep -q models && log "  Ollama OK" || warn "  Ollama not responding"
      log "Checking Pipeline..."
      curl -s "http://localhost:8001/healthz" | grep -q ok && log "  Pipeline OK" || warn "  Pipeline not responding"
      log "Checking Workers..."
      local worker_count; worker_count="$(docker ps --filter name=worker --format '{{.Names}}' 2>/dev/null | wc -l | tr -d ' ')"
      log "  Workers running: $worker_count (expected: 3)"
      log "Pinging Mini Tailscale..."
      ping -c 1 -W 2 "$(grep TAILSCALE_IP "$ROOT/.env" 2>/dev/null | cut -d= -f2)" &>/dev/null && log "  Mini reachable" || warn "  Mini not reachable"
      ;;
    air)
      log "OpenCode: $(opencode --version 2>/dev/null || echo 'not installed')"
      log "Git: $(git --version 2>/dev/null || echo 'not installed')"
      log "Node: $(node --version 2>/dev/null || echo 'not installed')"
      log "Config: $(cat "$HOME/.config/opencode/config.yaml" 2>/dev/null | head -3 || echo 'not found')"
      ;;
  esac
  log "Doctor check complete"
}

cmd_update() {
  local file
  file="$(compose_file)"
  local branch; branch="$(current_branch)"
  log "Pulling latest code from git (branch: $branch)..."
  local old_hash new_hash
  old_hash="$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || true)"
  git -C "$ROOT" pull --ff-only origin "$branch" 2>&1
  new_hash="$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || true)"
  if [ "$old_hash" != "$new_hash" ]; then
    log "Code updated ($(echo "$old_hash" | cut -c1-7) → $(echo "$new_hash" | cut -c1-7))"
  else
    log "Code already up to date"
  fi
  # Always rebuild custom services to pick up latest code changes
  log "Rebuilding custom services (light-router, telegram-bridge)..."
  docker compose -f "$file" --env-file "$ROOT/.env" up -d --build 2>&1
  log "Pulling new Docker images..."
  docker compose -f "$file" --env-file "$ROOT/.env" pull --ignore-pull-failures 2>&1
  cmd_up
  log "Update complete for $NODE"

  # Clean up old Docker images
  docker image prune -f 2>/dev/null || true
  log "Old Docker images cleaned"
}

# Auto-update: check for changes and apply them silently
cmd_auto_update() {
  local file
  file="$(compose_file 2>/dev/null || true)"
  cd "$ROOT" || return
  log "Checking for updates..."
  local old_hash new_hash branch remote
  old_hash="$(git rev-parse HEAD 2>/dev/null || true)"
  branch="$(current_branch)"
  remote="$(current_remote)"

  # Fetch without merging
  git fetch "$remote" 2>&1 || return

  # Check if local is behind
  local behind
  behind="$(git rev-list --count "HEAD..$remote/$branch" 2>/dev/null || echo 0)"
  if [ "$behind" -gt 0 ]; then
    log "Auto-update: $behind new commit(s) behind on $branch"
    git pull --ff-only "$remote" "$branch" 2>&1
    new_hash="$(git rev-parse HEAD)"
    log "Updated ($(echo "$old_hash" | cut -c1-7) → $(echo "$new_hash" | cut -c1-7))"
    if [ -n "$file" ] && [ "$NODE" != "air" ]; then
      # Rebuild custom services
      log "Rebuilding custom services..."
      docker compose -f "$file" --env-file "$ROOT/.env" up -d --build 2>&1 || true
    fi
    log "Auto-update complete"
  else
    log "Already up to date"
  fi

  # Auto-fix: check if light-router is healthy, rebuild if not
  local ts_ip; ts_ip="$(grep TAILSCALE_IP "$ROOT/.env" 2>/dev/null | cut -d= -f2)"
  if [ -n "$ts_ip" ] && [ "$NODE" = "mini" ]; then
    if ! curl -sf "http://${ts_ip}:4000/healthz" >/dev/null 2>&1; then
      warn "Light Router unhealthy — rebuilding..."
      docker compose -f "$(compose_file)" --env-file "$ROOT/.env" up -d --build light-router 2>/dev/null || true
    fi
  fi
}

cmd_backup() {
  require_env
  local backup_dir="$ROOT/backups"
  run mkdir -p "$backup_dir"
  local ts; ts="$(date +%Y%m%d_%H%M%S)"
  log "Backing up to $backup_dir/$ts..."
  run docker exec devstation-mini-postgres-1 pg_dump -U devstation devstation > "$backup_dir/db_$ts.sql" 2>/dev/null || warn "pg_dump failed (is postgres running?)"
  log "Backup complete"
}

cmd_restore() {
  local file="${ARGS[0]:-}"
  [ -z "$file" ] && fail "Usage: devstation.sh restore <backup_file>"
  require_env
  run docker exec -i devstation-mini-postgres-1 psql -U devstation devstation < "$file" 2>/dev/null || warn "restore failed"
  log "Restore complete"
}

cmd_self_upgrade() {
  local branch; branch="$(current_branch)"
  log "Upgrading devstation.sh from $branch branch..."
  run curl -fsSL -o "$0" "https://raw.githubusercontent.com/thanhnhan2tn/mini-dev-station/$branch/bin/devstation.sh" && chmod +x "$0" && log "Script upgraded, re-run with desired action"
}

# ============================================================================
# MAIN
# ============================================================================

parse_args "$@"

case "$ACTION" in
  setup)        cmd_setup ;;
  up)           require_env; check_docker; cmd_up ;;
  down)         check_docker; cmd_down ;;
  restart)      require_env; check_docker; cmd_restart ;;
  status)       cmd_status ;;
  logs)         check_docker; cmd_logs ;;
  doctor)       cmd_doctor ;;
  update)       log "Starting update for $NODE..."; if [ "$NODE" = "air" ]; then
         cd "$ROOT" && git fetch origin main && git checkout main && git pull --ff-only origin main 2>&1 || log "Already up to date"
         log "Update complete for Air"
       else
         require_env; check_docker; cmd_update
       fi ;;
  auto-update)  [ -z "$NODE" ] && NODE="$(detect_node)"; log "Auto-update for $NODE..."; cmd_auto_update ;;
  self-upgrade) cmd_self_upgrade ;;
  maintenance)  require_env; cmd_maintenance ;;
  backup)       check_docker; cmd_backup ;;
  restore)      check_docker; cmd_restore ;;
  *)            usage; exit 1 ;;
esac