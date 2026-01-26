#!/bin/bash

# Run both web interface and worker in the background
# Useful for testing and development

set -e

# Color codes
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo -e "${RED}Virtual environment not found. Please run ./setup.sh first.${NC}"
    exit 1
fi

# Activate virtual environment
source venv/bin/activate

# Create PID directory
mkdir -p .pids

# Function to cleanup on exit
cleanup() {
    echo ""
    echo -e "${YELLOW}Shutting down...${NC}"

    if [ -f .pids/web.pid ]; then
        WEB_PID=$(cat .pids/web.pid)
        if ps -p $WEB_PID > /dev/null 2>&1; then
            echo "Stopping web interface (PID: $WEB_PID)"
            kill $WEB_PID 2>/dev/null || true
        fi
        rm .pids/web.pid
    fi

    if [ -f .pids/worker.pid ]; then
        WORKER_PID=$(cat .pids/worker.pid)
        if ps -p $WORKER_PID > /dev/null 2>&1; then
            echo "Stopping worker (PID: $WORKER_PID)"
            kill $WORKER_PID 2>/dev/null || true
        fi
        rm .pids/worker.pid
    fi

    echo -e "${GREEN}Shutdown complete${NC}"
    exit 0
}

# Register cleanup function
trap cleanup INT TERM

echo "=========================================="
echo "RSS Podcast Processor"
echo "=========================================="
echo ""

# Start web interface
echo -e "${GREEN}Starting web interface...${NC}"
python run_web.py > web.log 2>&1 &
WEB_PID=$!
echo $WEB_PID > .pids/web.pid
echo "  Web interface running (PID: $WEB_PID)"
echo "  Logs: web.log"
echo "  URL: http://localhost:5000"
echo ""

# Wait a moment for web to start
sleep 2

# Start worker
echo -e "${GREEN}Starting background worker...${NC}"
python run_worker.py > worker_stdout.log 2>&1 &
WORKER_PID=$!
echo $WORKER_PID > .pids/worker.pid
echo "  Worker running (PID: $WORKER_PID)"
echo "  Logs: worker.log, worker_stdout.log"
echo ""

echo "=========================================="
echo -e "${GREEN}Both services are running!${NC}"
echo "=========================================="
echo ""
echo "Web Interface: http://localhost:5000"
echo ""
echo "To view logs in real-time:"
echo "  Web:    tail -f web.log"
echo "  Worker: tail -f worker.log"
echo ""
echo "Press Ctrl+C to stop both services"
echo ""

# Wait for processes
wait $WEB_PID $WORKER_PID
