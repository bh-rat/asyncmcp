"""
Webhook client transport implementation.

The client sends HTTP POST requests to the server and receives responses via webhooks.
"""

import json
import logging
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import anyio
import anyio.lowlevel
import httpx
import mcp.types as types
import orjson
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp.shared.message import SessionMessage
from starlette.requests import Request
from starlette.responses import Response

from asyncmcp.common.client_state import ClientState
from asyncmcp.webhook.utils import WebhookClientConfig, create_http_headers, parse_webhook_request

logger = logging.getLogger(__name__)


class WebhookClient:
    """Webhook client that sends HTTP requests and provides callback for webhook responses."""

    def __init__(self, config: WebhookClientConfig, state: ClientState, webhook_path: str):
        self.config = config
        self.state = state
        self.webhook_path = webhook_path
        self.server_url = config.server_url

        # HTTP client for sending requests
        self.http_client: httpx.AsyncClient | None = None

        # Stream writer for incoming responses
        self.read_stream_writer: MemoryObjectSendStream[SessionMessage | Exception] | None = None
        self.read_stream: MemoryObjectReceiveStream[SessionMessage | Exception] | None = None
        self.write_stream: MemoryObjectSendStream[SessionMessage] | None = None

    async def handle_webhook_response(self, request: Request) -> Response:
        """Handle incoming webhook response."""
        try:
            body = await request.body()
            session_message = await parse_webhook_request(body)

            # Extract session ID from headers and update state
            received_session_id = request.headers.get("X-Session-ID")
            if received_session_id:
                await self.state.set_session_id_if_none(received_session_id)

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

    async def get_webhook_callback(self):
        """Get callback function for external app integration."""
        return self.handle_webhook_response
        
    def get_streams(self) -> tuple[
        MemoryObjectReceiveStream[SessionMessage | Exception], MemoryObjectSendStream[SessionMessage]
    ]:
        """Get direct access to read/write streams for advanced users."""
        if not self.read_stream or not self.write_stream:
            raise RuntimeError("Streams not initialized. Use within webhook_client context manager.")
        return self.read_stream, self.write_stream

    async def send_request(self, session_message: SessionMessage) -> None:
        """Send HTTP request to server."""
        if not self.http_client:
            return

        try:
            # For initialize requests, add webhook URL to params
            message_root = session_message.message.root
            json_message = session_message.message.model_dump_json(by_alias=True, exclude_none=True)

            if isinstance(message_root, types.JSONRPCRequest) and message_root.method == "initialize":
                # For initialize requests, verify webhook URL is provided in _meta field
                message_dict = session_message.message.model_dump(by_alias=True, exclude_none=True)
                if "params" in message_dict and "_meta" in message_dict["params"] and "webhookUrl" in message_dict["params"]["_meta"]:
                    # Webhook URL already provided - use as is
                    json_message = json.dumps(message_dict)
                else:
                    # External app should set the full webhook URL in _meta
                    # For backwards compatibility, log a warning but continue
                    logger.warning("webhookUrl not provided in initialize request _meta field. External app should set this.")
                    json_message = session_message.message.model_dump_json(by_alias=True, exclude_none=True)

            headers = await create_http_headers(
                session_message, session_id=self.state.session_id, client_id=self.state.client_id
            )

            response = await self.http_client.post(
                self.server_url,
                headers=headers,
                content=json_message,
            )
            response.raise_for_status()

        except httpx.HTTPError as e:
            logger.error(f"Failed to send request to {self.server_url}: {e}")
            if self.read_stream_writer:
                await self.read_stream_writer.send(e)
        except Exception as e:
            logger.error(f"Unexpected error sending request: {e}")
            if self.read_stream_writer:
                await self.read_stream_writer.send(e)

    async def stop(self) -> None:
        """Stop the client and clean up resources."""
        # Close HTTP client
        if self.http_client:
            try:
                await self.http_client.aclose()
            except Exception as e:
                logger.debug(f"Error closing HTTP client: {e}")
            finally:
                self.http_client = None

        # Close stream writer if available
        if self.read_stream_writer:
            try:
                await self.read_stream_writer.aclose()
            except Exception as e:
                logger.debug(f"Error closing stream writer: {e}")
            finally:
                self.read_stream_writer = None


@asynccontextmanager
async def webhook_client(
    config: WebhookClientConfig,
    webhook_path: str = "/webhook/response",
) -> AsyncGenerator[
    tuple[MemoryObjectReceiveStream[SessionMessage | Exception], MemoryObjectSendStream[SessionMessage], WebhookClient],
    None,
]:
    """Create a webhook client transport.
    
    Returns:
        A tuple of (read_stream, write_stream, client) where:
        - read_stream: Receives messages from the server
        - write_stream: Sends messages to the server  
        - client: WebhookClient instance with get_webhook_callback() method
        
    Example:
        async with webhook_client(config, "/webhook/mcp") as (read, write, client):
            # Get callback for your web app
            callback = await client.get_webhook_callback()
            # Add to your routes: app.add_route("/webhook/mcp", callback, methods=["POST"])
    """
    state = ClientState(client_id=config.client_id or f"mcp-client-{uuid.uuid4().hex[:8]}", session_id=None)

    client = WebhookClient(config, state, webhook_path)

    # Create streams
    read_stream_writer: MemoryObjectSendStream[SessionMessage | Exception]
    read_stream: MemoryObjectReceiveStream[SessionMessage | Exception]
    read_stream_writer, read_stream = anyio.create_memory_object_stream(0)

    write_stream: MemoryObjectSendStream[SessionMessage]
    write_stream_reader: MemoryObjectReceiveStream[SessionMessage]
    write_stream, write_stream_reader = anyio.create_memory_object_stream(0)

    # Assign streams to client
    client.read_stream_writer = read_stream_writer
    client.read_stream = read_stream
    client.write_stream = write_stream

    async def webhook_writer():
        """Task that sends requests to the server."""
        async with write_stream_reader:
            async for session_message in write_stream_reader:
                await anyio.lowlevel.checkpoint()
                await client.send_request(session_message)

    # Initialize HTTP client
    timeout = httpx.Timeout(config.timeout_seconds)
    client.http_client = httpx.AsyncClient(timeout=timeout)

    if config.transport_timeout_seconds is None:
        async with anyio.create_task_group() as tg:
            tg.start_soon(webhook_writer)
            try:
                yield read_stream, write_stream, client
            finally:
                tg.cancel_scope.cancel()
                await client.stop()
    else:
        with anyio.move_on_after(config.transport_timeout_seconds):
            async with anyio.create_task_group() as tg:
                tg.start_soon(webhook_writer)
                try:
                    yield read_stream, write_stream, client
                finally:
                    tg.cancel_scope.cancel()
                    await client.stop()
