"""Proxy server implementation.

This module provides a StreamableHTTP server that acts as a proxy,
forwarding requests to asyncmcp backend transports.
"""

import asyncio
import json
import logging

import uvicorn
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route

from .message_handler import MessageHandler
from .session_manager import ProxySessionManager
from .session_resolver import SessionResolver
from .sse_handler import SSEHandler
from .utils import ProxyConfig, validate_auth_token

logger = logging.getLogger(__name__)


class ProxyServer:
    """StreamableHTTP proxy server for asyncmcp transports.

    This server exposes a standard MCP StreamableHTTP endpoint and forwards
    requests to configured asyncmcp backend transports (SQS, SNS+SQS, etc.).
    """

    def __init__(self, config: ProxyConfig):
        """Initialize the proxy server.

        Args:
            config: Proxy server configuration
        """
        self.config = config
        self.session_manager = ProxySessionManager(config)
        self.session_resolver = SessionResolver(self.session_manager, config.stateless)
        self.message_handler = MessageHandler(self.session_manager)
        self.sse_handler = SSEHandler(self.session_manager)

        # Create Starlette app with routes
        self.app = Starlette(
            routes=[
                Route(config.server_path, self.handle_request, methods=["GET", "POST"]),
                Route("/health", self.handle_health, methods=["GET"]),
            ],
            on_startup=[self.startup],
            on_shutdown=[self.shutdown],
        )

        # Add CORS middleware if configured
        if config.cors_origins:
            self.app.add_middleware(
                CORSMiddleware,
                allow_origins=config.cors_origins,
                allow_credentials=True,
                allow_methods=["GET", "POST"],
                allow_headers=["*"],
            )

    async def startup(self):
        """Initialize server resources on startup."""
        await self.session_manager.start()

    async def shutdown(self):
        """Clean up server resources on shutdown."""
        await self.session_manager.stop()

    async def handle_health(self, request: Request) -> Response:
        """Health check endpoint."""
        return Response(
            content=json.dumps(
                {
                    "status": "healthy",
                    "backend_transport": self.config.backend_transport,
                    "active_sessions": self.session_manager.active_session_count,
                }
            ),
            media_type="application/json",
        )

    async def handle_request(self, request: Request) -> Response:
        """Handle all requests to the MCP endpoint.

        Routes based on HTTP method:
        - GET: Establish SSE connection
        - POST: Handle message requests
        """
        if request.method == "GET":
            return await self.handle_sse(request)
        elif request.method == "POST":
            return await self.handle_message(request)
        else:
            return Response(
                status_code=405,
                content=json.dumps({"error": "Method not allowed"}),
                media_type="application/json",
                headers={"Allow": "GET, POST"},
            )

    async def handle_sse(self, request: Request) -> Response:
        """Handle Server-Sent Events (SSE) connection for StreamableHTTP.

        This endpoint establishes an SSE connection with the client and
        streams responses from the backend transport.
        """
        # Validate authentication if enabled
        if self.config.auth_enabled:
            auth_header = request.headers.get("Authorization", "")
            token = auth_header.replace("Bearer ", "") if auth_header.startswith("Bearer ") else ""
            if not validate_auth_token(token, self.config.auth_token):
                return Response(status_code=401, content="Unauthorized")

        # Get or create session
        session_id, is_new_session = self.session_resolver.resolve_sse_session(request)

        # Create and return SSE response
        return await self.sse_handler.create_sse_response(session_id, is_new_session)

    async def _create_sse_response_for_request(self, session_id: str, request_id: str) -> StreamingResponse:
        """Create an SSE response stream for a specific request."""

        async def request_event_generator():
            """Generate SSE events for a specific request response."""
            try:
                # Get response stream for this session
                response_stream = await self.session_manager.get_response_stream(session_id)

                # Wait for the specific response
                async for message in response_stream:
                    # Check if this is the response we're waiting for
                    message_dict = message.message.model_dump(exclude_none=True, by_alias=True)
                    if message_dict.get("id") == request_id:
                        # Send the response as SSE event
                        message_data = message.message.model_dump_json(
                            exclude_none=True,
                            by_alias=True,
                        )
                        yield f"event: message\ndata: {message_data}\n\n"
                        break
                    # Also forward any notifications or messages without ID
                    elif "id" not in message_dict:
                        message_data = message.message.model_dump_json(
                            exclude_none=True,
                            by_alias=True,
                        )
                        yield f"event: message\ndata: {message_data}\n\n"

            except Exception as e:
                logger.error(f"Error in SSE response stream: {e}")
                error_data = json.dumps({"error": str(e), "type": "stream_error"})
                yield f"event: error\ndata: {error_data}\n\n"

        return StreamingResponse(
            request_event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Session-Id": session_id,
            },
        )

    async def handle_message(self, request: Request) -> Response:
        """Handle message POST endpoint for StreamableHTTP.

        This endpoint receives messages from the client and forwards them
        to the backend transport.
        """
        # Validate authentication if enabled
        if self.config.auth_enabled:
            auth_header = request.headers.get("Authorization", "")
            token = auth_header.replace("Bearer ", "") if auth_header.startswith("Bearer ") else ""
            if not validate_auth_token(token, self.config.auth_token):
                return Response(status_code=401, content="Unauthorized")

        # Parse request body
        try:
            body = await request.body()
            message_data = json.loads(body)
        except json.JSONDecodeError as e:
            return Response(
                status_code=400,
                content=json.dumps({"error": f"Invalid JSON: {str(e)}"}),
                media_type="application/json",
            )

        # Resolve session ID
        resolution = await self.session_resolver.resolve_session(request, message_data)

        if resolution.error_message:
            return Response(
                status_code=400,
                content=json.dumps({"error": resolution.error_message}),
                media_type="application/json",
            )

        session_id = resolution.session_id
        is_new_session = resolution.is_new_session

        if not session_id:
            return Response(
                status_code=400,
                content=json.dumps({"error": "Failed to resolve session"}),
                media_type="application/json",
            )

        # Ensure session exists
        if not await self.message_handler.ensure_session_exists(session_id, is_new_session):
            return Response(
                status_code=404,
                content=json.dumps({"error": "Session not found"}),
                media_type="application/json",
            )

        try:
            # Handle notification or request
            if self.message_handler.is_notification(message_data):
                response = await self.message_handler.handle_notification(session_id, message_data, is_new_session)
            else:
                response = await self.message_handler.handle_request(session_id, message_data, is_new_session)

            # Set session cookie for new sessions
            if is_new_session:
                response.set_cookie(
                    key="mcp-session-id",
                    value=session_id,
                    httponly=True,
                    samesite="lax",
                    max_age=86400,  # 24 hours
                )

            return response

        except Exception as e:
            logger.error(f"Error handling message for session {session_id}: {e}")
            return Response(
                status_code=500,
                content=json.dumps({"error": "Internal server error"}),
                media_type="application/json",
            )

    async def run(self):
        """Run the proxy server.

        This method starts the Uvicorn server with the configured settings.
        """
        config = uvicorn.Config(
            self.app,
            host=self.config.host,
            port=self.config.port,
            log_level="info",
        )
        server = uvicorn.Server(config)
        await server.serve()

    def run_sync(self):
        """Run the proxy server synchronously.

        This method is useful for running the server from a non-async context.
        """
        asyncio.run(self.run())
