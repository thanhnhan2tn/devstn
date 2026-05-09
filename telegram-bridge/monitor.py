#!/usr/bin/env python3
"""Telegram bridge monitor - checks health and auto-restarts docker container if needed."""

import os
import sys
import time
import subprocess
import logging
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [MONITOR] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler("/tmp/telegram-bridge-monitor.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("telegram-bridge-monitor")

BOT_CONTAINER = "devstation-mini-telegram-bridge-1"
BOT_PORT = 7700
CHECK_INTERVAL = 60
MAX_RESTARTS = 5
COMPOSE_FILE = "compose/mini.yml"


def check_port(host="localhost", port=BOT_PORT, timeout=3):
    """Check if port is open."""
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def check_health():
    """Check bot health endpoint."""
    import httpx
    try:
        r = httpx.get(f"http://localhost:{BOT_PORT}/healthz", timeout=5)
        return r.json().get("status") == "ok"
    except Exception:
        return False


def is_docker_running():
    """Check if docker container is running."""
    try:
        r = subprocess.run(
            ["docker", "ps", "--filter", f"name={BOT_CONTAINER}", "--format", "{{.Status}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return "Up" in r.stdout
    except Exception:
        return False


def docker_restart():
    """Restart docker container."""
    log.info(f"Restarting {BOT_CONTAINER}...")
    try:
        subprocess.run(
            ["docker", "compose", "-f", COMPOSE_FILE, "restart", "telegram-bridge"],
            cwd="/Users/nhanthai/Downloads/workspace/workspace/mini-dev-station",
            env={**os.environ, "TAILSCALE_IP": "100.117.146.122"},
            timeout=60,
        )
        log.info("Container restarted")
        return True
    except Exception as e:
        log.error(f"Failed to restart: {e}")
        return False


def docker_start():
    """Start docker container."""
    log.info(f"Starting {BOT_CONTAINER}...")
    try:
        subprocess.run(
            ["docker", "compose", "-f", COMPOSE_FILE, "up", "-d", "telegram-bridge"],
            cwd="/Users/nhanthai/Downloads/workspace/workspace/mini-dev-station",
            env={**os.environ, "TAILSCALE_IP": "100.117.146.122"},
            timeout=60,
        )
        log.info("Container started")
        return True
    except Exception as e:
        log.error(f"Failed to start: {e}")
        return False


def run_opencode_fix():
    """Run opencode to automatically fix the bot."""
    log.warning("Running opencode to auto-fix telegram-bridge...")
    try:
        result = subprocess.run(
            ["/Users/nhanthai/.opencode/bin/opencode", "-p", "Check telegram bot at telegram-bridge/bot.py, ensure it is healthy and all dependencies are correct. Test with: curl localhost:7700/healthz"],
            capture_output=True,
            text=True,
            timeout=120,
            cwd="/Users/nhanthai/Downloads/workspace/workspace/mini-dev-station",
        )
        log.info(f"Opencode output: {result.stdout[:500]}")
        if result.returncode == 0:
            log.info("Opencode fix completed")
            return True
        else:
            log.error(f"Opencode failed: {result.stderr[:500]}")
            return False
    except Exception as e:
        log.error(f"Opencode execution error: {e}")
        return False


def notify_alert(message: str):
    """Send alert via the bot's notify endpoint if available."""
    import httpx
    try:
        httpx.post(
            f"http://localhost:{BOT_PORT}/notify",
            json={"message": f"⚠️ {message}"},
            timeout=5,
        )
    except Exception:
        pass


def monitor_loop():
    """Main monitoring loop."""
    log.info("Telegram bridge monitor started (docker mode)")

    consecutive_failures = 0
    restarts_today = 0

    while True:
        try:
            port_open = check_port()
            health_ok = port_open and check_health()
            container_ok = is_docker_running()

            if health_ok:
                if consecutive_failures > 0:
                    log.info("Bot recovered - health check passed")
                    notify_alert("Telegram bridge recovered and is healthy")
                consecutive_failures = 0
                restarts_today = 0
            else:
                consecutive_failures += 1
                log.warning(f"Health check failed (consecutive: {consecutive_failures}, port:{port_open}, container:{container_ok})")

                if not container_ok:
                    log.warning("Container not running - starting...")
                    if docker_start():
                        restarts_today += 1
                        notify_alert("Telegram bridge container started")
                        consecutive_failures = 0
                elif not port_open:
                    log.warning("Port not responding - restarting container...")
                    if restarts_today < MAX_RESTARTS:
                        docker_restart()
                        restarts_today += 1
                        notify_alert("Telegram bridge restarted (port not responding)")
                        consecutive_failures = 0
                    else:
                        log.error("Max restarts reached, trying opencode fix...")
                        run_opencode_fix()
                        restarts_today = 0

            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            log.info("Monitor stopped by user")
            break
        except Exception as e:
            log.error(f"Monitor error: {e}")
            time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == "start":
            docker_start()
        elif sys.argv[1] == "stop":
            subprocess.run(["docker", "compose", "-f", COMPOSE_FILE, "stop", "telegram-bridge"], cwd="/Users/nhanthai/Downloads/workspace/workspace/mini-dev-station")
        elif sys.argv[1] == "restart":
            docker_restart()
        elif sys.argv[1] == "status":
            print(f"Port open: {check_port()}")
            print(f"Health OK: {check_health()}")
            print(f"Container running: {is_docker_running()}")
        elif sys.argv[1] == "fix":
            run_opencode_fix()
        else:
            print(f"Usage: {sys.argv[0]} [start|stop|restart|status|fix]")
    else:
        monitor_loop()