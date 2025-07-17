"""
Webhook transport layer for MCP

This module provides webhook-based transport for MCP clients and servers.
The client sends HTTP POST requests to the server and receives responses via webhooks.
"""

from .utils import WebhookTransportConfig
from .client import webhook_client
from .server import webhook_server

__all__ = [
    "WebhookTransportConfig",
    "webhook_client",
    "webhook_server",
] 