"""FastMCP integration for asyncmcp.

This module provides integration between FastMCP servers and asyncmcp's custom
transport layers (SQS, SNS+SQS, Webhook, etc.), allowing FastMCP servers to
communicate over async transports.
"""

from asyncmcp.fastmcp.client import create_fastmcp_client
from asyncmcp.fastmcp.server import run_fastmcp_server
from asyncmcp.fastmcp.transport import AsyncMCPTransport

__all__ = [
    "AsyncMCPTransport",
    "create_fastmcp_client",
    "run_fastmcp_server",
]
