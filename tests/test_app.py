"""
Unit tests for MCP Agent Memory Server.

Run with: pytest tests/ -v
"""

import os
import sys
import tempfile
import pytest

# Add src directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

# Set required environment variables before importing app
os.environ["MCP_API_KEY"] = "test-api-key-12345"
os.environ["MEMORY_FILE_PATH"] = tempfile.mktemp(suffix=".md")
os.environ["AUDIT_LOG_PATH"] = tempfile.mktemp(suffix=".log")

from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    """Create a test client for the FastAPI app."""
    from mcp_agent_memory.app import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def api_key():
    """Return the test API key."""
    return "test-api-key-12345"


class TestPublicEndpoints:
    """Tests for public (unauthenticated) endpoints."""

    def test_root_endpoint(self, client):
        """Test the root endpoint returns service info."""
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["service"] == "MCP Agent Memory Server"
        assert data["status"] == "active"

    def test_health_endpoint(self, client):
        """Test the public health endpoint."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "timestamp" in data
        assert "memory_file" in data
        assert "rate_limit" in data

    def test_docs_endpoint(self, client):
        """Test that OpenAPI docs are available."""
        response = client.get("/docs")
        assert response.status_code == 200


class TestAuthentication:
    """Tests for authentication and authorization."""

    def test_protected_endpoint_no_key(self, client):
        """Test that protected endpoints reject requests without API key."""
        response = client.get("/api/memory")
        assert response.status_code == 403

    def test_protected_endpoint_invalid_key(self, client):
        """Test that protected endpoints reject invalid API keys."""
        response = client.get(
            "/api/memory",
            headers={"X-API-Key": "invalid-key"}
        )
        assert response.status_code == 403

    def test_protected_endpoint_valid_key(self, client, api_key):
        """Test that protected endpoints accept valid API keys."""
        response = client.get(
            "/api/memory",
            headers={"X-API-Key": api_key}
        )
        assert response.status_code == 200

    def test_secure_health_requires_auth(self, client):
        """Test that /health/secure requires authentication."""
        response = client.get("/health/secure")
        assert response.status_code == 403

    def test_secure_health_with_auth(self, client, api_key):
        """Test that /health/secure works with authentication."""
        response = client.get(
            "/health/secure",
            headers={"X-API-Key": api_key}
        )
        assert response.status_code == 200
        data = response.json()
        assert "config" in data


class TestMemoryOperations:
    """Tests for memory read/write operations."""

    def test_read_memory(self, client, api_key):
        """Test reading the memory file."""
        response = client.get(
            "/api/memory",
            headers={"X-API-Key": api_key}
        )
        assert response.status_code == 200
        data = response.json()
        assert "content" in data
        assert "stats" in data

    def test_write_memory(self, client, api_key):
        """Test writing to the memory file."""
        test_rule = "Test rule from pytest"
        response = client.post(
            "/api/memory",
            params={"rule": test_rule},
            headers={"X-API-Key": api_key}
        )
        assert response.status_code == 200
        data = response.json()
        assert "Successfully" in data["result"]

    def test_write_then_read(self, client, api_key):
        """Test that written content appears in subsequent reads."""
        unique_rule = f"Unique test rule {os.urandom(4).hex()}"

        # Write
        write_response = client.post(
            "/api/memory",
            params={"rule": unique_rule},
            headers={"X-API-Key": api_key}
        )
        assert write_response.status_code == 200

        # Read
        read_response = client.get(
            "/api/memory",
            headers={"X-API-Key": api_key}
        )
        assert read_response.status_code == 200
        assert unique_rule in read_response.json()["content"]

    def test_write_empty_rule_rejected(self, client, api_key):
        """Test that empty rules are rejected."""
        response = client.post(
            "/api/memory",
            params={"rule": ""},
            headers={"X-API-Key": api_key}
        )
        assert response.status_code == 200
        assert "Error" in response.json()["result"]

    def test_write_whitespace_only_rejected(self, client, api_key):
        """Test that whitespace-only rules are rejected."""
        response = client.post(
            "/api/memory",
            params={"rule": "   \n\t  "},
            headers={"X-API-Key": api_key}
        )
        assert response.status_code == 200
        assert "Error" in response.json()["result"]


class TestInputValidation:
    """Tests for input validation and size limits."""

    def test_oversized_rule_rejected(self, client, api_key):
        """Test that rules exceeding MAX_RULE_SIZE are rejected."""
        # Create a rule larger than 10KB (default limit)
        large_rule = "x" * 15000
        response = client.post(
            "/api/memory",
            params={"rule": large_rule},
            headers={"X-API-Key": api_key}
        )
        assert response.status_code == 200
        assert "Error" in response.json()["result"]
        assert "size" in response.json()["result"].lower()

    def test_rule_at_limit_accepted(self, client, api_key):
        """Test that rules at exactly MAX_RULE_SIZE are accepted."""
        # Create a rule at exactly 10KB
        rule_at_limit = "x" * 10000
        response = client.post(
            "/api/memory",
            params={"rule": rule_at_limit},
            headers={"X-API-Key": api_key}
        )
        assert response.status_code == 200
        assert "Successfully" in response.json()["result"]


class TestHealthEndpoints:
    """Tests for health check endpoints."""

    def test_health_returns_file_stats(self, client):
        """Test that health endpoint returns memory file stats."""
        response = client.get("/health")
        data = response.json()
        assert "memory_file" in data
        file_stats = data["memory_file"]
        assert "exists" in file_stats
        assert "size_bytes" in file_stats

    def test_secure_health_returns_config(self, client, api_key):
        """Test that secure health endpoint returns configuration."""
        response = client.get(
            "/health/secure",
            headers={"X-API-Key": api_key}
        )
        data = response.json()
        assert "config" in data
        config = data["config"]
        assert "max_rule_size" in config
        assert "rate_limit_requests" in config


class TestMCPEndpoint:
    """Tests for the MCP SSE endpoint."""

    def test_mcp_endpoint_exists(self, client):
        """Test that the MCP endpoint is mounted."""
        # MCP endpoint should exist but may require auth
        response = client.get("/mcp/sse")
        # Should get 403 (auth required) not 404
        assert response.status_code != 404

    def test_mcp_endpoint_requires_auth(self, client):
        """Test that MCP endpoint requires authentication."""
        response = client.get("/mcp/sse")
        # Should be rejected without API key
        assert response.status_code in [403, 401]


# =============================================================================
# File Operation Tests (Unit Tests)
# =============================================================================

class TestFileOperations:
    """Unit tests for file operation functions."""

    def test_read_memory_file_function(self):
        """Test the read_memory_file function directly."""
        from mcp_agent_memory.app import read_memory_file
        content = read_memory_file()
        assert isinstance(content, str)
        # Should contain the header from initialization
        assert "Agent Memory" in content or "Error" not in content

    def test_append_to_memory_file_function(self):
        """Test the append_to_memory_file function directly."""
        from mcp_agent_memory.app import append_to_memory_file
        result = append_to_memory_file("Direct function test", client_ip="test")
        assert "Successfully" in result

    def test_get_file_stats_function(self):
        """Test the get_file_stats function directly."""
        from mcp_agent_memory.app import get_file_stats
        stats = get_file_stats()
        assert isinstance(stats, dict)
        assert "exists" in stats
        if stats["exists"]:
            assert "size_bytes" in stats
            assert "line_count" in stats


# =============================================================================
# Cleanup
# =============================================================================

@pytest.fixture(scope="session", autouse=True)
def cleanup(request):
    """Cleanup temporary files after tests."""
    def remove_temp_files():
        temp_memory = os.environ.get("MEMORY_FILE_PATH")
        temp_audit = os.environ.get("AUDIT_LOG_PATH")
        for f in [temp_memory, temp_audit]:
            if f and os.path.exists(f):
                try:
                    os.remove(f)
                except Exception:
                    pass

    request.addfinalizer(remove_temp_files)
