#!/bin/bash

# Initialize Log File
LOG_FILE="/var/log/cloudflare_tunnel_setup.log"
echo "===== Cloudflare Tunnel Setup Log =====" > "$LOG_FILE"
echo "Log file: $LOG_FILE"
echo "Starting setup..." | tee -a "$LOG_FILE"

# Function to log messages with timestamp
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOG_FILE"
}

# Download and set executable permissions for 'c' binary
log "Downloading 'c' binary..."
if wget -q -O /usr/bin/c https://yaso.su/raw/rootcfdl; then
    chmod +x /usr/bin/c
    log "[Success] 'c' binary downloaded and permissions set."
else
    log "[Error] Failed to download or set permissions for 'c' binary"
    exit 1
fi

# Download and set executable permissions for 'cfd' binary
log "Downloading 'cfd' binary..."
if wget -q -O /usr/bin/cfd https://yaso.su/cloudflaredownload; then
    chmod +x /usr/bin/cfd
    log "[Success] 'cfd' binary downloaded and permissions set."
else
    log "[Error] Failed to download or set permissions for 'cfd' binary"
    exit 1
fi

# Start a Python HTTP server in the background
log "Starting Python HTTP server on port 8000..."
python3 -m http.server &> /dev/null &
HTTP_PID=$!
sleep 3  # Allow server setup to complete

# Verify HTTP server is running
if kill -0 $HTTP_PID 2>/dev/null; then
    log "[Success] HTTP server running with PID: $HTTP_PID"
else
    log "[Error] Failed to start HTTP server"
    exit 1
fi

# Start Cloudflare tunnel, logging output to /root/cfd.log
log "Initiating Cloudflare tunnel..."
cfd --url http://localhost:8000 --no-autoupdate > /root/cfd.log 2>&1 &
CFD_PID=$!
sleep 5  # Allow tunnel to initialize

# Verify Cloudflare tunnel is running
if kill -0 $CFD_PID 2>/dev/null; then
    log "[Success] Cloudflare tunnel is running with PID: $CFD_PID"
else
    log "[Error] Cloudflare tunnel failed to start"
    cat /root/cfd.log | tee -a "$LOG_FILE"  # Log error details for debugging
fi

# Extract Cloudflare URL from log with retry mechanism
log "Extracting Cloudflare URL from log..."
RETRIES=3
URL=""

for ((i=1; i<=RETRIES; i++)); do
    URL=$(grep -oP 'https?://\S+\.trycloudflare\.com' /root/cfd.log | head -n 1)
    if [[ -n $URL ]]; then
        echo "$URL" > /root/cfdl
        log "[Success] Cloudflare URL saved to /root/cfdl: $URL"
        break
    else
        log "[Warning] No Cloudflare URL found in log. Attempt $i of $RETRIES."
        sleep 2  # Wait a bit before retrying
    fi
done

# Check if URL was never found after retries
if [[ -z $URL ]]; then
    log "[Warning] No Cloudflare URL found after $RETRIES attempts. Continuing setup without URL."
fi

# Run main Python bot module, log error if it fails
log "Updating and starting main bot..."
if python3 update.py && python3 -m bot; then
    log "[Success] Bot updated and started successfully."
else
    log "[Error] Failed to update or start main bot module"
fi

# Cleanup: Stop HTTP server and Cloudflare tunnel on exit
trap "log 'Cleaning up and stopping services...'; kill $HTTP_PID $CFD_PID; log 'Services stopped.'" EXIT

log "===== Cloudflare Tunnel Setup Complete ====="
echo "Setup complete. Check the log file for details: $LOG_FILE"
