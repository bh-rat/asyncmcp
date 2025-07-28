"""
Streamable HTTP + Webhook session manager implementation.

This module provides session management for the StreamableHTTP + Webhook transport,
following AsyncMCP's session manager patterns while providing full MCP compatibility.
"""

import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional, Set

import anyio
import httpx
from anyio.abc import TaskGroup, TaskStatus
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp.server.lowlevel.server import Server as MCPServer
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import Receive, Scope, Send

from asyncmcp.common.outgoing_event import OutgoingMessageEvent

from .server import StreamableHTTPWebhookTransport
from .utils import (
    MCP_SESSION_ID_HEADER,
    SessionInfo,
    StreamableHTTPWebhookConfig,
    generate_session_id,
)

logger = logging.getLogger(__name__)


class StreamableHTTPWebhookSessionManager:
    """
    Session manager for StreamableHTTP + Webhook transport.

    Provides session management following AsyncMCP patterns while supporting
    full MCP StreamableHTTP compatibility plus selective webhook routing.
    """

    def __init__(
        self,
        app: MCPServer,
        config: StreamableHTTPWebhookConfig,
        server_path: str = "/mcp",
        stateless: bool = False,
        webhook_tools: Optional[Set[str]] = None,
    ):
        """
        Initialize the session manager.

        Args:
            app: The MCP server instance
            config: Transport configuration
            server_path: HTTP endpoint path
            stateless: Whether to run in stateless mode
            webhook_tools: Set of tool names that should be handled via webhook.
                          If None, no tools will be routed to webhooks.
        """
        self.app = app
        self.config = config
        self.server_path = server_path
        self.stateless = stateless

        # Use explicitly provided webhook tools
        self.webhook_tools = webhook_tools or set()
        logger.info(f"Configured webhook tools: {self.webhook_tools}")

        # Session management
        self._session_lock = anyio.Lock()
        self._transport_instances: Dict[str, StreamableHTTPWebhookTransport] = {}
        self._sessions: Dict[str, SessionInfo] = {}  # Track session initialization state

        # Message routing
        self._outgoing_message_sender: Optional[MemoryObjectSendStream[OutgoingMessageEvent]] = None
        self._outgoing_message_receiver: Optional[MemoryObjectReceiveStream[OutgoingMessageEvent]] = None

        # HTTP components
        self.http_client: Optional[httpx.AsyncClient] = None

        # Task management
        self._task_group: Optional[TaskGroup] = None
        self._run_lock = anyio.Lock()
        self._has_started = False

    @asynccontextmanager
    async def run(self):
        """Run the session manager with proper lifecycle management."""
        async with self._run_lock:
            if self._has_started:
                raise RuntimeError("StreamableHTTPWebhookSessionManager.run() can only be called once per instance.")
            self._has_started = True

        # Create message streams
        self._outgoing_message_sender, self._outgoing_message_receiver = anyio.create_memory_object_stream[
            OutgoingMessageEvent
        ](1000)

        # Initialize HTTP client
        timeout = httpx.Timeout(self.config.timeout_seconds)
        self.http_client = httpx.AsyncClient(timeout=timeout)

        try:
            async with anyio.create_task_group() as tg:
                self._task_group = tg
                yield self  # Yield control back to caller
        finally:
            await self.shutdown()

    async def handle_request(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        """Handle ASGI request."""
        if self.stateless:
            # In stateless mode, create a new transport for each request
            await self._handle_stateless_request(scope, receive, send)
        else:
            # In stateful mode, use session management
            await self._handle_stateful_request(scope, receive, send)

    async def _handle_stateless_request(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        """Handle request in stateless mode."""
        logger.debug("Stateless mode: Creating new transport for this request")

        # Create a temporary transport without session tracking
        http_transport = StreamableHTTPWebhookTransport(
            config=self.config,
            http_client=self.http_client,
            session_id=None,  # No session tracking in stateless mode
            webhook_url=None,  # Will be extracted from request if needed
            webhook_tools=self.webhook_tools,
            outgoing_message_sender=None,  # No central routing in stateless mode
            on_initialized=None,  # No initialization tracking in stateless mode
        )

        # Run server in a new task
        async def run_stateless_server(*, task_status: TaskStatus[None] = anyio.TASK_STATUS_IGNORED):
            async with http_transport.connect() as streams:
                read_stream, write_stream = streams
                task_status.started()
                try:
                    await self.app.run(
                        read_stream,
                        write_stream,
                        self.app.create_initialization_options(),
                        stateless=True,
                    )
                except Exception:
                    logger.exception("Stateless session crashed")

        # Start the server task
        assert self._task_group is not None
        await self._task_group.start(run_stateless_server)

        # Handle the HTTP request
        await http_transport.handle_request(scope, receive, send)

        # Terminate the transport after the request
        await http_transport.terminate()

    async def _handle_stateful_request(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        """Handle request in stateful mode with session management."""
        request = Request(scope, receive)
        request_session_id = request.headers.get(MCP_SESSION_ID_HEADER)

        # Existing session case
        if request_session_id is not None and request_session_id in self._transport_instances:
            transport = self._transport_instances[request_session_id]
            logger.debug("Session already exists, handling request directly")
            await transport.handle_request(scope, receive, send)
            return

        if request_session_id is None:
            # New session case - create session and let transport handle the request
            logger.debug("Creating new transport")
            async with self._session_lock:
                new_session_id = generate_session_id()

                # Create transport with default configuration (webhook will be set during initialize)
                http_transport = StreamableHTTPWebhookTransport(
                    config=self.config,
                    http_client=self.http_client,
                    session_id=new_session_id,
                    webhook_url=None,  # Will be extracted from initialize request
                    webhook_tools=self.webhook_tools,
                    outgoing_message_sender=self._outgoing_message_sender,
                    on_initialized=self._on_session_initialized,
                )

                self._transport_instances[new_session_id] = http_transport

                # Create session info to track initialization state
                session_info = SessionInfo(
                    session_id=new_session_id,
                    client_id="",  # Not using client_id in new architecture
                    webhook_url="",  # Will be set during initialization
                    webhook_tools=self.webhook_tools,
                    state="init_pending",  # Start in pending state
                )
                self._sessions[new_session_id] = session_info

                # Define the server runner
                async def run_server(*, task_status: TaskStatus[None] = anyio.TASK_STATUS_IGNORED) -> None:
                    async with http_transport.connect() as streams:
                        read_stream, write_stream = streams
                        task_status.started()
                        try:
                            await self.app.run(
                                read_stream,
                                write_stream,
                                self.app.create_initialization_options(),
                                stateless=False,  # Stateful mode
                            )
                        except Exception as e:
                            logger.error(
                                f"Session {http_transport.session_id} crashed: {e}",
                                exc_info=True,
                            )
                        finally:
                            # Only remove from instances if not terminated
                            async with self._session_lock:
                                if (
                                    http_transport.session_id
                                    and http_transport.session_id in self._transport_instances
                                    and not http_transport.is_terminated
                                ):
                                    logger.info(
                                        "Cleaning up crashed session "
                                        f"{http_transport.session_id} from "
                                        "active instances."
                                    )
                                    del self._transport_instances[http_transport.session_id]

                                    # Also clean up session info
                                    if http_transport.session_id in self._sessions:
                                        del self._sessions[http_transport.session_id]

                # Start the server task
                assert self._task_group is not None
                await self._task_group.start(run_server)

                # Handle the HTTP request and return the response
                await http_transport.handle_request(scope, receive, send)
        else:
            # Invalid session ID

            response = Response(
                "Bad Request: No valid session ID provided",
                status_code=400,
            )
            await response(scope, receive, send)

    def asgi_app(self):
        """Create ASGI application for use with ASGI servers like uvicorn."""
        return self

    async def _on_session_initialized(self, session_id: str) -> None:
        """Callback when a session completes initialization."""
        async with self._session_lock:
            if session_id in self._sessions:
                self._sessions[session_id].state = "initialized"
            else:
                logger.warning(f"Received initialization callback for unknown session: {session_id}")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Direct ASGI callable for mounting as middleware or sub-app."""
        await self.handle_request(scope, receive, send)

    async def terminate_session(self, session_id: str) -> bool:
        """Terminate a specific session."""
        async with self._session_lock:
            if session_id in self._transport_instances:
                transport = self._transport_instances[session_id]
                await transport.terminate()
                del self._transport_instances[session_id]

                # Also clean up session info
                if session_id in self._sessions:
                    del self._sessions[session_id]

                return True
            return False

    def get_all_sessions(self) -> Dict[str, Dict[str, Any]]:
        """Get information about all active sessions."""
        return {
            session_id: {
                "session_id": session_id,
                "webhook_url": transport.webhook_url,
                "webhook_tools": list(transport.webhook_tools),
                "terminated": transport.is_terminated,
            }
            for session_id, transport in self._transport_instances.items()
        }

    async def shutdown(self) -> None:
        """Shutdown the session manager and clean up resources."""
        if self._task_group:
            self._task_group.cancel_scope.cancel()

        await self._shutdown_all_sessions()

        if self.http_client:
            await self.http_client.aclose()
            self.http_client = None

        if self._outgoing_message_sender:
            await self._outgoing_message_sender.aclose()
            self._outgoing_message_sender = None
        if self._outgoing_message_receiver:
            await self._outgoing_message_receiver.aclose()
            self._outgoing_message_receiver = None

        self._task_group = None

    async def _shutdown_all_sessions(self) -> None:
        """Shutdown all active sessions."""
        sessions_to_terminate = list(self._transport_instances.keys())

        for session_id in sessions_to_terminate:
            try:
                await self.terminate_session(session_id)
            except Exception as e:
                logger.error(f"Error terminating session {session_id}: {e}")

        self._transport_instances.clear()
        self._sessions.clear()
