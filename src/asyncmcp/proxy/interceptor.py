import logging
from typing import Protocol

from anyio.streams.memory import MemoryObjectSendStream
from mcp.shared.message import SessionMessage

logger = logging.getLogger(__name__)


class ResponseHandler(Protocol):
    """Protocol for response handling."""

    def _handle_response(self, session_id: str, message: SessionMessage) -> bool:
        """Handle a response from the backend."""
        ...


class InterceptingStream:
    """Stream wrapper that intercepts responses for synchronous handling.

    This class wraps a MemoryObjectSendStream and intercepts messages
    to check if they should be handled synchronously (for pending requests)
    or forwarded to the SSE stream.
    """

    def __init__(
        self,
        manager: ResponseHandler,
        session_id: str,
        stream: MemoryObjectSendStream[SessionMessage],
    ):
        """Initialize the intercepting stream.

        Args:
            manager: The session manager that handles responses
            session_id: Session ID for this stream
            stream: The underlying stream to wrap
        """
        self.manager = manager
        self.session_id = session_id
        self.stream = stream

    async def send(self, message: SessionMessage):
        """Send a message, intercepting for synchronous handling.

        Args:
            message: The message to send
        """
        # Check if this message should be handled synchronously
        should_forward = self.manager._handle_response(self.session_id, message)
        if should_forward:
            # Forward to SSE
            await self.stream.send(message)
        # Otherwise it was handled by a pending request

    async def aclose(self):
        """Close the underlying stream."""
        await self.stream.aclose()
