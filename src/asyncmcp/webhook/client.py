"""
Webhook client transport implementation.

The client sends HTTP POST requests to the server and receives responses via webhooks.
"""

import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

import anyio
import anyio.lowlevel
import httpx
import orjson
from urllib.parse import urlparse
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from collections.abc import AsyncGenerator
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route
import uvicorn
import json

import mcp.types as types
from mcp.shared.message import SessionMessage

from asyncmcp.common.client_state import ClientState
from asyncmcp.webhook.utils import WebhookClientConfig, create_http_headers, parse_webhook_request

logger = logging.getLogger(__name__)


class WebhookClient:
    """Webhook client that sends HTTP requests and receives webhook responses."""

    def __init__(self, config: WebhookClientConfig, state: ClientState, webhook_url: str):
        self.config = config
        self.state = state
        self.webhook_url = webhook_url
        self.server_url = config.server_url

        # HTTP client for sending requests
        self.http_client: Optional[httpx.AsyncClient] = None

        # Webhook server for receiving responses
        self.webhook_server: Optional[Any] = None

        # Stream writer for incoming responses
        self.read_stream_writer: Optional[MemoryObjectSendStream[SessionMessage | Exception]] = None

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

    async def start_webhook_server(self) -> None:
        """Start the webhook server to receive responses."""
        # Extract path from webhook URL for the route
        parsed_url = urlparse(self.webhook_url)
        webhook_path = parsed_url.path

        routes = [
            Route(webhook_path, self.handle_webhook_response, methods=["POST"]),
        ]

        app = Starlette(routes=routes)

        # Extract host and port from webhook URL
        webhook_host = parsed_url.hostname or "0.0.0.0"
        webhook_port = parsed_url.port or 8001

        config = uvicorn.Config(
            app=app,
            host=webhook_host,
            port=webhook_port,
            log_level="warning",
            access_log=False,  # Disable access logs to reduce noise
        )

        self.webhook_server = uvicorn.Server(config)
        try:
            await self.webhook_server.serve()
        except Exception as e:
            logger.error(f"Webhook server crashed: {e}")
            # Send error to read stream if available
            if self.read_stream_writer:
                try:
                    await self.read_stream_writer.send(e)
                except anyio.BrokenResourceError:
                    pass  # Stream already closed
            raise

    async def send_request(self, session_message: SessionMessage) -> None:
        """Send HTTP request to server."""
        if not self.http_client:
            return

        try:
            # For initialize requests, add webhook URL to params
            message_root = session_message.message.root
            json_message = session_message.message.model_dump_json(by_alias=True, exclude_none=True)

            if isinstance(message_root, types.JSONRPCRequest) and message_root.method == "initialize":
                # Add webhook URL to _meta field
                message_dict = session_message.message.model_dump(by_alias=True, exclude_none=True)
                if "params" not in message_dict:
                    message_dict["params"] = {}
                if "_meta" not in message_dict["params"]:
                    message_dict["params"]["_meta"] = {}
                message_dict["params"]["_meta"]["webhookUrl"] = self.webhook_url
                json_message = json.dumps(message_dict)

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

        # Stop webhook server
        if self.webhook_server:
            try:
                self.webhook_server.should_exit = True
            except Exception as e:
                logger.debug(f"Error stopping webhook server: {e}")
            finally:
                self.webhook_server = None

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
    webhook_url: str = "http://localhost:8001/webhook/response",
) -> AsyncGenerator[
    tuple[MemoryObjectReceiveStream[SessionMessage | Exception], MemoryObjectSendStream[SessionMessage]],
    None,
]:
    """Create a webhook client transport."""
    state = ClientState(client_id=config.client_id or f"mcp-client-{uuid.uuid4().hex[:8]}", session_id=None)

    client = WebhookClient(config, state, webhook_url)

    # Create streams
    read_stream_writer: MemoryObjectSendStream[SessionMessage | Exception]
    read_stream: MemoryObjectReceiveStream[SessionMessage | Exception]
    read_stream_writer, read_stream = anyio.create_memory_object_stream(0)

    write_stream: MemoryObjectSendStream[SessionMessage]
    write_stream_reader: MemoryObjectReceiveStream[SessionMessage]
    write_stream, write_stream_reader = anyio.create_memory_object_stream(0)

    # Assign stream writer to client
    client.read_stream_writer = read_stream_writer

    async def webhook_reader():
        """Task that receives webhook responses."""
        # This is handled by the webhook server itself
        pass

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
            tg.start_soon(client.start_webhook_server)
            tg.start_soon(webhook_writer)
            try:
                # Wait a bit to ensure webhook server is ready
                await anyio.sleep(0.1)
                yield read_stream, write_stream
            finally:
                tg.cancel_scope.cancel()
                await client.stop()
    else:
        with anyio.move_on_after(config.transport_timeout_seconds):
            async with anyio.create_task_group() as tg:
                tg.start_soon(client.start_webhook_server)
                tg.start_soon(webhook_writer)
                try:
                    # Wait a bit to ensure webhook server is ready
                    await anyio.sleep(0.1)
                    yield read_stream, write_stream
                finally:
                    tg.cancel_scope.cancel()
                    await client.stop()
