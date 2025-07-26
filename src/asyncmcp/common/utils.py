import json
import re
from typing import Any, Dict, Optional, Tuple

from mcp import ErrorData, JSONRPCError
from mcp import types as types
from mcp.shared.message import SessionMessage
from mcp.shared.version import SUPPORTED_PROTOCOL_VERSIONS
from mcp.types import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    PARSE_ERROR,
)
from pydantic import ValidationError

# Session ID validation pattern (visible ASCII characters ranging from 0x21 to 0x7E)
# Pattern ensures entire string contains only valid characters by using ^ and $ anchors
SESSION_ID_PATTERN = re.compile(r"^[\x21-\x7E]+$")


def validate_protocol_version(protocol_version: Optional[str]) -> bool:
    """Validate protocol version against supported versions."""
    if protocol_version is None:
        # this signifies default version
        return True

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


def create_parse_error_response(error_message: str = "Parse error", request_id: Optional[str] = None) -> JSONRPCError:
    """Create a parse error response (-32700)."""
    return create_jsonrpc_error_response(error_message, PARSE_ERROR, request_id)


def create_invalid_request_error_response(
    error_message: str = "Invalid Request", request_id: Optional[str] = None
) -> JSONRPCError:
    """Create an invalid request error response (-32600)."""
    return create_jsonrpc_error_response(error_message, INVALID_REQUEST, request_id)


def create_invalid_params_error_response(
    error_message: str = "Invalid params", request_id: Optional[str] = None
) -> JSONRPCError:
    """Create an invalid params error response (-32602)."""
    return create_jsonrpc_error_response(error_message, INVALID_PARAMS, request_id)


def create_internal_error_response(
    error_message: str = "Internal error", request_id: Optional[str] = None
) -> JSONRPCError:
    """Create an internal error response (-32603)."""
    return create_jsonrpc_error_response(error_message, INTERNAL_ERROR, request_id)


def create_session_not_found_error_response(
    session_id: Optional[str] = None, request_id: Optional[str] = None
) -> JSONRPCError:
    """Create a session not found error response."""
    message = f"Session not found: {session_id}" if session_id else "Session not found"
    return create_invalid_request_error_response(message, request_id)


def create_session_terminated_error_response(
    session_id: Optional[str] = None, request_id: Optional[str] = None
) -> JSONRPCError:
    """Create a session terminated error response."""
    message = f"Session has been terminated: {session_id}" if session_id else "Session has been terminated"
    return create_invalid_request_error_response(message, request_id)


def create_protocol_version_error_response(
    protocol_version: Optional[str] = None, request_id: Optional[str] = None
) -> JSONRPCError:
    """Create a protocol version error response."""
    supported_versions = ", ".join(SUPPORTED_PROTOCOL_VERSIONS)
    if protocol_version:
        message = f"Unsupported protocol version: {protocol_version}. Supported versions: {supported_versions}"
    else:
        message = f"Missing protocol version. Supported versions: {supported_versions}"
    return create_invalid_request_error_response(message, request_id)


def create_session_id_error_response(
    session_id: Optional[str] = None, request_id: Optional[str] = None
) -> JSONRPCError:
    """Create a session ID validation error response."""
    if session_id:
        message = f"Invalid session ID format: {session_id}"
    else:
        message = "Missing or invalid session ID"
    return create_invalid_request_error_response(message, request_id)


def validate_and_parse_message(message_body: str) -> Tuple[Optional[SessionMessage], Optional[JSONRPCError]]:
    """Validate and parse a message body into a SessionMessage."""
    try:
        try:
            raw_message = json.loads(message_body)
        except json.JSONDecodeError as e:
            return None, create_parse_error_response(f"Parse error: {str(e)}")

        try:
            message = types.JSONRPCMessage.model_validate(raw_message)
            return SessionMessage(message), None
        except ValidationError as e:
            return None, create_invalid_params_error_response(f"Validation error: {str(e)}")

    except Exception as e:
        return None, create_internal_error_response(f"Unexpected error: {str(e)}")


def validate_message_attributes(
    message_attrs: Dict[str, Any], require_session_id: bool = False, existing_session_id: Optional[str] = None
) -> Optional[JSONRPCError]:
    """Validate message attributes (protocol version, session ID)."""
    protocol_version = None
    if "ProtocolVersion" in message_attrs:
        protocol_version = message_attrs["ProtocolVersion"]["StringValue"]

    if not validate_protocol_version(protocol_version):
        return create_protocol_version_error_response(protocol_version)

    session_id = None
    if "SessionId" in message_attrs:
        session_id = message_attrs["SessionId"]["StringValue"]

    if require_session_id and not session_id:
        return create_session_id_error_response()

    if session_id and not validate_session_id(session_id):
        return create_session_id_error_response(session_id)

    if existing_session_id and session_id and session_id != existing_session_id:
        return create_session_not_found_error_response(session_id)

    return None


async def to_session_message(sqs_message: Dict[str, Any]) -> SessionMessage:
    """Convert SQS message to SessionMessage with proper error handling."""
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
            actual_message = json.dumps(body)

        session_message, error = validate_and_parse_message(actual_message)
        if error:
            raise ValueError(f"Invalid JSON-RPC message: {error.error.message}")

        if session_message is None:
            raise ValueError("Failed to parse session message")

        return session_message
    except Exception as e:
        raise ValueError(f"Invalid JSON-RPC message: {e}")
