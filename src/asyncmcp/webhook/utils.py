"""
Webhook transport utilities and configuration.
"""

import logging
import uuid
from dataclasses import dataclass

import httpx
import mcp.types as types
import orjson
from mcp.shared.message import SessionMessage
from pydantic_core import ValidationError

logger = logging.getLogger(__name__)


@dataclass
class WebhookServerConfig:
    """Configuration for webhook server transport."""

    # HTTP client configuration for sending webhooks to clients
    timeout_seconds: float = 30.0
    max_retries: int = 0  # No retries as specified

    # Transport configuration
    transport_timeout_seconds: float | None = None


@dataclass
class WebhookClientConfig:
    """Configuration for webhook client transport."""

    # Server URL where the client sends requests
    server_url: str = "http://localhost:8000/mcp/request"

    # HTTP client configuration
    timeout_seconds: float = 30.0
    max_retries: int = 0  # No retries as specified

    # Transport configuration
    client_id: str | None = None
    transport_timeout_seconds: float | None = None

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
    session_id: str | None = None,
    client_id: str | None = None,
) -> dict[str, str]:
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
    session_id: str | None,
    client_id: str | None,
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


def extract_webhook_url_from_meta(message: types.JSONRPCMessage) -> str | None:
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
