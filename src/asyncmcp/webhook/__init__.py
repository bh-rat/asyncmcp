"""
Webhook transport for AsyncMCP.

This module provides webhook-based transport for MCP (Model Context Protocol).
The client sends HTTP POST requests to the server and receives responses via webhooks.
"""

from asyncmcp.webhook.client import webhook_client
from asyncmcp.webhook.manager import WebhookSessionManager
from asyncmcp.webhook.server import WebhookTransport, webhook_server
from asyncmcp.webhook.utils import (
    SessionInfo,
    WebhookClientConfig,
    WebhookServerConfig,
    create_http_headers,
    extract_webhook_url_from_meta,
    generate_session_id,
    parse_webhook_request,
    send_webhook_response,
)

__all__ = [
    # Client
    "webhook_client",
    # Server
    "WebhookTransport",
    "webhook_server",
    # Manager
    "WebhookSessionManager",
    # Configuration
    "WebhookServerConfig",
    "WebhookClientConfig",
    # Utilities
    "SessionInfo",
    "create_http_headers",
    "parse_webhook_request",
    "send_webhook_response",
    "extract_webhook_url_from_meta",
    "generate_session_id",
]
