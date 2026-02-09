#!/bin/bash

# =============================================================================
# Deployment script for MCP Agent Memory Server
# =============================================================================
#
# This script rebuilds the Docker image and starts a new container.
#
# Usage:
#   ./pull_build_start_container.sh [prod|dev] [port]
#
# Examples:
#   ./pull_build_start_container.sh            # Default: production on port 8010
#   ./pull_build_start_container.sh prod       # Production on port 8010
#   ./pull_build_start_container.sh dev        # Development on port 8011
#   ./pull_build_start_container.sh dev 9000   # Development on port 9000

set -e

# =============================================================================
# Configuration
# =============================================================================
APP_NAME="mcp_agent_memory"
DEFAULT_PROD_PORT=8010
DEFAULT_DEV_PORT=8011

# Parse arguments or prompt for them
if [ $# -eq 0 ]; then
    echo "Select deployment environment:"
    echo "1) Production (default port $DEFAULT_PROD_PORT)"
    echo "2) Development (default port $DEFAULT_DEV_PORT)"
    read -r -p "Enter choice [1-2]: " env_choice

    case $env_choice in
        2)
            ENVIRONMENT="dev"
            DEFAULT_PORT=$DEFAULT_DEV_PORT
            ;;
        *)
            ENVIRONMENT="prod"
            DEFAULT_PORT=$DEFAULT_PROD_PORT
            echo ""
            echo "WARNING: You are about to deploy to PRODUCTION!"
            read -r -p "Are you sure you want to deploy to production? (yes/no): " confirm
            if [ "$confirm" != "yes" ]; then
                echo "Production deployment cancelled."
                exit 0
            fi
            ;;
    esac

    read -r -p "Enter port (default: $DEFAULT_PORT): " user_port
    PORT=${user_port:-$DEFAULT_PORT}
else
    ENVIRONMENT=${1:-prod}
    if [ "$ENVIRONMENT" = "dev" ]; then
        DEFAULT_PORT=$DEFAULT_DEV_PORT
    else
        DEFAULT_PORT=$DEFAULT_PROD_PORT
        echo ""
        echo "WARNING: You are about to deploy to PRODUCTION!"
        read -r -p "Are you sure you want to deploy to production? (yes/no): " confirm
        if [ "$confirm" != "yes" ]; then
            echo "Production deployment cancelled."
            exit 0
        fi
    fi
    PORT=${2:-$DEFAULT_PORT}
fi

echo "=========================================="
echo "Deployment Configuration"
echo "=========================================="
echo "Environment: $ENVIRONMENT"
echo "Port: $PORT"
echo "=========================================="

# =============================================================================
# Pre-flight checks
# =============================================================================

# Ensure the script is run from the correct directory
SCRIPT_DIR=$(dirname "$(readlink -f "$0" 2>/dev/null || realpath "$0")")
cd "$SCRIPT_DIR" || exit 1
echo "Working directory: $SCRIPT_DIR"

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo "ERROR: Docker is not installed. Please install Docker and try again."
    exit 1
fi

# Check if Docker Buildx is available
if ! docker buildx version &> /dev/null; then
    echo "ERROR: Docker Buildx is not available. Please ensure Docker Buildx is installed."
    exit 1
fi

# Check if Dockerfile exists
if [ ! -f Dockerfile ]; then
    echo "ERROR: Dockerfile not found in the current directory."
    exit 1
fi

# =============================================================================
# Pull latest code
# =============================================================================
echo ""
echo "Pulling latest changes from master branch..."
git pull origin master

# Get current git commit hash for build tracking
GIT_COMMIT=$(git rev-parse --short HEAD)
echo "Building from git commit: $GIT_COMMIT"

# =============================================================================
# Build Docker image
# =============================================================================
IMAGE_TAG="${APP_NAME}_${ENVIRONMENT}"
CONTAINER_NAME="${APP_NAME}_${ENVIRONMENT}"

echo ""
echo "Building Docker image: $IMAGE_TAG"
if ! docker buildx build \
    --build-arg GIT_COMMIT="$GIT_COMMIT" \
    --build-arg CACHEBUST="$(date +%s)" \
    -t "$IMAGE_TAG" .; then
    echo "ERROR: Docker build failed. Please check the Dockerfile and try again."
    exit 1
fi
echo "Docker image built successfully"

# =============================================================================
# Cleanup existing containers
# =============================================================================
echo ""
echo "Cleaning up existing container: $CONTAINER_NAME"
docker stop "$CONTAINER_NAME" 2>/dev/null || true
docker rm -f "$CONTAINER_NAME" 2>/dev/null || true

# Check if port is still in use
if lsof -Pi :"$PORT" -sTCP:LISTEN -t >/dev/null 2>&1; then
    echo "Port $PORT is still in use. Checking what's using it..."
    lsof -i :"$PORT" 2>/dev/null || true

    # Try to find and remove any Docker containers using this port
    echo "Looking for containers using port $PORT..."
    CONTAINERS_ON_PORT=$(docker ps -a --filter "publish=$PORT" --format "{{.Names}}")
    if [ -n "$CONTAINERS_ON_PORT" ]; then
        echo "Found containers on port $PORT: $CONTAINERS_ON_PORT"
        for container in $CONTAINERS_ON_PORT; do
            echo "Stopping and removing: $container"
            docker stop "$container" 2>/dev/null || true
            docker rm -f "$container" 2>/dev/null || true
        done
    fi

    sleep 3  # Give Docker time to release the port
fi

# =============================================================================
# Configure environment file
# =============================================================================
# Select environment-specific .env file on Hetzner server
CREDENTIALS_DIR="/home/deploy/dev/credentials/mcp-agents-md"
FALLBACK_CREDENTIALS_DIR="/srv/credentials/${APP_NAME}"

if [ "$ENVIRONMENT" = "dev" ]; then
    ENV_FILE="${CREDENTIALS_DIR}/.env_dev"
    FALLBACK_ENV_FILE="${FALLBACK_CREDENTIALS_DIR}/.env_${APP_NAME}_dev"
else
    ENV_FILE="${CREDENTIALS_DIR}/.env"
    FALLBACK_ENV_FILE="${FALLBACK_CREDENTIALS_DIR}/.env_${APP_NAME}"
fi

# Try primary location, then fallback, then local
if [ -f "$ENV_FILE" ]; then
    echo "Using environment file: $ENV_FILE"
elif [ -f "$FALLBACK_ENV_FILE" ]; then
    echo "Primary env file not found, using fallback: $FALLBACK_ENV_FILE"
    ENV_FILE="$FALLBACK_ENV_FILE"
else
    echo "Server environment files not found, checking local..."
    ENV_FILE="$SCRIPT_DIR/.env"
    if [ ! -f "$ENV_FILE" ]; then
        echo "ERROR: No .env file found. Checked locations:"
        echo "   - ${CREDENTIALS_DIR}/.env (primary)"
        echo "   - ${FALLBACK_CREDENTIALS_DIR}/.env_${APP_NAME} (fallback)"
        echo "   - $SCRIPT_DIR/.env (local)"
        echo ""
        echo "Please create a .env file with required environment variables"
        echo "Required variables: MCP_API_KEY"
        exit 1
    fi
    echo "Using local .env file: $ENV_FILE"
fi

# =============================================================================
# Resource limits (lightweight app)
# =============================================================================
MEMORY=512m
CPUS=1.0

# =============================================================================
# Run container
# =============================================================================
echo ""
echo "Starting container: $CONTAINER_NAME on port $PORT"

docker run -d \
    --name "$CONTAINER_NAME" \
    --restart unless-stopped \
    -p "$PORT:8000" \
    -e APP_ENV="$ENVIRONMENT" \
    --env-file "$ENV_FILE" \
    -v "${APP_NAME}_data:/app/data" \
    -m "$MEMORY" \
    --cpus="$CPUS" \
    "$IMAGE_TAG"

# =============================================================================
# Verify deployment
# =============================================================================
echo ""
echo "Waiting for container to start..."
sleep 5

if docker ps --filter "name=$CONTAINER_NAME" --filter "status=running" | grep -q "$CONTAINER_NAME"; then
    echo ""
    echo "=========================================="
    echo "Deployment successful!"
    echo "=========================================="
    echo "Container: $CONTAINER_NAME"
    echo "Image: $IMAGE_TAG"
    echo "Port: $PORT"
    echo "Health check: http://localhost:$PORT/health"
    echo "MCP endpoint: http://localhost:$PORT/mcp/sse"
    echo ""
    echo "View logs: docker logs -f $CONTAINER_NAME"
    echo "=========================================="
else
    echo ""
    echo "ERROR: Container failed to start. Checking logs..."
    docker logs "$CONTAINER_NAME" 2>&1 | tail -50
    exit 1
fi
