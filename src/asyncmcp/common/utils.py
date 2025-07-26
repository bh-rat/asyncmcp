import json
import re
from typing import Any, Dict, Optional

from mcp import ErrorData, JSONRPCError
from mcp import types as types
from mcp.shared.message import SessionMessage
from mcp.shared.version import SUPPORTED_PROTOCOL_VERSIONS
from mcp.types import DEFAULT_NEGOTIATED_VERSION, INVALID_REQUEST

# Session ID validation pattern (visible ASCII characters ranging from 0x21 to 0x7E)
# Pattern ensures entire string contains only valid characters by using ^ and $ anchors
SESSION_ID_PATTERN = re.compile(r"^[\x21-\x7E]+$")


def validate_protocol_version(protocol_version: Optional[str]) -> bool:
    """Validate protocol version against supported versions."""
    if protocol_version is None:
        protocol_version = DEFAULT_NEGOTIATED_VERSION

    return protocol_version in SUPPORTED_PROTOCOL_VERSIONS


def validate_session_id(session_id: str) -> bool:
    """Validate session ID contains only visible ASCII characters."""
    return bool(SESSION_ID_PATTERN.fullmatch(session_id))


def is_initialize_request(session_message: SessionMessage) -> bool:
    """Check if message is an initialize request."""
    message_root = session_message.message.root
    return isinstance(message_root, types.JSONRPCRequest) and message_root.method == "initialize"


def create_jsonrpc_error_response(
    error_message: str, error_code: int = INVALID_REQUEST, request_id: Optional[str] = None
) -> JSONRPCError:
    """Create a standardized JSON-RPC error response."""
    return JSONRPCError(
        jsonrpc="2.0",
        id=request_id or "server-error",
        error=ErrorData(
            code=error_code,
            message=error_message,
        ),
    )


async def to_session_message(sqs_message: Dict[str, Any]) -> SessionMessage:
    """Convert SQS message to SessionMessage."""
    try:
        body = sqs_message["Body"]

        # Handle SNS notification format
        if isinstance(body, str):
            try:
                parsed_body = json.loads(body)
                if "Message" in parsed_body and "Type" in parsed_body:
                    # This is an SNS notification, extract the actual message
                    actual_message = parsed_body["Message"]
                else:
                    actual_message = body
            except json.JSONDecodeError:
                actual_message = body
        else:
            # If body is already a dict, convert to JSON string first
            actual_message = json.dumps(body)

        # Parse the JSON-RPC message
        if isinstance(actual_message, str):
            parsed = json.loads(actual_message)
        else:
            parsed = actual_message

        message = types.JSONRPCMessage.model_validate(parsed)
        return SessionMessage(message)
    except Exception as e:
        raise ValueError(f"Invalid JSON-RPC message: {e}")
