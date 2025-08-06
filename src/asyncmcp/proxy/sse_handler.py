"""Server-Sent Events (SSE) handling for proxy server.

This module handles SSE connections and streaming responses.
"""

import asyncio
import json
import logging
from typing import AsyncGenerator, Protocol

from anyio.streams.memory import MemoryObjectReceiveStream
from mcp.shared.message import SessionMessage
from starlette.responses import StreamingResponse

logger = logging.getLogger(__name__)


class SessionManagerProtocol(Protocol):
    """Protocol for session manager."""

    async def get_response_stream(self, session_id: str) -> MemoryObjectReceiveStream[SessionMessage]:
        """Get the response stream for a session."""
        ...


class SSEHandler:
    """Handles Server-Sent Events (SSE) connections.

    This class encapsulates the logic for creating SSE responses
    and streaming messages from the backend.
    """

    def __init__(self, session_manager: SessionManagerProtocol):
        """Initialize the SSE handler.

        Args:
            session_manager: The session manager to use
        """
        self.session_manager = session_manager

    async def create_sse_response(self, session_id: str, is_new_session: bool = False) -> StreamingResponse:
        """Create an SSE response for a session.

        Args:
            session_id: The session ID
            is_new_session: Whether this is a new session

        Returns:
            StreamingResponse for SSE
        """
        response = StreamingResponse(
            self._event_generator(session_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Session-Id": session_id,
            },
        )

        # Set session cookie for browser-based clients
        response.set_cookie(
            key="mcp-session-id",
            value=session_id,
            httponly=True,
            samesite="lax",
            max_age=86400,  # 24 hours
        )

        return response

    async def _event_generator(self, session_id: str) -> AsyncGenerator[str, None]:
        """Generate SSE events from backend responses.

        Args:
            session_id: The session ID

        Yields:
            SSE formatted events
        """
        try:
            # Get response stream for this session
            response_stream = await self.session_manager.get_response_stream(session_id)

            # Stream responses from backend
            async for message in response_stream:
                # Convert SessionMessage to JSON
                message_data = message.message.model_dump_json(
                    exclude_none=True,
                    by_alias=True,
                )

                # Send as SSE event
                yield f"event: message\ndata: {message_data}\n\n"

        except asyncio.CancelledError:
            logger.info(f"SSE connection closed for session {session_id}")
            raise
        except Exception as e:
            logger.error(f"Error in SSE stream for session {session_id}: {e}")
            error_data = json.dumps({"error": str(e), "type": "stream_error"})
            yield f"event: error\ndata: {error_data}\n\n"
        finally:
            # Don't remove session here - sessions should persist beyond SSE connections
            # This allows clients like MCP Inspector to reconnect or make subsequent requests
            logger.info(f"SSE stream ended for session {session_id}, but session remains active")
