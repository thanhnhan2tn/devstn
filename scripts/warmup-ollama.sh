#!/bin/bash
echo "Waiting for Ollama to be available..."

OLLAMA_URL=""
MAX_RETRIES=12 # 1 minute total

# Load .env if it exists
if [ -f "$(dirname "$0")/../.env" ]; then
  source "$(dirname "$0")/../.env"
fi

function check_ollama() {
  if curl -s -m 2 http://127.0.0.1:11434 > /dev/null; then
    OLLAMA_URL="http://127.0.0.1:11434"
    return 0
  elif curl -s -m 2 http://host.docker.internal:11434 > /dev/null; then
    OLLAMA_URL="http://host.docker.internal:11434"
    return 0
  elif [ -n "$STUDIO_TAILSCALE_IP" ] && curl -s -m 2 "http://$STUDIO_TAILSCALE_IP:11434" > /dev/null; then
    OLLAMA_URL="http://$STUDIO_TAILSCALE_IP:11434"
    return 0
  fi
  return 1
}

for i in $(seq 1 $MAX_RETRIES); do
  if check_ollama; then
    break
  fi
  
  # If it's the first attempt and Ollama is offline, try to start it
  if [ "$i" -eq 1 ]; then
    echo "Ollama seems offline. Attempting to start it..."
    if command -v brew >/dev/null 2>&1; then
      brew services start ollama
    elif command -v ollama >/dev/null 2>&1; then
      # Run in background if brew isn't available
      OLLAMA_HOST=0.0.0.0 ollama serve > /dev/null 2>&1 &
    else
      echo "Cannot auto-start Ollama: command not found (this usually means the script is running inside a Docker container)."
    fi
  fi
  
  echo "Attempt $i/$MAX_RETRIES: Ollama not ready yet. Retrying in 5s..."
  sleep 5
done

if [ -z "$OLLAMA_URL" ]; then
  echo "Error: Could not reach Ollama after 60 seconds."
  exit 1
fi

echo "Found Ollama at $OLLAMA_URL"
echo "Warming up model qwen2.5-coder:32b-instruct-q8_0..."

curl -s "$OLLAMA_URL/api/generate" -d '{"model": "qwen2.5-coder:32b-instruct-q8_0", "prompt": "hi", "stream": false, "keep_alive": "-1"}' > /dev/null

echo "Model warmed up and kept alive!"
