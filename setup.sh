#!/bin/bash

set -e  # Exit on error

echo "=========================================="
echo "RSS Podcast Processor Setup"
echo "=========================================="
echo ""

# Color codes for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

echo "Working directory: $SCRIPT_DIR"
echo ""

# Check for Homebrew
echo "Checking for Homebrew..."
if ! command -v brew &> /dev/null; then
    echo -e "${RED}✗ Homebrew not found${NC}"
    echo "Please install Homebrew first: https://brew.sh"
    exit 1
fi
echo -e "${GREEN}✓ Homebrew found${NC}"
echo ""

# Check for system dependencies
echo "Checking system dependencies..."

# Check chromaprint
if ! command -v fpcalc &> /dev/null; then
    echo -e "${YELLOW}! chromaprint not found, installing...${NC}"
    brew install chromaprint
    echo -e "${GREEN}✓ chromaprint installed${NC}"
else
    echo -e "${GREEN}✓ chromaprint found${NC}"
fi

# Check ffmpeg
if ! command -v ffmpeg &> /dev/null; then
    echo -e "${YELLOW}! ffmpeg not found, installing...${NC}"
    brew install ffmpeg
    echo -e "${GREEN}✓ ffmpeg installed${NC}"
else
    echo -e "${GREEN}✓ ffmpeg found${NC}"
fi

echo ""

# Check for Python 3
echo "Checking for Python 3..."
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}✗ Python 3 not found${NC}"
    echo "Please install Python 3.10 or later"
    exit 1
fi

PYTHON_VERSION=$(python3 --version | cut -d' ' -f2)
echo -e "${GREEN}✓ Python $PYTHON_VERSION found${NC}"
echo ""

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
    echo -e "${GREEN}✓ Virtual environment created${NC}"
else
    echo -e "${GREEN}✓ Virtual environment already exists${NC}"
fi
echo ""

# Activate virtual environment
echo "Activating virtual environment..."
source venv/bin/activate
echo -e "${GREEN}✓ Virtual environment activated${NC}"
echo ""

# Upgrade pip
echo "Upgrading pip..."
pip install --upgrade pip --quiet
echo -e "${GREEN}✓ pip upgraded${NC}"
echo ""

# Install/upgrade Python dependencies
echo "Installing Python dependencies..."
pip install -r requirements.txt --upgrade
echo -e "${GREEN}✓ Python dependencies installed${NC}"
echo ""

# Create data directories if they don't exist
echo "Creating data directories..."
mkdir -p data/original
mkdir -p data/processed
mkdir -p data/segments
echo -e "${GREEN}✓ Data directories ready${NC}"
echo ""

# Initialize database
echo "Initializing database..."
python3 << 'EOF'
from app.database import init_db
try:
    init_db()
    print("Database initialized successfully")
except Exception as e:
    print(f"Database initialization: {e}")
EOF
echo -e "${GREEN}✓ Database initialized${NC}"
echo ""

# Verify installation
echo "Verifying installation..."
python3 << 'EOF'
import sys
errors = []

try:
    import flask
except ImportError:
    errors.append("Flask")

try:
    import feedparser
except ImportError:
    errors.append("feedparser")

try:
    import acoustid
except ImportError:
    errors.append("pyacoustid")

try:
    from pydub import AudioSegment
except ImportError:
    errors.append("pydub")

try:
    import yaml
except ImportError:
    errors.append("PyYAML")

if errors:
    print(f"Missing dependencies: {', '.join(errors)}")
    sys.exit(1)
else:
    print("All dependencies verified")
EOF

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ Installation verified${NC}"
else
    echo -e "${RED}✗ Some dependencies are missing${NC}"
    exit 1
fi
echo ""

# Check if config needs updating
echo "Checking configuration..."
if grep -q "http://localhost:5000" config.yaml; then
    echo -e "${YELLOW}! Note: config.yaml still has default localhost URL${NC}"
    echo "  Update 'web.base_url' in config.yaml with your Tailscale hostname for remote access"
else
    echo -e "${GREEN}✓ Configuration appears customized${NC}"
fi
echo ""

echo "=========================================="
echo -e "${GREEN}Setup complete!${NC}"
echo "=========================================="
echo ""
echo "To verify the installation:"
echo "   source venv/bin/activate"
echo "   python test_installation.py"
echo ""
echo "To start using the application:"
echo ""
echo "1. Activate the virtual environment:"
echo "   source venv/bin/activate"
echo ""
echo "2. Start the web interface:"
echo "   python run_web.py"
echo "   Then visit: http://localhost:5000"
echo ""
echo "3. Start the background worker (in another terminal):"
echo "   source venv/bin/activate"
echo "   python run_worker.py"
echo ""
echo "Optional: Update config.yaml with your Tailscale hostname"
echo "for remote access from your phone."
echo ""
