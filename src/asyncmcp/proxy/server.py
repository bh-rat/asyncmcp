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

from .manager import ProxySessionManager
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
        logger.info(
            f"Starting proxy server on {self.config.host}:{self.config.port} "
            f"with backend transport: {self.config.backend_transport}"
        )
        await self.session_manager.start()

    async def shutdown(self):
        """Clean up server resources on shutdown."""
        logger.info("Shutting down proxy server")
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

    async def handle_sse(self, request: Request) -> StreamingResponse:
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
        session_id = request.headers.get("X-Session-Id")
        if not session_id:
            session_id = self.session_manager.create_session_id()
            logger.info(f"New SSE connection from {request.client.host}, assigned session {session_id}")
        else:
            logger.info(f"Existing SSE connection from {request.client.host}, session {session_id}")

        # Create SSE response
        async def event_generator():
            """Generate SSE events from backend responses."""
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

        response = StreamingResponse(
            event_generator(),
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
            max_age=86400  # 24 hours
        )
        
        return response

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
        to the backend transport. Returns SSE stream if Accept header includes
        text/event-stream, otherwise returns JSON response.
        """
        # Validate authentication if enabled
        if self.config.auth_enabled:
            auth_header = request.headers.get("Authorization", "")
            token = auth_header.replace("Bearer ", "") if auth_header.startswith("Bearer ") else ""
            if not validate_auth_token(token, self.config.auth_token):
                return Response(status_code=401, content="Unauthorized")

        # Check Accept header to determine response type
        accept_header = request.headers.get("accept", "")
        accepts_sse = "text/event-stream" in accept_header
        accepts_json = "application/json" in accept_header or "*/*" in accept_header
        
        # Debug logging for MCP Inspector
        logger.info(f"POST request Accept header: '{accept_header}'")
        logger.info(f"Accepts SSE: {accepts_sse}, Accepts JSON: {accepts_json}")

        # Parse request body first to check if it's an initialize request
        try:
            body = await request.body()
            message_data = json.loads(body)
            is_initialize = message_data.get("method") == "initialize"
        except json.JSONDecodeError as e:
            return Response(
                status_code=400,
                content=json.dumps({"error": f"Invalid JSON: {str(e)}"}),
                media_type="application/json",
            )
        
        # Get session ID from various sources
        session_id = None
        is_new_session = False
        
        # Debug logging
        logger.debug(f"Request method: {request.method}, is_initialize: {is_initialize}")
        logger.debug(f"Headers: {dict(request.headers)}")
        logger.debug(f"Active sessions: {self.session_manager.active_session_count}")
        
        # 1. Check X-Session-Id header (for clients that support it)
        session_id = request.headers.get("X-Session-Id")
        if session_id:
            logger.debug(f"Found session ID in X-Session-Id header: {session_id}")
        
        # 2. Check cookies for session (for browser-based clients like MCP Inspector)
        if not session_id and "cookie" in request.headers:
            cookies = request.cookies
            session_id = cookies.get("mcp-session-id")
            if session_id:
                logger.debug(f"Found session ID in cookie: {session_id}")
        
        # 3. For stateless mode or single session, use a default session
        if not session_id and (self.config.stateless or is_initialize):
            # In stateless mode or for initialize, use/create a default session
            if self.config.stateless:
                session_id = "default"
                logger.debug("Using default session for stateless mode")
            elif is_initialize:
                # For initialize, try to use existing session if there's only one
                if self.session_manager.active_session_count == 1:
                    session_id = self.session_manager.get_single_session_id()
                    logger.info(f"Using existing single session for initialize: {session_id}")
                else:
                    # Create new session for initialize requests
                    session_id = self.session_manager.create_session_id()
                    is_new_session = True
                    logger.info(f"New session created for initialize request: {session_id}")
        
        # 4. If still no session ID and not initialize, try to get the first/only active session
        # This supports MCP Inspector which expects a single session per server
        if not session_id and self.session_manager.active_session_count == 1:
            # Get the single active session
            session_id = self.session_manager.get_single_session_id()
            if session_id:
                logger.info(f"Using single active session: {session_id}")
        
        # 5. Final check - if no session ID found, return error
        if not session_id:
            logger.error(f"No session ID found. Active sessions: {self.session_manager.active_session_count}")
            return Response(
                status_code=400,
                content=json.dumps({"error": "No active session. Please initialize first."}),
                media_type="application/json",
            )
        
        # Create session if needed
        if is_new_session or not self.session_manager.has_session(session_id):
            await self.session_manager.get_response_stream(session_id)

        # Check if session exists (skip for new sessions)
        if not is_new_session and not self.session_manager.has_session(session_id):
            return Response(
                status_code=404,
                content=json.dumps({"error": "Session not found"}),
                media_type="application/json",
            )

        try:
            # Forward message to backend
            await self.session_manager.send_message(session_id, message_data)
            
            # Get the request ID to wait for the response
            request_id = message_data.get("id")
            
            # Wait for the response from backend (with timeout)
            logger.info(f"Waiting for response to request {request_id} from backend...")
            
            response_data = await self.session_manager.wait_for_response(
                session_id, request_id, timeout=10.0
            )
            
            if response_data:
                # Return the JSON-RPC response directly
                logger.info(f"Returning response for request {request_id} directly in POST response")
                response = Response(
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
                    "error": {
                        "code": -32001,
                        "message": "Request timed out"
                    }
                }
                response = Response(
                    status_code=200,
                    content=json.dumps(error_response),
                    media_type="application/json",
                    headers={"X-Session-Id": session_id} if is_new_session else {},
                )
            
            # Set session cookie for new sessions
            if is_new_session:
                response.set_cookie(
                    key="mcp-session-id",
                    value=session_id,
                    httponly=True,
                    samesite="lax",
                    max_age=86400  # 24 hours
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
