"""
MCP Agent Memory Server
A secure, rate-limited MCP server for shared agent memory.

Uses the official MCP Python SDK (mcp.server.fastmcp.FastMCP).
"""

import os
import fcntl
import time
import logging
from datetime import datetime
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Callable

from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.security import APIKeyHeader
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.responses import Response as StarletteResponse
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from dotenv import load_dotenv

# =============================================================================
# Configuration
# =============================================================================

load_dotenv()

API_KEY = os.getenv("MCP_API_KEY")
FILE_PATH = os.getenv("MEMORY_FILE_PATH", "AGENTS.md")
AUDIT_LOG_PATH = os.getenv("AUDIT_LOG_PATH", "audit.log")
MAX_RULE_SIZE = int(os.getenv("MAX_RULE_SIZE", "10000"))  # 10KB default
RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "60"))
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60"))  # seconds

if not API_KEY:
    raise ValueError("MCP_API_KEY must be set in .env")

# =============================================================================
# Logging Setup
# =============================================================================

# Application logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("mcp-server")

# Audit logger (separate file for security events)
audit_logger = logging.getLogger("audit")
audit_handler = logging.FileHandler(AUDIT_LOG_PATH)
audit_handler.setFormatter(
    logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
)
audit_logger.addHandler(audit_handler)
audit_logger.setLevel(logging.INFO)

# =============================================================================
# Rate Limiting
# =============================================================================

rate_limit_store: dict[str, list[float]] = defaultdict(list)


def check_rate_limit(client_ip: str) -> bool:
    """Check if client has exceeded rate limit. Returns True if allowed."""
    now = time.time()
    # Clean old entries
    rate_limit_store[client_ip] = [
        t for t in rate_limit_store[client_ip]
        if now - t < RATE_LIMIT_WINDOW
    ]

    if len(rate_limit_store[client_ip]) >= RATE_LIMIT_REQUESTS:
        return False

    rate_limit_store[client_ip].append(now)
    return True


# =============================================================================
# File Operations (Thread-Safe)
# =============================================================================

def init_memory_file():
    """Initialize memory file if it doesn't exist."""
    if not os.path.exists(FILE_PATH):
        with open(FILE_PATH, "w") as f:
            f.write("# Agent Memory\n\n")
            f.write(f"- System initialized on {datetime.now().isoformat()}\n")
        logger.info(f"Created new memory file: {FILE_PATH}")


def read_memory_file() -> str:
    """Read the memory file with shared lock."""
    try:
        with open(FILE_PATH, "r") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                content = f.read()
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
            return content
    except FileNotFoundError:
        return "Error: Memory file not found."
    except Exception as e:
        logger.error(f"Read error: {e}")
        return f"Error reading file: {str(e)}"


def append_to_memory_file(rule: str, client_ip: str = "unknown") -> str:
    """Append to memory file with exclusive lock and audit logging."""
    # Validate input
    if not rule or not rule.strip():
        return "Error: Rule cannot be empty."

    if len(rule) > MAX_RULE_SIZE:
        audit_logger.warning(
            f"REJECTED - IP: {client_ip} - Rule exceeded size limit: {len(rule)} bytes"
        )
        return f"Error: Rule exceeds maximum size ({MAX_RULE_SIZE} bytes)."

    try:
        timestamp = datetime.now().isoformat()
        formatted_rule = f"\n- [{timestamp}] {rule.strip()}"

        with open(FILE_PATH, "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.write(formatted_rule)
                f.flush()
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

        # Audit log successful write
        audit_logger.info(
            f"WRITE - IP: {client_ip} - Size: {len(rule)} bytes - "
            f"Preview: {rule[:100]}{'...' if len(rule) > 100 else ''}"
        )
        logger.info(f"New rule appended ({len(rule)} bytes)")
        return "Successfully added new rule."

    except Exception as e:
        logger.error(f"Write error: {e}")
        audit_logger.error(f"WRITE_FAILED - IP: {client_ip} - Error: {e}")
        return f"Error writing to file: {str(e)}"


def get_file_stats() -> dict:
    """Get memory file statistics."""
    try:
        stat = os.stat(FILE_PATH)
        with open(FILE_PATH, "r") as f:
            lines = sum(1 for _ in f)
        return {
            "exists": True,
            "size_bytes": stat.st_size,
            "line_count": lines,
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat()
        }
    except FileNotFoundError:
        return {"exists": False, "size_bytes": 0, "line_count": 0, "modified": None}
    except Exception as e:
        return {"exists": False, "error": str(e)}


# =============================================================================
# Authentication Middleware for MCP
# =============================================================================

class APIKeyAuthMiddleware(BaseHTTPMiddleware):
    """Middleware to authenticate requests via X-API-Key header."""

    async def dispatch(
        self, request: StarletteRequest, call_next: Callable
    ) -> StarletteResponse:
        client_ip = request.client.host if request.client else "unknown"

        # Check API key
        api_key = request.headers.get("X-API-Key")
        if api_key != API_KEY:
            audit_logger.warning(f"MCP_AUTH_FAILED - IP: {client_ip}")
            return JSONResponse(
                status_code=403,
                content={"error": "Invalid or missing API Key"}
            )

        # Check rate limit
        if not check_rate_limit(client_ip):
            audit_logger.warning(f"MCP_RATE_LIMITED - IP: {client_ip}")
            return JSONResponse(
                status_code=429,
                content={"error": "Rate limit exceeded"}
            )

        audit_logger.info(f"MCP_REQUEST - IP: {client_ip} - Path: {request.url.path}")
        return await call_next(request)


# =============================================================================
# FastAPI Security (for REST endpoints)
# =============================================================================

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(
    request: Request,
    key: str = Depends(api_key_header)
):
    """Verify API key and check rate limit."""
    client_ip = request.client.host if request.client else "unknown"

    # Check API key
    if key != API_KEY:
        audit_logger.warning(f"AUTH_FAILED - IP: {client_ip} - Invalid API key")
        raise HTTPException(status_code=403, detail="Invalid or missing API Key")

    # Check rate limit
    if not check_rate_limit(client_ip):
        audit_logger.warning(f"RATE_LIMITED - IP: {client_ip}")
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    return {"key": key, "client_ip": client_ip}


# =============================================================================
# MCP Server Setup (Official SDK)
# =============================================================================

mcp = FastMCP(
    name="AgentMemory",
    instructions=(
        "This server provides shared persistent memory for AI agents. "
        "Use read_memory to retrieve stored knowledge and write_memory to add new learnings. "
        "All writes are timestamped and append-only to preserve history."
    ),
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["localhost:*", "127.0.0.1:*", "agents-md.spoerico.com:*", "agents-md.spoerico.com"],
        allowed_origins=["https://agents-md.spoerico.com", "https://agents-md.spoerico.com:*"],
    )
)


@mcp.tool(description="Read the AGENTS.md memory file containing shared agent knowledge and learnings.")
def read_memory() -> str:
    """
    Read the current contents of the agent memory file.

    Returns the full markdown content of AGENTS.md, which contains
    accumulated knowledge, rules, and learnings from all agents.
    """
    return read_memory_file()


@mcp.tool(description="Append a new rule, learning, or note to the AGENTS.md memory file.")
def write_memory(rule: str) -> str:
    """
    Append a new entry to the agent memory.

    The entry will be automatically timestamped and formatted as a markdown
    list item. This is append-only - existing content is never modified.

    Args:
        rule: The text to append. Maximum size is 10KB.
              Examples:
              - "User prefers TypeScript over JavaScript"
              - "The database connection string is stored in DATABASE_URL"
              - "Always run tests before committing"

    Returns:
        Success message or error description.
    """
    return append_to_memory_file(rule, client_ip="mcp-client")


# =============================================================================
# FastAPI Application
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    init_memory_file()
    logger.info("MCP Agent Memory Server started")
    logger.info(f"Memory file: {FILE_PATH}")
    logger.info(f"Rate limit: {RATE_LIMIT_REQUESTS} requests per {RATE_LIMIT_WINDOW}s")
    yield
    logger.info("MCP Agent Memory Server shutting down")


# Create main FastAPI app
app = FastAPI(
    title="MCP Agent Memory Server",
    version="1.0.0",
    description="A secure MCP server for shared agent memory",
    lifespan=lifespan
)

# Create MCP SSE app and add auth middleware
# The SSE app is the standard way to expose MCP over HTTP
mcp_sse_app = mcp.sse_app()

mcp_sse_app.add_middleware(APIKeyAuthMiddleware)

# Mount the secured MCP app
app.mount("/mcp", mcp_sse_app)


# =============================================================================
# REST Endpoints
# =============================================================================

@app.get("/")
async def root():
    """Basic root endpoint."""
    return {
        "service": "MCP Agent Memory Server",
        "status": "active",
        "mcp_endpoint": "/mcp/sse",
        "docs": "/docs"
    }


@app.get("/health")
async def health():
    """Public health check endpoint."""
    file_stats = get_file_stats()
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "memory_file": file_stats,
        "rate_limit": {
            "max_requests": RATE_LIMIT_REQUESTS,
            "window_seconds": RATE_LIMIT_WINDOW
        }
    }


@app.get("/health/secure", dependencies=[Depends(verify_api_key)])
async def health_secure():
    """Authenticated health check with configuration details."""
    file_stats = get_file_stats()
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "memory_file": file_stats,
        "config": {
            "max_rule_size": MAX_RULE_SIZE,
            "rate_limit_requests": RATE_LIMIT_REQUESTS,
            "rate_limit_window": RATE_LIMIT_WINDOW,
            "file_path": FILE_PATH,
            "audit_log_path": AUDIT_LOG_PATH
        }
    }


# =============================================================================
# REST API Endpoints (Alternative to MCP)
# =============================================================================

@app.get("/api/memory", dependencies=[Depends(verify_api_key)])
async def api_read_memory():
    """
    REST API endpoint to read memory.

    This is an alternative to the MCP read_memory tool for clients
    that don't support the MCP protocol.
    """
    content = read_memory_file()
    return {"content": content, "stats": get_file_stats()}


@app.post("/api/memory", dependencies=[Depends(verify_api_key)])
async def api_write_memory(request: Request, rule: str):
    """
    REST API endpoint to write to memory.

    This is an alternative to the MCP write_memory tool for clients
    that don't support the MCP protocol.

    Query Parameters:
        rule: The text to append to the memory file.
    """
    client_ip = request.client.host if request.client else "unknown"
    result = append_to_memory_file(rule, client_ip=client_ip)
    return {"result": result, "stats": get_file_stats()}


# =============================================================================
# Main Entry Point
# =============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
