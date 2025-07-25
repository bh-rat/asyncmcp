"""
Webhook transport for AsyncMCP.

This module provides webhook-based transport for MCP (Model Context Protocol).
The client sends HTTP POST requests to the server and receives responses via webhooks.
"""

from .client import webhook_client
from .server import WebhookTransport, webhook_server
from .manager import WebhookSessionManager
from .utils import (
    WebhookTransportConfig,
    SessionInfo,
    create_http_headers,
    parse_webhook_request,
    send_webhook_response,
    extract_webhook_url_from_meta,
    generate_session_id,
)

__all__ = [
    # Client
    "webhook_client",
    # Server
    "WebhookTransport",
    "webhook_server",
    # Manager
    "WebhookSessionManager",
    # Utilities
    "WebhookTransportConfig",
    "SessionInfo",
    "create_http_headers",
    "parse_webhook_request",
    "send_webhook_response",
    "extract_webhook_url_from_meta",
    "generate_session_id",
]
