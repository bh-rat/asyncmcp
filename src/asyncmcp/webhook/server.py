"""
Webhook server transport implementation.

The server receives HTTP POST requests from clients and sends responses via webhooks.
"""

import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

import anyio
import httpx
import orjson
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from collections.abc import AsyncGenerator
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route
import uvicorn

import mcp.types as types
from mcp.shared.message import SessionMessage

from .utils import (
    WebhookTransportConfig,
    SessionInfo,
    create_http_headers,
    parse_webhook_request,
    send_webhook_response,
    extract_webhook_url_from_meta,
    generate_session_id,
)

logger = logging.getLogger(__name__)


class WebhookServer:
    """Webhook server that receives HTTP requests and sends webhook responses."""
    
    def __init__(self, config: WebhookTransportConfig):
        self.config = config
        self.sessions: Dict[str, SessionInfo] = {}  # session_id -> SessionInfo
        self.client_sessions: Dict[str, str] = {}  # client_id -> session_id
        self.request_sessions: Dict[str, str] = {}  # request_id -> session_id
        
        # Streams for communication
        self.read_stream_writer: Optional[MemoryObjectSendStream[SessionMessage | Exception]] = None
        self.write_stream_reader: Optional[MemoryObjectReceiveStream[SessionMessage]] = None
        
        # HTTP client for sending webhook responses
        self.http_client: Optional[httpx.AsyncClient] = None
        
        # HTTP server for receiving requests
        self.http_server: Optional[Any] = None
    
    async def handle_client_request(self, request: Request) -> Response:
        """Handle incoming client request."""
        try:
            body = await request.body()
            session_message = await parse_webhook_request(body)
            
            # Extract client and session info from headers
            client_id = request.headers.get("X-Client-ID")
            session_id = request.headers.get("X-Session-ID")
            
            if not client_id:
                return Response(
                    content=orjson.dumps({"error": "Missing X-Client-ID header"}),
                    media_type="application/json",
                    status_code=400,
                )
            
            # Handle initialization request
            message_root = session_message.message.root
            if (isinstance(message_root, types.JSONRPCRequest) and 
                message_root.method == "initialize"):
                
                # Extract webhook URL from _meta field
                webhook_url = extract_webhook_url_from_meta(session_message.message)
                if not webhook_url:
                    return Response(
                        content=orjson.dumps({"error": "Missing webhookUrl in _meta field"}),
                        media_type="application/json",
                        status_code=400,
                    )
                
                # Create new session
                new_session_id = generate_session_id()
                session_info = SessionInfo(
                    session_id=new_session_id,
                    client_id=client_id,
                    webhook_url=webhook_url,
                    state="init_pending"
                )
                
                self.sessions[new_session_id] = session_info
                self.client_sessions[client_id] = new_session_id
                
                # Update session message metadata
                session_message.metadata = {
                    "session_id": new_session_id,
                    "client_id": client_id,
                    "webhook_url": webhook_url
                }
                
                logger.info(f"Created new session {new_session_id} for client {client_id}")
                
                # Store request-to-session mapping for response routing
                if isinstance(message_root, types.JSONRPCRequest):
                    self.request_sessions[str(message_root.id)] = new_session_id
            
            elif (isinstance(message_root, types.JSONRPCNotification) and 
                  message_root.method == "notifications/initialized"):
                
                # Handle initialized notification
                if session_id and session_id in self.sessions:
                    self.sessions[session_id].state = "initialized"
                    logger.info(f"Session {session_id} marked as initialized")
                
                # Don't send this notification to the MCP server
                return Response(
                    content=orjson.dumps({"status": "acknowledged"}),
                    media_type="application/json",
                    status_code=200,
                )
            
            else:
                # Regular request - find session
                if session_id and session_id in self.sessions:
                    session_info = self.sessions[session_id]
                    session_message.metadata = {
                        "session_id": session_id,
                        "client_id": session_info.client_id,
                        "webhook_url": session_info.webhook_url
                    }
                elif client_id and client_id in self.client_sessions:
                    session_id = self.client_sessions[client_id]
                    session_info = self.sessions[session_id]
                    session_message.metadata = {
                        "session_id": session_id,
                        "client_id": client_id,
                        "webhook_url": session_info.webhook_url
                    }
                else:
                    return Response(
                        content=orjson.dumps({"error": "No active session found"}),
                        media_type="application/json",
                        status_code=400,
                    )
            
            # Store request-to-session mapping for response routing
            if isinstance(message_root, types.JSONRPCRequest) and session_message.metadata:
                self.request_sessions[str(message_root.id)] = session_message.metadata["session_id"]
            
            # Forward to MCP server
            if self.read_stream_writer:
                await self.read_stream_writer.send(session_message)
            
            return Response(
                content=orjson.dumps({"status": "received"}),
                media_type="application/json",
                status_code=200,
            )
        
        except Exception as e:
            logger.error(f"Error handling client request: {e}")
            if self.read_stream_writer:
                await self.read_stream_writer.send(e)
            
            return Response(
                content=orjson.dumps({"error": str(e)}),
                media_type="application/json",
                status_code=500,
            )
    
    async def start_http_server(self) -> None:
        """Start the HTTP server to receive client requests."""
        routes = [
            Route(f"{self.config.server_base_path}/request", self.handle_client_request, methods=["POST"]),
        ]
        
        app = Starlette(routes=routes)
        
        config = uvicorn.Config(
            app=app,
            host=self.config.server_host,
            port=self.config.server_port,
            log_level="warning",  # Reduce uvicorn logging
        )
        
        self.http_server = uvicorn.Server(config)
        await self.http_server.serve()
    
    async def send_webhook_response_to_client(self, session_message: SessionMessage) -> None:
        """Send webhook response to client."""
        if not self.http_client:
            return
        
        # Extract session info from metadata or look it up from request ID
        session_id = None
        client_id = None
        webhook_url = None
        
        if session_message.metadata:
            session_id = session_message.metadata.get("session_id")
            client_id = session_message.metadata.get("client_id")
            webhook_url = session_message.metadata.get("webhook_url")
        
        # If metadata is missing, try to look up session from request ID
        if not all([session_id, client_id, webhook_url]):
            message_root = session_message.message.root
            if isinstance(message_root, (types.JSONRPCResponse, types.JSONRPCError)):
                request_id = str(message_root.id)
                if request_id in self.request_sessions:
                    session_id = self.request_sessions[request_id]
                    if session_id in self.sessions:
                        session_info = self.sessions[session_id]
                        client_id = session_info.client_id
                        webhook_url = session_info.webhook_url
                        logger.debug(f"Looked up session {session_id} from request ID {request_id}")
        
        if not all([session_id, client_id, webhook_url]):
            logger.error("Missing required session information in response message")
            return
        
        try:
            await send_webhook_response(
                self.http_client,
                webhook_url,
                session_message,
                session_id,
                client_id
            )
            logger.debug(f"Webhook response sent to client {client_id}")
            
            # Clean up request-to-session mapping after successful response
            message_root = session_message.message.root
            if isinstance(message_root, (types.JSONRPCResponse, types.JSONRPCError)):
                request_id = str(message_root.id)
                self.request_sessions.pop(request_id, None)
        
        except Exception as e:
            logger.error(f"Failed to send webhook response to client {client_id}: {e}")
    
    async def response_sender(self) -> None:
        """Task that sends responses to clients via webhooks."""
        if not self.write_stream_reader:
            return
        
        async with self.write_stream_reader:
            async for session_message in self.write_stream_reader:
                await self.send_webhook_response_to_client(session_message)
    
    async def start(self) -> None:
        """Start the server."""
        timeout = httpx.Timeout(self.config.timeout_seconds)
        self.http_client = httpx.AsyncClient(timeout=timeout)
        
        async with anyio.create_task_group() as tg:
            # Start HTTP server
            tg.start_soon(self.start_http_server)
            
            # Start response sender
            tg.start_soon(self.response_sender)
            
            # Wait a bit to ensure servers are ready
            await anyio.sleep(0.1)
    
    async def stop(self) -> None:
        """Stop the server."""
        if self.http_client:
            await self.http_client.aclose()
        
        if self.http_server:
            self.http_server.should_exit = True
        
        # Clean up sessions
        self.sessions.clear()
        self.client_sessions.clear()
        self.request_sessions.clear()


@asynccontextmanager
async def webhook_server(
    config: WebhookTransportConfig,
) -> AsyncGenerator[
    tuple[MemoryObjectReceiveStream[SessionMessage | Exception], MemoryObjectSendStream[SessionMessage]],
    None,
]:
    """Create a webhook server transport."""
    server = WebhookServer(config)
    
    # Create streams
    read_stream_writer: MemoryObjectSendStream[SessionMessage | Exception]
    read_stream: MemoryObjectReceiveStream[SessionMessage | Exception]
    read_stream_writer, read_stream = anyio.create_memory_object_stream(0)
    
    write_stream: MemoryObjectSendStream[SessionMessage]
    write_stream_reader: MemoryObjectReceiveStream[SessionMessage]
    write_stream, write_stream_reader = anyio.create_memory_object_stream(0)
    
    # Assign streams to server
    server.read_stream_writer = read_stream_writer
    server.write_stream_reader = write_stream_reader
    
    if config.transport_timeout_seconds is None:
        async with anyio.create_task_group() as tg:
            tg.start_soon(server.start)
            try:
                yield read_stream, write_stream
            finally:
                tg.cancel_scope.cancel()
                await server.stop()
    else:
        with anyio.move_on_after(config.transport_timeout_seconds):
            async with anyio.create_task_group() as tg:
                tg.start_soon(server.start)
                try:
                    yield read_stream, write_stream
                finally:
                    tg.cancel_scope.cancel()
                    await server.stop() 