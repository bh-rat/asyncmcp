"""
Base client transport functionality for MCP protocol compliance.

This module provides a base class that implements common MCP protocol features
like session management, protocol version handling, and JSON-RPC error responses,
following patterns from the official StreamableHTTP client.
"""

import logging
from typing import Optional

from mcp.shared.message import SessionMessage
from mcp.types import (
    ErrorData,
    InitializeResult,
    JSONRPCError,
    JSONRPCMessage,
    JSONRPCNotification,
    JSONRPCRequest,
    JSONRPCResponse,
    RequestId,
)

from asyncmcp.common.client_state import ClientState

logger = logging.getLogger(__name__)


class BaseClientTransport:
    """Base client transport with MCP protocol compliance features."""

    def __init__(self, client_state: ClientState):
        self.client_state = client_state
        self.protocol_version: Optional[str] = None

    def _is_initialization_request(self, message: JSONRPCMessage) -> bool:
        """Check if the message is an initialization request."""
        return isinstance(message.root, JSONRPCRequest) and message.root.method == "initialize"

    def _is_initialized_notification(self, message: JSONRPCMessage) -> bool:
        """Check if the message is an initialized notification."""
        return isinstance(message.root, JSONRPCNotification) and message.root.method == "notifications/initialized"

    def _is_initialize_response(self, message: JSONRPCMessage) -> bool:
        """Check if the message is an initialize response."""
        return (
            isinstance(message.root, JSONRPCResponse)
            and message.root.result is not None
            and isinstance(message.root.result, dict)
            and "protocolVersion" in message.root.result
        )

    def _maybe_extract_protocol_version_from_message(self, message: JSONRPCMessage) -> None:
        """Extract protocol version from initialization response message."""
        if isinstance(message.root, JSONRPCResponse) and message.root.result:
            try:
                # Parse the result as InitializeResult for type safety
                init_result = InitializeResult.model_validate(message.root.result)
                self.protocol_version = str(init_result.protocolVersion)
            except Exception as exc:
                logger.warning(f"Failed to parse initialization response as InitializeResult: {exc}")
                logger.warning(f"Raw result: {message.root.result}")

    async def _maybe_extract_session_id_from_initialize_response(
        self, message: JSONRPCMessage, session_id_source: Optional[str] = None
    ) -> None:
        """Extract and store session ID from initialize response only."""
        if self._is_initialize_response(message) and session_id_source:
            await self.client_state.set_session_id_if_none(session_id_source)

    def _create_jsonrpc_error_response(
        self, request_id: RequestId, error_code: int, error_message: str
    ) -> SessionMessage:
        """Create a JSON-RPC error response following MCP specification."""
        jsonrpc_error = JSONRPCError(
            jsonrpc="2.0",
            id=request_id,
            error=ErrorData(code=error_code, message=error_message),
        )
        return SessionMessage(JSONRPCMessage(jsonrpc_error))

    def _create_session_terminated_error(self, request_id: RequestId) -> SessionMessage:
        """Create a session terminated error response."""
        return self._create_jsonrpc_error_response(
            request_id=request_id,
            error_code=32600,  # Invalid Request
            error_message="Session terminated",
        )

    def _create_transport_error(self, request_id: RequestId, error_message: str) -> SessionMessage:
        """Create a transport-level error response."""
        return self._create_jsonrpc_error_response(
            request_id=request_id,
            error_code=32603,  # Internal Error
            error_message=f"Transport error: {error_message}",
        )

    async def handle_received_message(self, message: JSONRPCMessage, session_id_source: Optional[str] = None) -> None:
        """Handle a received message for session management and protocol version extraction."""
        if self._is_initialize_response(message):
            self._maybe_extract_protocol_version_from_message(message)
            await self._maybe_extract_session_id_from_initialize_response(message, session_id_source)

    def get_protocol_version(self) -> Optional[str]:
        """Get the negotiated protocol version."""
        return self.protocol_version

    def get_session_id(self) -> Optional[str]:
        """Get the current session ID."""
        return self.client_state.session_id

    def get_client_id(self) -> str:
        """Get the client ID."""
        return self.client_state.client_id
