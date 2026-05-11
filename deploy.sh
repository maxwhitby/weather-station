#!/bin/bash
# Deploy weather station changes to the production proxy server
#
# The weather endpoints are part of the unified radar_server.py in the DISPLAYS repo.
# This script deploys that server to Oracle Cloud.
#
# Usage: ./deploy.sh

set -e

# Paths
# The DISPLAYS repo lives in iCloud (MERCURY archive), not next to this project.
DISPLAYS_BASE="${DISPLAYS_BASE:-/Users/maxwhitby/Library/Mobile Documents/com~apple~CloudDocs/MERCURY 20240717 /ACTIVE/CODE/MCP_FILESYSTEM/DISPLAYS}"
DISPLAYS_PROXY="$DISPLAYS_BASE/METOFFICE/radar_proxy"
SSH_KEY="$DISPLAYS_BASE/ssh-key-2025-12-23.key"
SERVER="ubuntu@130.162.190.206"
REMOTE_PATH="/opt/radar-proxy/radar_server.py"

if [ ! -f "$DISPLAYS_PROXY/radar_server.py" ]; then
    echo "ERROR: Production server not found at $DISPLAYS_PROXY/radar_server.py"
    exit 1
fi

if [ ! -f "$SSH_KEY" ]; then
    echo "ERROR: SSH key not found at $SSH_KEY"
    exit 1
fi

echo "Deploying radar_server.py to production..."
echo "  Local: $DISPLAYS_PROXY/radar_server.py"
echo "  Remote: $SERVER:$REMOTE_PATH"

# Copy file
scp -i "$SSH_KEY" -o StrictHostKeyChecking=no "$DISPLAYS_PROXY/radar_server.py" "$SERVER:/tmp/radar_server.py"

# Move to correct location and restart
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$SERVER" "
    sudo cp /tmp/radar_server.py $REMOTE_PATH
    rm /tmp/radar_server.py
    sudo systemctl restart radar-proxy
    sleep 3
    sudo systemctl status radar-proxy | head -5
"

echo ""
echo "Deployment complete. Verify:"
echo "  curl http://130.162.190.206:5050/weather/status"
