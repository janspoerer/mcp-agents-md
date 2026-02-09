#!/bin/bash
# Install pre-commit hooks for MCP Agent Memory Server

set -e

echo "=== Installing Git Hooks ==="
echo ""

# Get repository root
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)

if [ -z "$REPO_ROOT" ]; then
    echo "Error: Not in a git repository"
    exit 1
fi

cd "$REPO_ROOT"

# Create hooks directory if it doesn't exist
HOOKS_DIR="$REPO_ROOT/.git/hooks"
mkdir -p "$HOOKS_DIR"

# Install pre-commit hook
PRE_COMMIT_SRC="$REPO_ROOT/.pre-commit-hook"
PRE_COMMIT_DST="$HOOKS_DIR/pre-commit"

if [ -f "$PRE_COMMIT_SRC" ]; then
    cp "$PRE_COMMIT_SRC" "$PRE_COMMIT_DST"
    chmod +x "$PRE_COMMIT_DST"
    echo "Installed: pre-commit hook"
else
    echo "Warning: .pre-commit-hook not found"
fi

# Verify installation
echo ""
echo "=== Installed Hooks ==="
ls -la "$HOOKS_DIR"/*.* 2>/dev/null || echo "No hooks installed"

echo ""
echo "=== Setup Complete ==="
echo ""
echo "The following checks will run before each commit:"
echo "  1. Check for sensitive files (.env, credentials)"
echo "  2. Python syntax validation"
echo "  3. Linting (if ruff or flake8 installed)"
echo "  4. Type checking (if mypy installed)"
echo "  5. Run tests (if pytest installed)"
echo "  6. Check for large files"
echo ""
echo "To skip hooks temporarily: git commit --no-verify"
echo ""

# Optional: Install development dependencies
echo "=== Optional: Install Dev Dependencies ==="
read -p "Install development dependencies (pytest, ruff, mypy)? [y/N] " -n 1 -r
echo ""

if [[ $REPLY =~ ^[Yy]$ ]]; then
    if [ -d "venv" ]; then
        source venv/bin/activate
        pip install pytest pytest-asyncio ruff mypy httpx
        echo "Development dependencies installed!"
    else
        echo "Warning: Virtual environment not found."
        echo "Run ./setup.sh first, then re-run this script."
    fi
fi

echo ""
echo "Done!"
