import json
import logging
from typing import Any, Dict, Optional, Protocol

from starlette.responses import Response

logger = logging.getLogger(__name__)


class SessionManagerProtocol(Protocol):
    """Protocol for session manager."""

    async def send_message(self, session_id: str, message_data: Dict[str, Any]):
        """Send a message to a session."""
        ...

    async def wait_for_response(self, session_id: str, request_id: Any, timeout: float) -> Optional[str]:
        """Wait for a response from a session."""
        ...

    async def get_response_stream(self, session_id: str) -> Any:
        """Get or create response stream for a session."""
        ...

    def has_session(self, session_id: str) -> bool:
        """Check if a session exists."""
        ...


class MessageHandler:
    """Handles different types of messages in the proxy.

    This class encapsulates the logic for handling notifications,
    requests, and responses.
    """

    def __init__(self, session_manager: SessionManagerProtocol):
        """Initialize the message handler.

        Args:
            session_manager: The session manager to use
        """
        self.session_manager = session_manager

    async def handle_notification(
        self, session_id: str, message_data: Dict[str, Any], is_new_session: bool
    ) -> Response:
        """Handle a notification message.

        Args:
            session_id: The session ID
            message_data: The message data
            is_new_session: Whether this is a new session

        Returns:
            HTTP response
        """
        await self.session_manager.send_message(session_id, message_data)

        # Return 204 No Content for notifications
        return Response(
            status_code=204,
            headers={"X-Session-Id": session_id} if is_new_session else {},
        )

    async def handle_request(
        self, session_id: str, message_data: Dict[str, Any], is_new_session: bool, timeout: float = 300.0
    ) -> Response:
        """Handle a regular request message.

        Args:
            session_id: The session ID
            message_data: The message data
            is_new_session: Whether this is a new session
            timeout: Timeout for waiting for response

        Returns:
            HTTP response
        """
        # Forward message to backend
        await self.session_manager.send_message(session_id, message_data)

        # Get the request ID to wait for the response
        request_id = message_data.get("id")

        # Wait for the response from backend (with timeout)
        logger.info(f"Waiting for response to request {request_id} from backend...")

        response_data = await self.session_manager.wait_for_response(session_id, request_id, timeout=timeout)

        if response_data:
            # Return the JSON-RPC response directly
            logger.info(f"Returning response for request {request_id} directly in POST response")
            return Response(
                status_code=200,
                content=response_data,
                media_type="application/json",
                headers={"X-Session-Id": session_id} if is_new_session else {},
            )
        else:
            # Timeout or error - return error response
            logger.error(f"Timeout waiting for response to request {request_id}")
            error_response = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32001, "message": "Request timed out"},
            }
            return Response(
                status_code=200,
                content=json.dumps(error_response),
                media_type="application/json",
                headers={"X-Session-Id": session_id} if is_new_session else {},
            )

    def is_notification(self, message_data: Dict[str, Any]) -> bool:
        """Check if a message is a notification.

        Args:
            message_data: The message data

        Returns:
            True if the message is a notification
        """
        return message_data.get("method", "").startswith("notifications/")

    async def ensure_session_exists(self, session_id: str, is_new_session: bool) -> bool:
        """Ensure a session exists, creating it if needed.

        Args:
            session_id: The session ID
            is_new_session: Whether this is marked as a new session

        Returns:
            True if session exists or was created
        """
        if is_new_session or not self.session_manager.has_session(session_id):
            await self.session_manager.get_response_stream(session_id)
            return True

        # Check if session exists (skip for new sessions)
        if not is_new_session and not self.session_manager.has_session(session_id):
            return False

        return True
