#!/bin/bash
# 9router Restore Script
# Restores db.json from host backup to container

BACKUP_FILE="/Users/mac/Documents/projects/mini-dev-station/9router-config/db.json"
CONTAINER_NAME="devstation-studio-9router-1"

echo "[$(date)] Starting 9router restore..."

# Stop container
echo "Stopping container..."
docker stop $CONTAINER_NAME

# Verify backup exists and has content
BACKUP_SIZE=$(wc -c < "$BACKUP_FILE")
if [ "$BACKUP_SIZE" -lt 1000 ]; then
    echo "ERROR: Backup file seems empty ($BACKUP_SIZE bytes)"
    docker start $CONTAINER_NAME
    exit 1
fi

# Start container (it will use the host file via bind mount)
echo "Starting container..."
docker start $CONTAINER_NAME

# Wait for startup
sleep 5

# Verify
docker exec $CONTAINER_NAME cat /home/bun/.9router/db.json | python3 -c "import json,sys; d=json.load(sys.stdin); print('Restored connections:', len(d.get('providerConnections',[])))"

echo "[$(date)] Restore completed"