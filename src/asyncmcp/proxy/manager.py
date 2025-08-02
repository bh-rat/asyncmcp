"""Proxy session manager implementation.

This module manages proxy sessions and routes messages between the
StreamableHTTP frontend and asyncmcp backend transports.
"""

import asyncio
import logging
from typing import Any, Dict, Optional, Union

import anyio
from anyio.abc import TaskGroup
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp.shared.message import SessionMessage
from mcp.types import ErrorData, JSONRPCMessage

from .client import ProxyClient, create_backend_client
from .utils import ProxyConfig, generate_session_id

logger = logging.getLogger(__name__)


class ProxySession:
    """Represents a single proxy session.

    Each session maintains a connection to the backend transport and
    manages message routing between the client and backend.
    """

    def __init__(
        self,
        session_id: str,
        backend_client: ProxyClient,
        response_sender: MemoryObjectSendStream[SessionMessage],
    ):
        """Initialize a proxy session.

        Args:
            session_id: Unique session identifier
            backend_client: Client for connecting to backend transport
            response_sender: Stream for sending responses to the client
        """
        self.session_id = session_id
        self.backend_client = backend_client
        self.response_sender = response_sender

        self._task_group: Optional[TaskGroup] = None
        self._read_stream: Optional[MemoryObjectReceiveStream[Union[SessionMessage, Exception]]] = None
        self._write_stream: Optional[MemoryObjectSendStream[SessionMessage]] = None
        self._running = False
        self._connected_event = anyio.Event()

    async def start(self):
        """Start the proxy session."""
        if self._running:
            return

        self._running = True
        logger.info(f"Starting proxy session {self.session_id}")

        async with anyio.create_task_group() as tg:
            self._task_group = tg

            # Connect to backend
            logger.info(f"Connecting to backend for session {self.session_id}")
            try:
                async with self.backend_client.connect() as (read_stream, write_stream):
                    self._read_stream = read_stream
                    self._write_stream = write_stream
                    logger.info(f"Backend connected successfully for session {self.session_id}")
                    logger.info(f"Read stream: {type(read_stream)}, Write stream: {type(write_stream)}")
                    
                    # Signal that we're connected
                    self._connected_event.set()

                    # Start forwarding messages from backend to client
                    tg.start_soon(self._forward_responses)

                    # Keep session alive until cancelled
                    try:
                        await anyio.Event().wait()
                    except asyncio.CancelledError:
                        logger.info(f"Proxy session {self.session_id} cancelled")
                        raise
            except Exception as e:
                logger.error(f"Failed to connect to backend for session {self.session_id}: {type(e).__name__}: {e}")
                import traceback
                logger.error(f"Traceback: {traceback.format_exc()}")
                raise

    async def stop(self):
        """Stop the proxy session."""
        if not self._running:
            return

        self._running = False
        logger.info(f"Stopping proxy session {self.session_id}")

        if self._task_group:
            self._task_group.cancel_scope.cancel()

    async def send_message(self, message_data: Dict[str, Any]):
        """Send a message to the backend.

        Args:
            message_data: JSON-RPC message data to send
        """
        logger.info(f"Session {self.session_id} sending message: method={message_data.get('method', 'unknown')}, id={message_data.get('id', 'none')}")
        
        # Wait for connection to be established
        logger.debug(f"Waiting for connection event for session {self.session_id}...")
        await self._connected_event.wait()
        logger.debug(f"Connection event received for session {self.session_id}")
        
        if not self._write_stream:
            logger.error(f"Write stream is None for session {self.session_id}")
            raise RuntimeError("Session not connected")

        try:
            # Parse and validate message
            logger.debug(f"Parsing message data: {message_data}")
            jsonrpc_message = JSONRPCMessage.model_validate(message_data)
            session_message = SessionMessage(jsonrpc_message)
            logger.debug(f"Created SessionMessage: {session_message}")

            # Send to backend
            logger.info(f"Sending message to backend via write stream for session {self.session_id}")
            await self._write_stream.send(session_message)
            logger.info(f"Message sent successfully to backend for session {self.session_id}")

        except Exception as e:
            logger.error(f"Session {self.session_id}: Error sending message: {e}")
            raise

    async def _forward_responses(self):
        """Forward responses from backend to client."""
        if not self._read_stream:
            return

        logger.info(f"Starting response forwarding for session {self.session_id}")
        try:
            async for message in self._read_stream:
                logger.info(f"Received message from backend for session {self.session_id}: {type(message)}")
                if isinstance(message, Exception):
                    # Handle backend errors
                    logger.error(f"Backend error for session {self.session_id}: {message}")
                    error_response = self._create_error_response(str(message))
                    await self.response_sender.send(error_response)
                else:
                    # Forward response to client
                    logger.info(f"Forwarding response to client for session {self.session_id}")
                    await self.response_sender.send(message)
                    logger.info(f"Session {self.session_id}: Successfully forwarded response to client")

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Session {self.session_id}: Error forwarding responses: {e}")

    def _create_error_response(self, error_message: str) -> SessionMessage:
        """Create an error response message."""
        error_data = ErrorData(
            code=-32603,  # Internal error
            message=error_message,
        )
        jsonrpc_message = JSONRPCMessage.model_validate(
            {
                "jsonrpc": "2.0",
                "error": error_data.model_dump(),
            }
        )
        return SessionMessage(jsonrpc_message)


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
        self._pending_responses: Dict[str, asyncio.Future] = {}

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
        logger.info("Starting proxy session manager")

    async def stop(self):
        """Stop the session manager and clean up all sessions."""
        if not self._running:
            return

        self._running = False
        logger.info("Stopping proxy session manager")

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

    async def wait_for_response(self, session_id: str, request_id: Any, timeout: float = 10.0) -> Optional[str]:
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
        
        if request_id is not None:  # Changed from if request_id to handle id=0
            future_key = f"{session_id}:{request_id}"
            future = self._pending_responses.get(future_key)
            
            if future and not future.done():
                # Fulfill the pending request
                response_json = message.message.model_dump_json(
                    exclude_none=True,
                    by_alias=True,
                )
                future.set_result(response_json)
                logger.info(f"Fulfilled pending response for request {request_id}")
                # Don't forward to SSE since it was handled synchronously
                return False
                
        # Forward to SSE stream
        return True
        
    def _create_intercepting_stream(self, session_id: str, original_stream: MemoryObjectSendStream[SessionMessage]) -> MemoryObjectSendStream[SessionMessage]:
        """Create a wrapper stream that intercepts responses.
        
        Args:
            session_id: Session ID
            original_stream: Original send stream
            
        Returns:
            Wrapped stream that intercepts messages
        """
        class InterceptingStream:
            def __init__(self, manager, session_id, stream):
                self.manager = manager
                self.session_id = session_id
                self.stream = stream
                
            async def send(self, message: SessionMessage):
                # Check if this message should be handled synchronously
                should_forward = self.manager._handle_response(self.session_id, message)
                if should_forward:
                    # Forward to SSE
                    await self.stream.send(message)
                # Otherwise it was handled by a pending request
                
            async def aclose(self):
                await self.stream.aclose()
                
        return InterceptingStream(self, session_id, original_stream)

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

        logger.info(f"Removed session {session_id}")

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

        logger.info(f"Creating proxy session {session_id}")

        # Create response streams
        send_stream, receive_stream = anyio.create_memory_object_stream[SessionMessage](100)
        self._response_streams[session_id] = send_stream
        self._response_receivers[session_id] = receive_stream

        # Create backend client
        logger.info(f"Creating backend client for transport: {self.config.backend_transport}")
        logger.info(f"Backend config: {self.config.backend_config}")
        logger.info(f"Backend clients: {self.config.backend_clients}")
        backend_client = create_backend_client(
            transport_type=self.config.backend_transport,
            config=self.config.backend_config,  # type: ignore[arg-type]
            low_level_clients=self.config.backend_clients,
            session_id=session_id,
        )
        logger.info(f"Backend client created: {type(backend_client).__name__} for session {session_id}")

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
