"""Proxy session manager implementation.

This module manages proxy sessions and routes messages between the
StreamableHTTP frontend and asyncmcp backend transports.
"""

import asyncio
import logging
from typing import Any, Dict, Optional, cast

import anyio
from anyio.abc import TaskGroup
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp.shared.message import SessionMessage

from .client import create_backend_client
from .interceptor import InterceptingStream
from .session import ProxySession
from .utils import ProxyConfig, generate_session_id

logger = logging.getLogger(__name__)


class ProxySessionManager:
    """Manages proxy sessions and message routing.

    This class handles session lifecycle, message routing between clients
    and backend transports, and resource management.
    """

    def __init__(self, config: ProxyConfig):
        """Initialize the session manager.

        Args:
            config: Proxy configuration
        """
        self.config = config
        self._sessions: Dict[str, ProxySession] = {}
        self._response_streams: Dict[str, MemoryObjectSendStream[SessionMessage]] = {}
        self._response_receivers: Dict[str, MemoryObjectReceiveStream[SessionMessage]] = {}
        self._task_group: Optional[TaskGroup] = None
        self._running = False
        # Track pending responses for synchronous request/response pattern
        self._pending_responses: Dict[str, asyncio.Future[str]] = {}

    @property
    def active_session_count(self) -> int:
        """Get the number of active sessions."""
        return len(self._sessions)

    def create_session_id(self) -> str:
        """Create a new unique session ID."""
        return generate_session_id()

    def has_session(self, session_id: str) -> bool:
        """Check if a session exists.

        Args:
            session_id: Session ID to check

        Returns:
            bool: True if session exists
        """
        return session_id in self._sessions

    def get_single_session_id(self) -> Optional[str]:
        """Get the session ID if there's only one active session.

        This is used to support clients like MCP Inspector that expect
        a single session per server instance.

        Returns:
            str: The session ID if there's exactly one session, None otherwise
        """
        if len(self._sessions) == 1:
            return next(iter(self._sessions.keys()))
        return None

    async def start(self):
        """Start the session manager."""
        if self._running:
            return

        self._running = True

    async def stop(self):
        """Stop the session manager and clean up all sessions."""
        if not self._running:
            return

        self._running = False

        # Stop all sessions
        for session_id in list(self._sessions.keys()):
            await self.remove_session(session_id)

    async def get_response_stream(self, session_id: str) -> MemoryObjectReceiveStream[SessionMessage]:
        """Get the response stream for a session.

        This creates a new session if it doesn't exist.

        Args:
            session_id: Session ID

        Returns:
            Stream for receiving responses from the backend
        """
        if session_id not in self._sessions:
            await self._create_session(session_id)

        return self._response_receivers[session_id]

    async def send_message(self, session_id: str, message_data: Dict[str, Any]):
        """Send a message to a session's backend.

        Args:
            session_id: Session ID
            message_data: Message data to send
        """
        session = self._sessions.get(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        await session.send_message(message_data)

    async def wait_for_response(self, session_id: str, request_id: Any, timeout: float = 60.0) -> Optional[str]:
        """Wait for a specific response from the backend.

        Args:
            session_id: Session ID
            request_id: Request ID to wait for
            timeout: Maximum time to wait in seconds

        Returns:
            JSON string of the response, or None if timeout
        """
        # Create a future to wait for the response
        future_key = f"{session_id}:{request_id}"
        future = asyncio.get_event_loop().create_future()
        self._pending_responses[future_key] = future

        try:
            # Wait for the response with timeout
            response = await asyncio.wait_for(future, timeout=timeout)
            return response
        except asyncio.TimeoutError:
            logger.warning(f"Timeout waiting for response to request {request_id}")
            return None
        except Exception as e:
            logger.error(f"Error waiting for response: {e}")
            return None
        finally:
            # Clean up the future
            self._pending_responses.pop(future_key, None)

    def _handle_response(self, session_id: str, message: SessionMessage) -> bool:
        """Handle a response from the backend, checking for pending requests.

        Args:
            session_id: Session ID
            message: Response message

        Returns:
            True if message should be forwarded to SSE, False if handled synchronously
        """
        # Check if this is a response to a pending request
        message_dict = message.message.model_dump(exclude_none=True, by_alias=True)
        request_id = message_dict.get("id")

        if request_id is not None:
            future_key = f"{session_id}:{request_id}"
            future = self._pending_responses.get(future_key)

            if future and not future.done():
                # Fulfill the pending request
                response_json = message.message.model_dump_json(
                    exclude_none=True,
                    by_alias=True,
                )
                future.set_result(response_json)
                # Don't forward to SSE since it was handled synchronously
                return False

        # Forward to SSE stream
        return True

    def _create_intercepting_stream(
        self, session_id: str, original_stream: MemoryObjectSendStream[SessionMessage]
    ) -> MemoryObjectSendStream[SessionMessage]:
        """Create a wrapper stream that intercepts responses.

        Args:
            session_id: Session ID
            original_stream: Original send stream

        Returns:
            Wrapped stream that intercepts messages
        """
        return cast(MemoryObjectSendStream[SessionMessage], InterceptingStream(self, session_id, original_stream))

    async def remove_session(self, session_id: str):
        """Remove a session and clean up its resources.

        Args:
            session_id: Session ID to remove
        """
        session = self._sessions.pop(session_id, None)
        if session:
            await session.stop()

        # Clean up streams
        sender = self._response_streams.pop(session_id, None)
        if sender:
            await sender.aclose()

        receiver = self._response_receivers.pop(session_id, None)
        if receiver:
            await receiver.aclose()

    async def _create_session(self, session_id: str):
        """Create a new proxy session.

        Args:
            session_id: Session ID for the new session
        """
        if session_id in self._sessions:
            return

        # Check session limit
        if len(self._sessions) >= self.config.max_sessions:
            raise RuntimeError(f"Maximum session limit ({self.config.max_sessions}) reached")

        # Create response streams
        send_stream, receive_stream = anyio.create_memory_object_stream[SessionMessage](100)
        self._response_streams[session_id] = send_stream
        self._response_receivers[session_id] = receive_stream

        # Create backend client
        backend_client = create_backend_client(
            transport_type=self.config.backend_transport,
            config=self.config.backend_config,  # type: ignore[arg-type]
            low_level_clients=self.config.backend_clients,
            session_id=session_id,
        )

        # Create a wrapper send stream that intercepts responses
        wrapped_send_stream = self._create_intercepting_stream(session_id, send_stream)

        # Create and start session
        session = ProxySession(
            session_id=session_id,
            backend_client=backend_client,
            response_sender=wrapped_send_stream,
        )
        self._sessions[session_id] = session

        # Start session in background
        asyncio.create_task(self._run_session(session))

    async def _run_session(self, session: ProxySession):
        """Run a session in the background.

        Args:
            session: Session to run
        """
        try:
            await session.start()
        except Exception as e:
            logger.error(f"Session {session.session_id} error: {e}")
        finally:
            # Clean up session
            await self.remove_session(session.session_id)
