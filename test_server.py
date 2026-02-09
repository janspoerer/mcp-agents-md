#!/usr/bin/env python3
"""
Test script for MCP Agent Memory Server.
Run this to verify your server is working correctly.

Usage:
    # Test against local server
    python test_server.py

    # Test against remote server
    python test_server.py https://mcp.yourdomain.com
"""

import os
import sys
import json
import asyncio
from datetime import datetime

# Try to import httpx for async HTTP, fall back to requests
try:
    import httpx
    USE_HTTPX = True
except ImportError:
    import requests
    USE_HTTPX = False

from dotenv import load_dotenv

load_dotenv()

# =============================================================================
# Configuration
# =============================================================================

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
API_KEY = os.getenv("MCP_API_KEY", "test-key")


# =============================================================================
# Test Functions
# =============================================================================

def print_result(test_name: str, success: bool, message: str = ""):
    """Print test result with color coding."""
    status = "✓ PASS" if success else "✗ FAIL"
    color = "\033[92m" if success else "\033[91m"
    reset = "\033[0m"
    print(f"{color}{status}{reset} - {test_name}")
    if message:
        print(f"       {message}")


def test_health(base_url: str) -> bool:
    """Test the public health endpoint."""
    url = f"{base_url}/health"
    try:
        if USE_HTTPX:
            with httpx.Client() as client:
                response = client.get(url)
        else:
            response = requests.get(url)

        data = response.json()
        success = response.status_code == 200 and data.get("status") == "healthy"
        print_result(
            "Health endpoint",
            success,
            f"Status: {data.get('status')}, File exists: {data.get('memory_file', {}).get('exists')}"
        )
        return success
    except Exception as e:
        print_result("Health endpoint", False, str(e))
        return False


def test_auth_required(base_url: str) -> bool:
    """Test that auth is required for protected endpoints."""
    url = f"{base_url}/api/memory"
    try:
        if USE_HTTPX:
            with httpx.Client() as client:
                response = client.get(url)
        else:
            response = requests.get(url)

        success = response.status_code == 403
        print_result(
            "Auth required (no key)",
            success,
            f"Got {response.status_code} (expected 403)"
        )
        return success
    except Exception as e:
        print_result("Auth required", False, str(e))
        return False


def test_auth_invalid(base_url: str) -> bool:
    """Test that invalid keys are rejected."""
    url = f"{base_url}/api/memory"
    headers = {"X-API-Key": "invalid-key-12345"}
    try:
        if USE_HTTPX:
            with httpx.Client() as client:
                response = client.get(url, headers=headers)
        else:
            response = requests.get(url, headers=headers)

        success = response.status_code == 403
        print_result(
            "Auth rejected (invalid key)",
            success,
            f"Got {response.status_code} (expected 403)"
        )
        return success
    except Exception as e:
        print_result("Auth rejected", False, str(e))
        return False


def test_read_memory(base_url: str, api_key: str) -> bool:
    """Test reading memory via REST API."""
    url = f"{base_url}/api/memory"
    headers = {"X-API-Key": api_key}
    try:
        if USE_HTTPX:
            with httpx.Client() as client:
                response = client.get(url, headers=headers)
        else:
            response = requests.get(url, headers=headers)

        data = response.json()
        success = response.status_code == 200 and "content" in data
        content_preview = data.get("content", "")[:100]
        print_result(
            "Read memory (REST)",
            success,
            f"Content preview: {content_preview}..."
        )
        return success
    except Exception as e:
        print_result("Read memory", False, str(e))
        return False


def test_write_memory(base_url: str, api_key: str) -> bool:
    """Test writing to memory via REST API."""
    url = f"{base_url}/api/memory"
    headers = {"X-API-Key": api_key}
    test_rule = f"Test rule from test_server.py at {datetime.now().isoformat()}"
    params = {"rule": test_rule}

    try:
        if USE_HTTPX:
            with httpx.Client() as client:
                response = client.post(url, headers=headers, params=params)
        else:
            response = requests.post(url, headers=headers, params=params)

        data = response.json()
        success = response.status_code == 200 and "Successfully" in data.get("result", "")
        print_result(
            "Write memory (REST)",
            success,
            f"Result: {data.get('result')}"
        )
        return success
    except Exception as e:
        print_result("Write memory", False, str(e))
        return False


def test_secure_health(base_url: str, api_key: str) -> bool:
    """Test the authenticated health endpoint."""
    url = f"{base_url}/health/secure"
    headers = {"X-API-Key": api_key}
    try:
        if USE_HTTPX:
            with httpx.Client() as client:
                response = client.get(url, headers=headers)
        else:
            response = requests.get(url, headers=headers)

        data = response.json()
        success = response.status_code == 200 and "config" in data
        print_result(
            "Secure health endpoint",
            success,
            f"Max rule size: {data.get('config', {}).get('max_rule_size')} bytes"
        )
        return success
    except Exception as e:
        print_result("Secure health", False, str(e))
        return False


def test_rule_size_limit(base_url: str, api_key: str) -> bool:
    """Test that oversized rules are rejected."""
    url = f"{base_url}/api/memory"
    headers = {"X-API-Key": api_key}
    # Create a rule larger than 10KB
    large_rule = "x" * 15000
    params = {"rule": large_rule}

    try:
        if USE_HTTPX:
            with httpx.Client() as client:
                response = client.post(url, headers=headers, params=params)
        else:
            response = requests.post(url, headers=headers, params=params)

        data = response.json()
        success = "Error" in data.get("result", "") and "size" in data.get("result", "").lower()
        print_result(
            "Rule size limit",
            success,
            f"Result: {data.get('result', '')[:80]}"
        )
        return success
    except Exception as e:
        print_result("Rule size limit", False, str(e))
        return False


def test_mcp_endpoint(base_url: str) -> bool:
    """Test that MCP endpoint is available (basic check)."""
    # MCP uses SSE, so we just check if the endpoint exists
    url = f"{base_url}/mcp"
    try:
        if USE_HTTPX:
            with httpx.Client() as client:
                response = client.get(url, follow_redirects=True)
        else:
            response = requests.get(url, allow_redirects=True)

        # MCP endpoint might return various status codes depending on implementation
        # We just check it's not a 404
        success = response.status_code != 404
        print_result(
            "MCP endpoint exists",
            success,
            f"Status: {response.status_code}"
        )
        return success
    except Exception as e:
        print_result("MCP endpoint", False, str(e))
        return False


# =============================================================================
# Main
# =============================================================================

def run_tests(base_url: str, api_key: str):
    """Run all tests."""
    print("\n" + "=" * 60)
    print("MCP Agent Memory Server - Test Suite")
    print("=" * 60)
    print(f"Target: {base_url}")
    print(f"API Key: {api_key[:8]}..." if len(api_key) > 8 else f"API Key: {api_key}")
    print("-" * 60)

    results = []

    # Public endpoint tests
    print("\n[Public Endpoints]")
    results.append(test_health(base_url))
    results.append(test_mcp_endpoint(base_url))

    # Auth tests
    print("\n[Authentication]")
    results.append(test_auth_required(base_url))
    results.append(test_auth_invalid(base_url))

    # Authenticated tests
    print("\n[Authenticated Operations]")
    results.append(test_secure_health(base_url, api_key))
    results.append(test_read_memory(base_url, api_key))
    results.append(test_write_memory(base_url, api_key))

    # Validation tests
    print("\n[Input Validation]")
    results.append(test_rule_size_limit(base_url, api_key))

    # Summary
    print("\n" + "-" * 60)
    passed = sum(results)
    total = len(results)
    print(f"Results: {passed}/{total} tests passed")

    if passed == total:
        print("\033[92m✓ All tests passed!\033[0m")
        return 0
    else:
        print(f"\033[91m✗ {total - passed} test(s) failed\033[0m")
        return 1


if __name__ == "__main__":
    base_url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BASE_URL
    api_key = sys.argv[2] if len(sys.argv) > 2 else API_KEY

    if not api_key or api_key == "test-key":
        print("Warning: Using default test API key. Set MCP_API_KEY in .env")

    sys.exit(run_tests(base_url, api_key))
