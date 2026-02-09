# MCP Agent Memory Server - Docker Image
# A secure MCP server for shared agent memory

FROM python:3.12-slim

# Prevent Python from buffering stdout/stderr
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Create app directory
WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Build arguments for cache invalidation
ARG GIT_COMMIT=unknown
ARG CACHEBUST=1
RUN echo "Building from git commit: $GIT_COMMIT (cache: $CACHEBUST)"

# Copy application code
COPY src/ /app/src/

# Create data directory for memory file
RUN mkdir -p /app/data

# Set environment variables
ENV MEMORY_FILE_PATH=/app/data/AGENTS.md
ENV AUDIT_LOG_PATH=/app/data/audit.log

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run the application
CMD ["python", "-m", "uvicorn", "src.mcp_agent_memory.app:app", "--host", "0.0.0.0", "--port", "8000"]
