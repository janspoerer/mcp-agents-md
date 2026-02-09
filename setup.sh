#!/bin/bash
# Setup script for MCP Agent Memory Server

set -e

echo "=== MCP Agent Memory Server Setup ==="
echo ""

# Check Python version
python_version=$(python3 --version 2>&1)
echo "Python: $python_version"

# Create virtual environment
if [ ! -d "venv" ]; then
    echo ""
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate and install dependencies
echo ""
echo "Installing dependencies..."
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Create .env if it doesn't exist
if [ ! -f ".env" ]; then
    echo ""
    echo "Creating .env from template..."
    cp .env.example .env

    # Generate a random API key
    api_key=$(openssl rand -hex 32)
    if [[ "$OSTYPE" == "darwin"* ]]; then
        sed -i '' "s/your_very_secure_random_string_here/$api_key/" .env
    else
        sed -i "s/your_very_secure_random_string_here/$api_key/" .env
    fi

    echo "Generated API key (saved to .env): ${api_key:0:16}..."
    echo ""
    echo "IMPORTANT: Edit .env to configure Google Drive backup:"
    echo "  - GOOGLE_DRIVE_FOLDER_ID"
    echo "  - Add your service_account.json file"
fi

echo ""
echo "=== Setup Complete ==="
echo ""
echo "To start the server:"
echo "  source venv/bin/activate"
echo "  python app.py"
echo ""
echo "Or with uvicorn (auto-reload for development):"
echo "  source venv/bin/activate"
echo "  uvicorn app:app --reload"
echo ""
echo "To run tests:"
echo "  source venv/bin/activate"
echo "  pip install httpx  # If not installed"
echo "  python test_server.py"
