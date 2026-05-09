#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
source /tmp/pipeline-venv/bin/activate
set -a; source .env; set +a
exec python3 pipeline.py --serve --port 8001
