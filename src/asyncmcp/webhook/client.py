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
import orjson
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp.shared.message import SessionMessage
from starlette.requests import Request
from starlette.responses import Response

from asyncmcp.common.base_client import BaseClientTransport
from asyncmcp.common.client_state import ClientState
from asyncmcp.webhook.utils import WebhookClientConfig, create_http_headers, parse_webhook_request

logger = logging.getLogger(__name__)


class WebhookClientTransport(BaseClientTransport):
    """Webhook-specific client transport with MCP protocol compliance."""

    def __init__(self, config: WebhookClientConfig):
        client_id = config.client_id or f"mcp-client-{uuid.uuid4().hex[:8]}"
        state = ClientState(client_id=client_id, session_id=None)
        super().__init__(state)
        self.config = config
        self.server_url = config.server_url

    async def _create_http_headers(self, session_message: SessionMessage) -> dict[str, str]:
        """Create HTTP headers with protocol version support."""
        headers = await create_http_headers(
            session_message, session_id=self.client_state.session_id, client_id=self.client_state.client_id
        )

        # Add protocol version if available
        if self.protocol_version:
            headers["X-Protocol-Version"] = self.protocol_version

        return headers

    async def _prepare_initialize_request(self, session_message: SessionMessage) -> str:
        """Prepare initialize request with webhookUrl validation."""
        if not self._is_initialization_request(session_message.message):
            return session_message.message.model_dump_json(by_alias=True, exclude_none=True)

        message_dict = session_message.message.model_dump(by_alias=True, exclude_none=True)
        params = message_dict.get("params", {})
        meta = params.get("_meta", {})

        if "params" in message_dict and "_meta" in params and "webhookUrl" in meta:
            # Webhook URL provided - use as is
            return json.dumps(message_dict)
        else:
            # External app must set the full webhook URL in _meta
            raise ValueError("webhookUrl is required in initialize request _meta field")

    async def send_message(self, session_message: SessionMessage, http_client: httpx.AsyncClient) -> None:
        """Send HTTP request to server with proper error handling."""
        try:
            json_message = await self._prepare_initialize_request(session_message)
            headers = await self._create_http_headers(session_message)

            response = await http_client.post(
                self.server_url,
                headers=headers,
                content=json_message,
            )
            response.raise_for_status()

        except httpx.HTTPError as e:
            logger.error(f"Failed to send request to {self.server_url}: {e}")
            # Don't raise - let the client continue processing other messages
        except Exception as e:
            logger.error(f"Unexpected error sending request: {e}")
            # Don't raise - let the client continue processing other messages


class WebhookClient:
    """Webhook client that sends HTTP requests and provides callback for webhook responses."""

    def __init__(self, config: WebhookClientConfig, webhook_path: str):
        self.transport = WebhookClientTransport(config)
        self.webhook_path = webhook_path

        # HTTP client for sending requests
        self.http_client: httpx.AsyncClient | None = None

        # Stream writer for incoming responses
        self.read_stream_writer: MemoryObjectSendStream[SessionMessage | Exception] | None = None
        self.read_stream: MemoryObjectReceiveStream[SessionMessage | Exception] | None = None
        self.write_stream: MemoryObjectSendStream[SessionMessage] | None = None

    async def handle_webhook_response(self, request: Request) -> Response:
        """Handle incoming webhook response with MCP protocol compliance."""
        try:
            body = await request.body()
            session_message = await parse_webhook_request(body)

            # Extract session ID from headers for initialize responses only
            received_session_id = request.headers.get("X-Session-ID")

            # Use transport's enhanced message handling
            await self.transport.handle_received_message(session_message.message, received_session_id)

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

    def get_streams(
        self,
    ) -> tuple[MemoryObjectReceiveStream[SessionMessage | Exception], MemoryObjectSendStream[SessionMessage]]:
        """Get direct access to read/write streams for advanced users."""
        if not self.read_stream or not self.write_stream:
            raise RuntimeError("Streams not initialized. Use within webhook_client context manager.")
        return self.read_stream, self.write_stream

    async def send_request(self, session_message: SessionMessage) -> None:
        """Send HTTP request to server using transport."""
        if not self.http_client:
            return

        await self.transport.send_message(session_message, self.http_client)

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
    client = WebhookClient(config, webhook_path)

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
