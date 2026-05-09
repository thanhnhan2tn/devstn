#!/bin/bash
# 9router Auto Backup Script
# Backs up db.json from HOST (not container) to 9router-config directory

BACKUP_DIR="/Users/mac/Documents/projects/mini-dev-station/9router-config"
HOST_FILE="$BACKUP_DIR/db.json"
CONTAINER_NAME="devstation-studio-9router-1"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

echo "[$(date)] Starting 9router backup..."

# Verify host file exists and has content
if [ ! -f "$HOST_FILE" ]; then
    echo "ERROR: Host file not found: $HOST_FILE"
    exit 1
fi

BACKUP_SIZE=$(wc -c < "$HOST_FILE")
if [ "$BACKUP_SIZE" -lt 1000 ]; then
    echo "ERROR: Host file seems too small ($BACKUP_SIZE bytes)"
    exit 1
fi

# Create timestamped backup
cp "$HOST_FILE" "$BACKUP_DIR/db.json.backup_$TIMESTAMP"
echo "Created backup: $BACKUP_DIR/db.json.backup_$TIMESTAMP"

# Keep only last 7 backups
cd $BACKUP_DIR && ls -t db.json.backup_* 2>/dev/null | tail -n +8 | xargs -r rm

# Verify
python3 -c "import json; d=json.load(open('$HOST_FILE')); print('Backup connections:', len(d.get('providerConnections',[])))"

echo "[$(date)] Backup completed: $BACKUP_SIZE bytes"