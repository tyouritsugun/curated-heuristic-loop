#!/usr/bin/env bash
# CHL API Server Startup Script
# Detects the appropriate venv and starts the API server

set -e

# Detect which venv exists and activate it
if [ -d ".venv-cpu" ]; then
    echo "Activating CPU mode venv..."
    source .venv-cpu/bin/activate
elif [ -d ".venv-apple" ]; then
    echo "Activating Apple Metal mode venv..."
    source .venv-apple/bin/activate
elif [ -d ".venv-nvidia" ]; then
    echo "Activating NVIDIA GPU mode venv..."
    source .venv-nvidia/bin/activate
elif [ -d ".venv-amd" ]; then
    echo "Activating AMD GPU mode venv..."
    source .venv-amd/bin/activate
elif [ -d ".venv-intel" ]; then
    echo "Activating Intel GPU mode venv..."
    source .venv-intel/bin/activate
else
    echo "ERROR: No API server venv found!"
    echo "Please install the API server first (see README.md Step 1)"
    exit 1
fi

echo "Starting CHL API server on http://127.0.0.1:8000"
echo "Press Ctrl+C to stop"
echo ""

# Start the API server
python -m uvicorn src.api.server:app --host 127.0.0.1 --port 8000
