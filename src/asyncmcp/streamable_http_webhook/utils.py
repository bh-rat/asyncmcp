"""
Configuration and utilities for Streamable HTTP + Webhook transport.

Provides configuration classes, the @webhook_tool decorator, and utility functions
for HTTP request/response handling with MCP compatibility.
"""

import logging
import re
import uuid
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any, Dict, Literal, Optional, Set

import anyio
import httpx
import mcp.types as types
import orjson
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.message import SessionMessage
from mcp.types import ErrorData, JSONRPCError, JSONRPCMessage
from pydantic_core import ValidationError
from starlette.responses import Response

logger = logging.getLogger(__name__)

# Header names (compatible with MCP StreamableHTTP)
MCP_SESSION_ID_HEADER = "mcp-session-id"
MCP_PROTOCOL_VERSION_HEADER = "mcp-protocol-version"
LAST_EVENT_ID_HEADER = "last-event-id"

# Content types
CONTENT_TYPE_JSON = "application/json"
CONTENT_TYPE_SSE = "text/event-stream"

# Session ID validation pattern (MCP compatible)
SESSION_ID_PATTERN = re.compile(r"^[\x21-\x7E]+$")

# Type aliases
StreamId = str
EventId = str
ResponseType = Literal["webhook", "sse", "json"]


@dataclass
class StreamableHTTPWebhookConfig:
    """Configuration for StreamableHTTP + Webhook server transport."""

    json_response: bool = False
    timeout_seconds: float = 30.0
    transport_timeout_seconds: Optional[float] = None

    webhook_timeout: float = 30.0
    webhook_max_retries: int = 1

    security_settings: Optional[TransportSecuritySettings] = None

    # TODO : event store support (MCP resumability)
    # event_store: Optional[EventStore] = None


@dataclass
class StreamableHTTPWebhookClientConfig:
    """Configuration for StreamableHTTP + Webhook client transport."""

    # Server connection
    server_url: str = "http://localhost:8000/mcp"
    webhook_url: str = "http://localhost:8001/webhook"

    # HTTP client configuration
    timeout_seconds: float = 30.0
    max_retries: int = 1

    # Transport configuration
    client_id: Optional[str] = None
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
    webhook_tools: Set[str]
    state: str  # "init_pending", "initialized", "closed"


def webhook_tool(description: Optional[str] = None, tool_name: Optional[str] = None, **metadata: Any):
    """
    Decorator to mark a tool for webhook delivery.

    Tools marked with this decorator will have their results delivered
    via HTTP POST to the client's webhook URL instead of through the
    standard SSE stream.

    Args:
        description: Optional description of why this tool uses webhooks
        tool_name: Optional tool name (defaults to function name)
        **metadata: Additional metadata for the tool-webhook association

    Example:
        @webhook_tool(description="Long-running analysis", tool_name="analyze_async")
        async def analyze_website_async(url: str):
            # This tool's result will be delivered via webhook
            return await perform_analysis(url)
    """

    def decorator(func):
        func._webhook_tool = True
        func._webhook_description = description
        func._webhook_tool_name = tool_name or func.__name__
        func._webhook_metadata = metadata
        return func

    return decorator


def is_webhook_tool(func) -> bool:
    """Check if a function is marked as a webhook tool."""
    return getattr(func, "_webhook_tool", False)


def get_webhook_tool_info(func) -> Dict[str, Any]:
    """Get webhook tool metadata from a decorated function."""
    if not is_webhook_tool(func):
        return {}

    return {
        "description": getattr(func, "_webhook_description", None),
        "metadata": getattr(func, "_webhook_metadata", {}),
    }


def validate_session_id(session_id: Optional[str]) -> bool:
    """Validate session ID format (MCP compatible)."""
    if session_id is None:
        return False
    return SESSION_ID_PATTERN.fullmatch(session_id) is not None


def extract_webhook_url_from_meta(message: JSONRPCMessage) -> Optional[str]:
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


def create_http_headers(
    session_message: SessionMessage,
    session_id: Optional[str] = None,
    client_id: Optional[str] = None,
) -> Dict[str, str]:
    """Create HTTP headers for webhook delivery (compatible with webhook transport)."""
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "asyncmcp-streamable-http-webhook/1.0",
    }

    if client_id:
        headers["X-Client-ID"] = client_id

    if session_id:
        headers[MCP_SESSION_ID_HEADER] = session_id

    message_root = session_message.message.root
    if isinstance(message_root, types.JSONRPCRequest):
        headers["X-Request-ID"] = str(message_root.id)
        headers["X-Method"] = message_root.method
    elif isinstance(message_root, types.JSONRPCNotification):
        headers["X-Method"] = message_root.method

    return headers


async def send_webhook_response(
    http_client: httpx.AsyncClient,
    webhook_url: str,
    session_message: SessionMessage,
    session_id: Optional[str],
    client_id: Optional[str],
    max_retries: int = 0,
) -> None:
    """Send a response via webhook with retry logic."""
    headers = create_http_headers(session_message, session_id, client_id)
    json_body = session_message.message.model_dump_json(by_alias=True, exclude_none=True)

    last_error = None
    for attempt in range(max_retries + 1):
        try:
            response = await http_client.post(
                webhook_url,
                headers=headers,
                content=json_body,
            )
            response.raise_for_status()
            return

        except httpx.HTTPError as e:
            last_error = e
            if attempt < max_retries:
                logger.warning(f"Webhook delivery attempt {attempt + 1} failed to {webhook_url}: {e}, retrying...")
                await anyio.sleep(0.1 * (attempt + 1))  # Brief exponential backoff
            else:
                logger.error(f"Failed to send webhook response to {webhook_url} after {max_retries + 1} attempts: {e}")
        except Exception as e:
            logger.error(f"Unexpected error sending webhook response: {e}")
            raise

    # If we get here, all retries failed
    if last_error:
        raise last_error


def parse_webhook_request(request_body: bytes) -> SessionMessage:
    """Parse a webhook request body into a SessionMessage."""
    try:
        parsed_body = orjson.loads(request_body)
        jsonrpc_message = types.JSONRPCMessage.model_validate(parsed_body)
        return SessionMessage(jsonrpc_message)
    except (orjson.JSONDecodeError, ValidationError) as e:
        logger.error(f"Failed to parse webhook request: {e}")
        raise


def create_error_response(
    error_message: str,
    status_code: HTTPStatus,
    error_code: int = types.INVALID_REQUEST,
    session_id: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
) -> Response:
    """Create an error response with proper MCP formatting."""
    response_headers = {"Content-Type": CONTENT_TYPE_JSON}
    if headers:
        response_headers.update(headers)

    if session_id:
        response_headers[MCP_SESSION_ID_HEADER] = session_id

    # Return a properly formatted JSON error response
    error_response = JSONRPCError(
        jsonrpc="2.0",
        id="server-error",  # We don't have a request ID for general errors
        error=ErrorData(
            code=error_code,
            message=error_message,
        ),
    )

    return Response(
        error_response.model_dump_json(by_alias=True, exclude_none=True),
        status_code=status_code,
        headers=response_headers,
    )


def create_json_response(
    response_message: Optional[JSONRPCMessage],
    session_id: Optional[str] = None,
    status_code: HTTPStatus = HTTPStatus.OK,
    headers: Optional[Dict[str, str]] = None,
) -> Response:
    """Create a JSON response from a JSONRPCMessage."""
    response_headers = {"Content-Type": CONTENT_TYPE_JSON}
    if headers:
        response_headers.update(headers)

    if session_id:
        response_headers[MCP_SESSION_ID_HEADER] = session_id

    return Response(
        response_message.model_dump_json(by_alias=True, exclude_none=True) if response_message else None,
        status_code=status_code,
        headers=response_headers,
    )


def generate_session_id() -> str:
    """Generate a unique session ID."""
    return str(uuid.uuid4())


def create_event_data(event_message: "EventMessage") -> Dict[str, str]:
    """Create event data dictionary from an EventMessage for SSE."""
    event_data = {
        "event": "message",
        "data": event_message.message.model_dump_json(by_alias=True, exclude_none=True),
    }

    # If an event ID was provided, include it
    if event_message.event_id:
        event_data["id"] = event_message.event_id

    return event_data


# Event message class for SSE streaming
@dataclass
class EventMessage:
    """A JSONRPCMessage with an optional event ID for stream resumability."""

    message: JSONRPCMessage
    event_id: Optional[str] = None
