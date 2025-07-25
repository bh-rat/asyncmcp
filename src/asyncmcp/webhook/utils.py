"""
Webhook transport utilities and configuration.
"""

import logging
import uuid
from dataclasses import dataclass
from typing import Dict, Optional

import httpx
import orjson
from pydantic_core import ValidationError

import mcp.types as types
from mcp.shared.message import SessionMessage

logger = logging.getLogger(__name__)


@dataclass
class WebhookTransportConfig:
    """Configuration for webhook transport."""

    server_url: str = "http://0.0.0.0:8000/mcp/request"
    webhook_url: str = "http://0.0.0.0:8001/webhook/response"

    timeout_seconds: float = 30.0
    max_retries: int = 0

    client_id: Optional[str] = None
    poll_interval_seconds: float = 1.0
    transport_timeout_seconds: Optional[float] = None

    def __post_init__(self):
        """Initialize client_id if not provided."""
        if self.client_id is None:
            self.client_id = str(uuid.uuid4())


@dataclass
class SessionInfo:
    """Information about a client session."""

    session_id: str
    client_id: str
    webhook_url: str
    state: str  # "init_pending", "initialized", "closed"


async def create_http_headers(
    session_message: SessionMessage,
    session_id: Optional[str] = None,
    client_id: Optional[str] = None,
) -> Dict[str, str]:
    """Create HTTP headers for webhook transport."""
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "asyncmcp-webhook/1.0",
    }

    if client_id:
        headers["X-Client-ID"] = client_id

    if session_id:
        headers["X-Session-ID"] = session_id

    message_root = session_message.message.root
    if isinstance(message_root, types.JSONRPCRequest):
        headers["X-Request-ID"] = str(message_root.id)
        headers["X-Method"] = message_root.method
    elif isinstance(message_root, types.JSONRPCNotification):
        headers["X-Method"] = message_root.method

    return headers


async def parse_webhook_request(request_body: bytes) -> SessionMessage:
    """Parse a webhook request body into a SessionMessage."""
    try:
        parsed_body = orjson.loads(request_body)
        jsonrpc_message = types.JSONRPCMessage.model_validate(parsed_body)
        return SessionMessage(jsonrpc_message)
    except (orjson.JSONDecodeError, ValidationError) as e:
        logger.error(f"Failed to parse webhook request: {e}")
        raise


async def send_webhook_response(
    http_client: httpx.AsyncClient,
    webhook_url: str,
    session_message: SessionMessage,
    session_id: Optional[str],
    client_id: Optional[str],
) -> None:
    """Send a response via webhook."""
    try:
        headers = await create_http_headers(session_message, session_id, client_id)
        # Ensure X-Session-ID is always set
        if session_id:
            headers["X-Session-ID"] = session_id
        json_body = session_message.message.model_dump_json(by_alias=True, exclude_none=True)

        response = await http_client.post(
            webhook_url,
            headers=headers,
            content=json_body,
        )
        response.raise_for_status()
        logger.debug(f"Webhook response sent successfully to {webhook_url}")

    except httpx.HTTPError as e:
        logger.error(f"Failed to send webhook response to {webhook_url}: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error sending webhook response: {e}")
        raise


def extract_webhook_url_from_meta(message: types.JSONRPCMessage) -> Optional[str]:
    """Extract webhook URL from the _meta field of an MCP message."""
    if not isinstance(message.root, types.JSONRPCRequest):
        return None

    params = message.root.params
    if not isinstance(params, dict):
        return None

    meta = params.get("_meta")
    if not isinstance(meta, dict):
        return None

    return meta.get("webhookUrl")


def generate_session_id() -> str:
    """Generate a unique session ID."""
    return str(uuid.uuid4())
