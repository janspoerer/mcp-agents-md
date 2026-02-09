"""
MCP Agent Memory Server

A secure, rate-limited MCP server for shared agent memory.
"""

__version__ = "1.0.0"
__author__ = "Your Name"

from .app import app, mcp

__all__ = ["app", "mcp", "__version__"]
