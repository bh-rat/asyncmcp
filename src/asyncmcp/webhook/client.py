"""
Webhook client transport implementation.

The client sends HTTP POST requests to the server and receives responses via webhooks.
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
    create_http_headers,
    parse_webhook_request,
    extract_webhook_url_from_meta,
)

logger = logging.getLogger(__name__)


class WebhookClient:
    """Webhook client that sends HTTP requests and receives webhook responses."""
    
    def __init__(self, config: WebhookTransportConfig):
        self.config = config
        self.session_id: Optional[str] = None
        self.webhook_url = f"http://{config.webhook_host}:{config.webhook_port}{config.webhook_base_path}/response"
        self.server_url = f"http://{config.server_host}:{config.server_port}{config.server_base_path}/request"
        
        # Streams for communication
        self.read_stream_writer: Optional[MemoryObjectSendStream[SessionMessage | Exception]] = None
        self.write_stream_reader: Optional[MemoryObjectReceiveStream[SessionMessage]] = None
        
        # HTTP client for sending requests
        self.http_client: Optional[httpx.AsyncClient] = None
        
        # Webhook server for receiving responses
        self.webhook_server: Optional[Any] = None
    
    async def handle_webhook_response(self, request: Request) -> Response:
        """Handle incoming webhook response."""
        try:
            body = await request.body()
            session_message = await parse_webhook_request(body)
            
            # Extract session ID from headers
            received_session_id = request.headers.get("X-Session-ID")
            if received_session_id:
                self.session_id = received_session_id
            
            if self.read_stream_writer:
                await self.read_stream_writer.send(session_message)
            
            return Response(
                content=orjson.dumps({"status": "success"}),
                media_type="application/json",
                status_code=200,
            )
        
        except Exception as e:
            logger.error(f"Error handling webhook response: {e}")
            if self.read_stream_writer:
                await self.read_stream_writer.send(e)
            
            return Response(
                content=orjson.dumps({"error": str(e)}),
                media_type="application/json", 
                status_code=400,
            )
    
    async def start_webhook_server(self) -> None:
        """Start the webhook server to receive responses."""
        routes = [
            Route(f"{self.config.webhook_base_path}/response", self.handle_webhook_response, methods=["POST"]),
        ]
        
        app = Starlette(routes=routes)
        
        config = uvicorn.Config(
            app=app,
            host=self.config.webhook_host,
            port=self.config.webhook_port,
            log_level="warning",  # Reduce uvicorn logging
        )
        
        self.webhook_server = uvicorn.Server(config)
        await self.webhook_server.serve()
    
    async def send_request(self, session_message: SessionMessage) -> None:
        """Send HTTP request to server."""
        if not self.http_client:
            return
        
        try:
            # Add webhook URL to _meta field for initialize requests
            message_root = session_message.message.root
            if (isinstance(message_root, types.JSONRPCRequest) and 
                message_root.method == "initialize"):
                
                # Add webhook URL to _meta field
                if isinstance(message_root.params, dict):
                    if "_meta" not in message_root.params:
                        message_root.params["_meta"] = {}
                    message_root.params["_meta"]["webhookUrl"] = self.webhook_url
                else:
                    message_root.params = {
                        "_meta": {"webhookUrl": self.webhook_url}
                    }
            
            headers = await create_http_headers(
                session_message,
                session_id=self.session_id,
                client_id=self.config.client_id
            )
            
            json_body = session_message.message.model_dump_json(by_alias=True, exclude_none=True)
            
            response = await self.http_client.post(
                self.server_url,
                headers=headers,
                content=json_body,
            )
            response.raise_for_status()
            logger.debug(f"Request sent successfully to {self.server_url}")
            
        except httpx.HTTPError as e:
            logger.error(f"Failed to send request to {self.server_url}: {e}")
            if self.read_stream_writer:
                await self.read_stream_writer.send(e)
        except Exception as e:
            logger.error(f"Unexpected error sending request: {e}")
            if self.read_stream_writer:
                await self.read_stream_writer.send(e)
    
    async def request_sender(self) -> None:
        """Task that sends requests to the server."""
        if not self.write_stream_reader:
            return
        
        async with self.write_stream_reader:
            async for session_message in self.write_stream_reader:
                await self.send_request(session_message)
    
    async def start(self) -> None:
        """Start the client."""
        timeout = httpx.Timeout(self.config.timeout_seconds)
        self.http_client = httpx.AsyncClient(timeout=timeout)
        
        async with anyio.create_task_group() as tg:
            # Start webhook server
            tg.start_soon(self.start_webhook_server)
            
            # Start request sender
            tg.start_soon(self.request_sender)
            
            # Wait a bit to ensure servers are ready
            await anyio.sleep(0.1)
    
    async def stop(self) -> None:
        """Stop the client."""
        if self.http_client:
            await self.http_client.aclose()
        
        if self.webhook_server:
            self.webhook_server.should_exit = True


@asynccontextmanager
async def webhook_client(
    config: WebhookTransportConfig,
) -> AsyncGenerator[
    tuple[MemoryObjectReceiveStream[SessionMessage | Exception], MemoryObjectSendStream[SessionMessage]],
    None,
]:
    """Create a webhook client transport."""
    client = WebhookClient(config)
    
    # Create streams
    read_stream_writer: MemoryObjectSendStream[SessionMessage | Exception]
    read_stream: MemoryObjectReceiveStream[SessionMessage | Exception]
    read_stream_writer, read_stream = anyio.create_memory_object_stream(0)
    
    write_stream: MemoryObjectSendStream[SessionMessage]
    write_stream_reader: MemoryObjectReceiveStream[SessionMessage]
    write_stream, write_stream_reader = anyio.create_memory_object_stream(0)
    
    # Assign streams to client
    client.read_stream_writer = read_stream_writer
    client.write_stream_reader = write_stream_reader
    
    if config.transport_timeout_seconds is None:
        async with anyio.create_task_group() as tg:
            tg.start_soon(client.start)
            try:
                yield read_stream, write_stream
            finally:
                tg.cancel_scope.cancel()
                await client.stop()
    else:
        with anyio.move_on_after(config.transport_timeout_seconds):
            async with anyio.create_task_group() as tg:
                tg.start_soon(client.start)
                try:
                    yield read_stream, write_stream
                finally:
                    tg.cancel_scope.cancel()
                    await client.stop() 