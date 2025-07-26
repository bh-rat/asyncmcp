"""
Webhook session manager implementation.

Handles HTTP requests, session management, and message routing for webhook transport.
"""

import logging
from contextlib import asynccontextmanager
from typing import Any

import anyio
import httpx
import mcp.types as types
import orjson
from anyio.abc import TaskGroup, TaskStatus
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp.server.lowlevel.server import Server as MCPServer
from mcp.shared.message import SessionMessage
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from asyncmcp.common.outgoing_event import OutgoingMessageEvent
from asyncmcp.common.utils import (
    create_internal_error_response,
    create_invalid_request_error_response,
    create_parse_error_response,
    create_protocol_version_error_response,
    create_session_id_error_response,
    create_session_not_found_error_response,
    create_session_terminated_error_response,
    is_initialize_request,
    validate_and_parse_message,
    validate_protocol_version,
    validate_session_id,
)
from asyncmcp.webhook.server import WebhookTransport
from asyncmcp.webhook.utils import (
    SessionInfo,
    WebhookServerConfig,
    extract_webhook_url_from_meta,
    generate_session_id,
)

logger = logging.getLogger(__name__)


class WebhookSessionManager:
    """Manages multiple webhook sessions and HTTP endpoints."""

    def __init__(
        self,
        app: MCPServer,
        config: WebhookServerConfig,
        server_path: str = "/mcp/request",
        stateless: bool = False,
    ):
        self.app = app
        self.config = config
        self.server_path = server_path
        self.stateless = stateless

        # Session management
        self._session_lock = anyio.Lock()
        self._transport_instances: dict[str, WebhookTransport] = {}
        self._sessions: dict[str, SessionInfo] = {}  # session_id -> SessionInfo
        self._client_sessions: dict[str, str] = {}  # client_id -> session_id
        self._request_sessions: dict[str, str] = {}  # request_id -> session_id

        # Message routing
        self._outgoing_message_sender: MemoryObjectSendStream[OutgoingMessageEvent] | None = None
        self._outgoing_message_receiver: MemoryObjectReceiveStream[OutgoingMessageEvent] | None = None

        # HTTP components
        self.http_client: httpx.AsyncClient | None = None

        # Task management
        self._task_group: TaskGroup | None = None
        self._run_lock = anyio.Lock()
        self._has_started = False

    @asynccontextmanager
    async def run(self):
        """Run the webhook session manager."""
        async with self._run_lock:
            if self._has_started:
                raise RuntimeError("WebhookSessionManager.run() can only be called once per instance.")
            self._has_started = True

        # Create message streams
        self._outgoing_message_sender, self._outgoing_message_receiver = anyio.create_memory_object_stream[
            OutgoingMessageEvent
        ](1000)

        # Initialize HTTP client
        timeout = httpx.Timeout(self.config.timeout_seconds)
        self.http_client = httpx.AsyncClient(timeout=timeout)

        async with anyio.create_task_group() as tg:
            self._task_group = tg
            tg.start_soon(self._event_driven_message_sender)

            logger.debug("Webhook session manager started")

            try:
                yield
            finally:
                logger.debug("Webhook session manager shutting down")
                await self.shutdown()

    async def handle_request(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        """Handle ASGI request."""
        request = Request(scope, receive)
        response = await self._handle_client_request(request)
        await response(scope, receive, send)

    async def _handle_client_request(self, request: Request) -> Response:
        """Handle incoming client HTTP request."""
        try:
            # Check for DELETE request (session termination)
            if request.method == "DELETE":
                return await self._handle_delete_request(request)

            # Only handle POST requests for regular MCP communication
            if request.method != "POST":
                error_response = create_invalid_request_error_response("Method Not Allowed")
                return Response(
                    content=error_response.model_dump_json(by_alias=True, exclude_none=True),
                    media_type="application/json",
                    status_code=405,
                    headers={"Allow": "POST, DELETE"},
                )

            body = await request.body()

            try:
                body_str = body.decode("utf-8")
            except UnicodeDecodeError:
                error_response = create_parse_error_response("Invalid UTF-8 encoding")
                return Response(
                    content=error_response.model_dump_json(by_alias=True, exclude_none=True),
                    media_type="application/json",
                    status_code=400,
                )

            session_message, parse_error = validate_and_parse_message(body_str)
            if parse_error:
                return Response(
                    content=parse_error.model_dump_json(by_alias=True, exclude_none=True),
                    media_type="application/json",
                    status_code=400,
                )

            client_id = request.headers.get("X-Client-ID")
            session_id = request.headers.get("X-Session-ID")
            protocol_version = request.headers.get("X-Protocol-Version")

            if session_id and session_id in self._transport_instances:
                transport = self._transport_instances[session_id]
                if transport.is_terminated:
                    error_response = create_session_terminated_error_response(session_id)
                    return Response(
                        content=error_response.model_dump_json(by_alias=True, exclude_none=True),
                        media_type="application/json",
                        status_code=404,
                    )

            if not client_id:
                error_response = create_invalid_request_error_response("Missing X-Client-ID header")
                return Response(
                    content=error_response.model_dump_json(by_alias=True, exclude_none=True),
                    media_type="application/json",
                    status_code=400,
                )

            if not validate_protocol_version(protocol_version):
                error_response = create_protocol_version_error_response(protocol_version)
                return Response(
                    content=error_response.model_dump_json(by_alias=True, exclude_none=True),
                    media_type="application/json",
                    status_code=400,
                )

            if session_id and not validate_session_id(session_id):
                error_response = create_session_id_error_response(session_id)
                return Response(
                    content=error_response.model_dump_json(by_alias=True, exclude_none=True),
                    media_type="application/json",
                    status_code=400,
                )

            if is_initialize_request(session_message):
                return await self._handle_initialize_request(session_message, client_id, session_id, protocol_version)

            elif (
                isinstance(session_message.message.root, types.JSONRPCNotification)
                and session_message.message.root.method == "notifications/initialized"
            ):
                if not session_id:
                    error_response = create_invalid_request_error_response(
                        "Missing X-Session-ID header for initialized notification"
                    )
                    return Response(
                        content=error_response.model_dump_json(by_alias=True, exclude_none=True),
                        media_type="application/json",
                        status_code=400,
                    )
                return await self._handle_initialized_notification(session_message, session_id)

            else:
                return await self._handle_regular_request(session_message, client_id, session_id)

        except Exception as e:
            logger.error(f"Error handling client request: {e}")
            error_response = create_internal_error_response(f"Internal server error: {str(e)}")
            return Response(
                content=error_response.model_dump_json(by_alias=True, exclude_none=True),
                media_type="application/json",
                status_code=500,
            )

    async def _handle_delete_request(self, request: Request) -> Response:
        """Handle DELETE request for session termination (like StreamableHTTP)."""
        session_id = request.headers.get("X-Session-ID")

        if not session_id:
            error_response = create_invalid_request_error_response("Missing X-Session-ID header")
            return Response(
                content=error_response.model_dump_json(by_alias=True, exclude_none=True),
                media_type="application/json",
                status_code=400,
            )

        if not validate_session_id(session_id):
            error_response = create_session_id_error_response(session_id)
            return Response(
                content=error_response.model_dump_json(by_alias=True, exclude_none=True),
                media_type="application/json",
                status_code=400,
            )

        success = await self.terminate_session(session_id)
        if success:
            return Response(
                content=orjson.dumps({"status": "terminated"}),
                media_type="application/json",
                status_code=200,
            )
        else:
            error_response = create_session_not_found_error_response()
            return Response(
                content=error_response.model_dump_json(by_alias=True, exclude_none=True),
                media_type="application/json",
                status_code=404,
            )

    async def _handle_initialize_request(
        self,
        session_message: SessionMessage,
        client_id: str,
        session_id: str | None,
        protocol_version: str | None = None,
    ) -> Response:
        """Handle initialize request and create new session."""
        webhook_url = extract_webhook_url_from_meta(session_message.message)
        if not webhook_url:
            return Response(
                content=orjson.dumps({"error": "Missing webhookUrl in _meta field"}),
                media_type="application/json",
                status_code=400,
            )

        async with self._session_lock:
            new_session_id = session_id or generate_session_id()

            if not validate_session_id(new_session_id):
                error_response = create_session_id_error_response(new_session_id)
                return Response(
                    content=error_response.model_dump_json(by_alias=True, exclude_none=True),
                    media_type="application/json",
                    status_code=400,
                )

            session_info = SessionInfo(
                session_id=new_session_id, client_id=client_id, webhook_url=webhook_url, state="init_pending"
            )
            self._sessions[new_session_id] = session_info
            self._client_sessions[client_id] = new_session_id
            if isinstance(session_message.message.root, types.JSONRPCRequest):
                self._request_sessions[str(session_message.message.root.id)] = new_session_id
            if not self.http_client:
                return Response(
                    content=orjson.dumps({"error": "HTTP client not initialized"}),
                    media_type="application/json",
                    status_code=500,
                )
            transport = WebhookTransport(
                config=self.config,
                http_client=self.http_client,
                session_id=new_session_id,
                webhook_url=webhook_url,
                outgoing_message_sender=self._outgoing_message_sender,
            )
            self._transport_instances[new_session_id] = transport

            async def run_session(*, task_status: TaskStatus[None] = anyio.TASK_STATUS_IGNORED):
                try:
                    async with transport.connect() as (read_stream, write_stream):
                        task_status.started()
                        await self.app.run(
                            read_stream,
                            write_stream,
                            self.app.create_initialization_options(),
                            stateless=self.stateless,
                        )
                except Exception as e:
                    logger.error(f"Session {new_session_id} crashed: {e}", exc_info=True)
                finally:
                    async with self._session_lock:
                        if new_session_id in self._transport_instances:
                            del self._transport_instances[new_session_id]
                        if new_session_id in self._sessions:
                            del self._sessions[new_session_id]
                        if client_id in self._client_sessions:
                            del self._client_sessions[client_id]

            assert self._task_group is not None
            await self._task_group.start(run_session)
            await transport.send_message(session_message)
        return Response(
            content=orjson.dumps({"status": "session_created", "session_id": new_session_id}),
            media_type="application/json",
            status_code=200,
            headers={"X-Session-ID": new_session_id},
        )

    async def _handle_initialized_notification(self, session_message: SessionMessage, session_id: str) -> Response:
        """Handle the initialized notification from the client."""
        if session_id not in self._sessions:
            logger.error(f"Session not found: {session_id}")
            return Response(
                content=orjson.dumps({"error": "Session not found"}),
                media_type="application/json",
                status_code=404,
            )

        session_info = self._sessions[session_id]
        if session_info.state != "init_pending":
            logger.warning(f"Session {session_id} is not in init_pending state: {session_info.state}")
            return Response(
                content=orjson.dumps({"error": "Session not in init_pending state"}),
                media_type="application/json",
                status_code=400,
            )

        if session_id in self._transport_instances:
            transport = self._transport_instances[session_id]
            await transport.send_message(session_message)
            logger.debug(f"Forwarded initialized notification to MCP server for session {session_id}")
        else:
            logger.error(f"Transport not found for session {session_id}")
            return Response(
                content=orjson.dumps({"error": "Transport not found"}),
                media_type="application/json",
                status_code=500,
            )

        session_info.state = "initialized"
        logger.debug(f"Session {session_id} marked as initialized")

        return Response(
            content=orjson.dumps({"status": "initialized"}),
            media_type="application/json",
            status_code=200,
        )

    async def _handle_regular_request(
        self, session_message: SessionMessage, client_id: str, session_id: str | None
    ) -> Response:
        if session_id and session_id in self._sessions:
            target_session_id = session_id
        elif client_id and client_id in self._client_sessions:
            target_session_id = self._client_sessions[client_id]
        else:
            logger.warning(f"No active session found for client_id={client_id} session_id={session_id}")
            return Response(
                content=orjson.dumps({"error": "No active session found"}),
                media_type="application/json",
                status_code=400,
            )
        if isinstance(session_message.message.root, types.JSONRPCRequest):
            self._request_sessions[str(session_message.message.root.id)] = target_session_id
        if target_session_id in self._transport_instances:
            transport = self._transport_instances[target_session_id]
            await transport.send_message(session_message)
        else:
            logger.error(f"Session transport not found for session_id={target_session_id}")
            return Response(
                content=orjson.dumps({"error": "Session transport not found"}),
                media_type="application/json",
                status_code=500,
            )
        return Response(
            content=orjson.dumps({"status": "received"}),
            media_type="application/json",
            status_code=200,
        )

    def asgi_app(self) -> ASGIApp:
        """Create ASGI application."""

        async def endpoint_handler(request: Request) -> Response:
            return await self._handle_client_request(request)

        routes = [
            Route(self.server_path, endpoint_handler, methods=["POST", "DELETE"]),
        ]
        return Starlette(routes=routes)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Direct ASGI callable for mounting as middleware or sub-app."""
        await self.handle_request(scope, receive, send)

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

    async def _event_driven_message_sender(self) -> None:
        """Background task to send outgoing messages from sessions back to clients via webhooks."""
        if not self._outgoing_message_receiver:
            logger.error("No outgoing message receiver configured")
            return

        try:
            async with self._outgoing_message_receiver:
                async for message_event in self._outgoing_message_receiver:
                    try:
                        if not message_event.session_id:
                            logger.warning("Received message event with no session_id")
                            continue
                        transport = self._transport_instances.get(message_event.session_id)
                        if not transport or transport.is_terminated:
                            logger.warning(
                                f"Received message for unknown/terminated session: {message_event.session_id}"
                            )
                            continue

                        await transport.send_to_client_webhook(message_event.message)

                        # Clean up request-to-session mapping after successful response
                        message_root = message_event.message.message.root
                        if isinstance(message_root, types.JSONRPCResponse | types.JSONRPCError):
                            request_id = str(message_root.id)
                            self._request_sessions.pop(request_id, None)

                    except Exception as e:
                        logger.error(f"Error processing outgoing message for session {message_event.session_id}: {e}")

        except anyio.get_cancelled_exc_class():
            raise
        except Exception as e:
            logger.error(f"Fatal error in webhook message sender: {e}")
            raise

    async def terminate_session(self, session_id: str) -> bool:
        """Terminate a specific session."""
        async with self._session_lock:
            if session_id in self._transport_instances:
                transport = self._transport_instances[session_id]
                await transport.terminate()
                del self._transport_instances[session_id]

                # Clean up session info
                if session_id in self._sessions:
                    session_info = self._sessions[session_id]
                    client_id = session_info.client_id
                    del self._sessions[session_id]

                    # Clean up client mapping
                    if client_id in self._client_sessions:
                        del self._client_sessions[client_id]

                logger.debug(f"Terminated session: {session_id}")
                return True
            return False

    def get_all_sessions(self) -> dict[str, dict[str, Any]]:
        """Get information about all active sessions."""
        return {
            session_id: {
                "session_id": session_id,
                "client_id": self._sessions.get(session_id, SessionInfo("", "", "", "")).client_id,
                "webhook_url": self._sessions.get(session_id, SessionInfo("", "", "", "")).webhook_url,
                "state": self._sessions.get(session_id, SessionInfo("", "", "", "")).state,
                "terminated": transport.is_terminated,
            }
            for session_id, transport in self._transport_instances.items()
        }

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
        self._client_sessions.clear()
        self._request_sessions.clear()
