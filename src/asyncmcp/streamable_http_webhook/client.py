"""
Streamable HTTP + Webhook client transport implementation.

This module provides client-side transport for StreamableHTTP + Webhook,
combining full StreamableHTTP capabilities with webhook handling for marked tools.
"""

import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Optional, Set, Tuple

import anyio
import httpx
import orjson
from anyio.abc import TaskGroup
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from httpx_sse import EventSource, ServerSentEvent, aconnect_sse
from mcp.shared._httpx_utils import McpHttpClientFactory, create_mcp_http_client
from mcp.shared.message import ClientMessageMetadata, SessionMessage
from mcp.types import (
    ErrorData,
    InitializeResult,
    JSONRPCError,
    JSONRPCMessage,
    JSONRPCNotification,
    JSONRPCRequest,
    JSONRPCResponse,
    RequestId,
)
from starlette.requests import Request
from starlette.responses import Response

from .utils import (
    CONTENT_TYPE_JSON,
    CONTENT_TYPE_SSE,
    LAST_EVENT_ID_HEADER,
    MCP_PROTOCOL_VERSION_HEADER,
    MCP_SESSION_ID_HEADER,
    StreamableHTTPWebhookClientConfig,
    parse_webhook_request,
)

logger = logging.getLogger(__name__)

# Type aliases matching the reference implementation
SessionMessageOrError = SessionMessage | Exception
StreamWriter = MemoryObjectSendStream[SessionMessageOrError]
StreamReader = MemoryObjectReceiveStream[SessionMessage]
GetSessionIdCallback = Callable[[], str | None]

# Constants from reference implementation
ACCEPT = "Accept"
CONTENT_TYPE = "content-type"


class StreamableHTTPWebhookError(Exception):
    """Base exception for StreamableHTTP + Webhook transport errors."""


class ResumptionError(StreamableHTTPWebhookError):
    """Raised when resumption request is invalid."""


@dataclass
class RequestContext:
    """Context for a request operation."""

    client: httpx.AsyncClient
    headers: dict[str, str]
    session_id: str | None
    session_message: SessionMessage
    metadata: ClientMessageMetadata | None
    read_stream_writer: StreamWriter
    sse_read_timeout: float
    webhook_tools: Set[str]


class StreamableHTTPWebhookTransport:
    """StreamableHTTP + Webhook client transport implementation."""

    def __init__(
        self,
        url: str,
        webhook_url: str,
        client_id: str | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | timedelta = 30,
        sse_read_timeout: float | timedelta = 60 * 5,
        auth: httpx.Auth | None = None,
    ) -> None:
        """
        Initialize the StreamableHTTP + Webhook transport.

        Args:
            url: The server endpoint URL.
            webhook_url: The client webhook URL for tool responses.
            client_id: Client identifier for the session.
            headers: Optional headers to include in requests.
            timeout: HTTP timeout for regular operations.
            sse_read_timeout: Timeout for SSE read operations.
            auth: Optional HTTPX authentication handler.
        """
        self.url = url
        self.webhook_url = webhook_url
        self.client_id = client_id
        self.headers = headers or {}
        self.timeout = timeout.total_seconds() if isinstance(timeout, timedelta) else timeout
        self.sse_read_timeout = (
            sse_read_timeout.total_seconds() if isinstance(sse_read_timeout, timedelta) else sse_read_timeout
        )
        self.auth = auth
        self.session_id = None
        self.protocol_version = None
        self.webhook_tools: Set[str] = set()
        self.request_headers = {
            ACCEPT: f"{CONTENT_TYPE_JSON}, {CONTENT_TYPE_SSE}",
            CONTENT_TYPE: CONTENT_TYPE_JSON,
            **self.headers,
        }

        # Add client ID to headers if provided
        if self.client_id:
            self.request_headers["X-Client-ID"] = self.client_id

    def _prepare_request_headers(self, base_headers: dict[str, str]) -> dict[str, str]:
        """Update headers with session ID and protocol version if available."""
        headers = base_headers.copy()
        if self.session_id:
            headers[MCP_SESSION_ID_HEADER] = self.session_id
        if self.protocol_version:
            headers[MCP_PROTOCOL_VERSION_HEADER] = self.protocol_version
        return headers

    def _is_initialization_request(self, message: JSONRPCMessage) -> bool:
        """Check if the message is an initialization request."""
        return isinstance(message.root, JSONRPCRequest) and message.root.method == "initialize"

    def _is_initialized_notification(self, message: JSONRPCMessage) -> bool:
        """Check if the message is an initialized notification."""
        return isinstance(message.root, JSONRPCNotification) and message.root.method == "notifications/initialized"

    def _maybe_extract_session_id_from_response(
        self,
        response: httpx.Response,
    ) -> None:
        """Extract and store session ID from response headers."""
        new_session_id = response.headers.get(MCP_SESSION_ID_HEADER)
        if new_session_id:
            self.session_id = new_session_id

    def _maybe_extract_protocol_version_from_message(
        self,
        message: JSONRPCMessage,
    ) -> None:
        """Extract protocol version from initialization response message."""
        if isinstance(message.root, JSONRPCResponse) and message.root.result:
            try:
                # Parse the result as InitializeResult for type safety
                init_result = InitializeResult.model_validate(message.root.result)
                self.protocol_version = str(init_result.protocolVersion)
            except Exception as exc:
                logger.warning(f"Failed to parse initialization response as InitializeResult: {exc}")
                logger.warning(f"Raw result: {message.root.result}")

    def _prepare_initialize_request(self, session_message: SessionMessage) -> dict[str, Any]:
        """Prepare initialize request with webhook URL and tools."""
        if not self._is_initialization_request(session_message.message):
            return session_message.message.model_dump(by_alias=True, mode="json", exclude_none=True)

        message_dict = session_message.message.model_dump(by_alias=True, mode="json", exclude_none=True)
        params = message_dict.get("params", {})
        meta = params.get("_meta", {})

        # Add webhook URL to _meta
        meta["webhookUrl"] = self.webhook_url

        params["_meta"] = meta
        message_dict["params"] = params

        return message_dict

    def set_webhook_tools(self, webhook_tools: Set[str]) -> None:
        """Set the webhook tools for this transport."""
        self.webhook_tools = webhook_tools
        logger.debug(f"Updated webhook tools: {webhook_tools}")

    async def _handle_sse_event(
        self,
        sse: ServerSentEvent,
        read_stream_writer: StreamWriter,
        original_request_id: RequestId | None = None,
        resumption_callback: Callable[[str], Awaitable[None]] | None = None,
        is_initialization: bool = False,
    ) -> bool:
        """Handle an SSE event, returning True if the response is complete."""
        if sse.event == "message":
            try:
                message = JSONRPCMessage.model_validate_json(sse.data)
                logger.debug(f"SSE message: {message}")

                # Extract protocol version from initialization response
                if is_initialization:
                    self._maybe_extract_protocol_version_from_message(message)

                # If this is a response and we have original_request_id, replace it
                if original_request_id is not None and isinstance(message.root, JSONRPCResponse | JSONRPCError):
                    message.root.id = original_request_id

                session_message = SessionMessage(message)
                await read_stream_writer.send(session_message)

                # Call resumption token callback if we have an ID
                if sse.id and resumption_callback:
                    await resumption_callback(sse.id)

                # If this is a response or error return True indicating completion
                # Otherwise, return False to continue listening
                return isinstance(message.root, JSONRPCResponse | JSONRPCError)

            except Exception as exc:
                logger.exception("Error parsing SSE message")
                await read_stream_writer.send(exc)
                return False
        else:
            logger.warning(f"Unknown SSE event: {sse.event}")
            return False

    async def handle_get_stream(
        self,
        client: httpx.AsyncClient,
        read_stream_writer: StreamWriter,
    ) -> None:
        """Handle GET stream for server-initiated messages."""
        try:
            if not self.session_id:
                return

            headers = self._prepare_request_headers(self.request_headers)

            async with aconnect_sse(
                client,
                "GET",
                self.url,
                headers=headers,
                timeout=httpx.Timeout(self.timeout, read=self.sse_read_timeout),
            ) as event_source:
                event_source.response.raise_for_status()
                logger.debug("GET SSE connection established")

                async for sse in event_source.aiter_sse():
                    await self._handle_sse_event(sse, read_stream_writer)

        except Exception as exc:
            logger.debug(f"GET stream error (non-fatal): {exc}")

    async def _handle_resumption_request(self, ctx: RequestContext) -> None:
        """Handle a resumption request using GET with SSE."""
        headers = self._prepare_request_headers(ctx.headers)
        if ctx.metadata and ctx.metadata.resumption_token:
            headers[LAST_EVENT_ID_HEADER] = ctx.metadata.resumption_token
        else:
            raise ResumptionError("Resumption request requires a resumption token")

        # Extract original request ID to map responses
        original_request_id = None
        if isinstance(ctx.session_message.message.root, JSONRPCRequest):
            original_request_id = ctx.session_message.message.root.id

        async with aconnect_sse(
            ctx.client,
            "GET",
            self.url,
            headers=headers,
            timeout=httpx.Timeout(self.timeout, read=self.sse_read_timeout),
        ) as event_source:
            event_source.response.raise_for_status()
            logger.debug("Resumption GET SSE connection established")

            async for sse in event_source.aiter_sse():
                is_complete = await self._handle_sse_event(
                    sse,
                    ctx.read_stream_writer,
                    original_request_id,
                    ctx.metadata.on_resumption_token_update if ctx.metadata else None,
                )
                if is_complete:
                    break

    async def _handle_post_request(self, ctx: RequestContext) -> None:
        """Handle a POST request with response processing."""
        headers = self._prepare_request_headers(ctx.headers)
        message = ctx.session_message.message
        is_initialization = self._is_initialization_request(message)

        # Prepare the request body - handle initialization specially
        if is_initialization:
            request_data = self._prepare_initialize_request(ctx.session_message)
        else:
            request_data = message.model_dump(by_alias=True, mode="json", exclude_none=True)

        # Log request for debugging
        logger.debug(f"Sending POST request to {self.url}")
        logger.debug(f"Headers: {headers}")
        logger.debug(f"Request data: {request_data}")

        async with ctx.client.stream(
            "POST",
            self.url,
            json=request_data,
            headers=headers,
        ) as response:
            if response.status_code == 202:
                logger.debug("Received 202 Accepted")
                return

            if response.status_code == 404:
                if isinstance(message.root, JSONRPCRequest):
                    await self._send_session_terminated_error(
                        ctx.read_stream_writer,
                        message.root.id,
                    )
                return

            response.raise_for_status()
            if is_initialization:
                self._maybe_extract_session_id_from_response(response)

            content_type = response.headers.get(CONTENT_TYPE, "").lower()

            if content_type.startswith(CONTENT_TYPE_JSON):
                await self._handle_json_response(response, ctx.read_stream_writer, is_initialization)
            elif content_type.startswith(CONTENT_TYPE_SSE):
                await self._handle_sse_response(response, ctx, is_initialization)
            else:
                await self._handle_unexpected_content_type(
                    content_type,
                    ctx.read_stream_writer,
                )

    async def _handle_json_response(
        self,
        response: httpx.Response,
        read_stream_writer: StreamWriter,
        is_initialization: bool = False,
    ) -> None:
        """Handle JSON response from the server."""
        try:
            content = await response.aread()
            message = JSONRPCMessage.model_validate_json(content)

            # Extract protocol version from initialization response
            if is_initialization:
                self._maybe_extract_protocol_version_from_message(message)

            session_message = SessionMessage(message)
            await read_stream_writer.send(session_message)
        except Exception as exc:
            logger.exception("Error parsing JSON response")
            await read_stream_writer.send(exc)

    async def _handle_sse_response(
        self,
        response: httpx.Response,
        ctx: RequestContext,
        is_initialization: bool = False,
    ) -> None:
        """Handle SSE response from the server."""
        try:
            event_source = EventSource(response)
            async for sse in event_source.aiter_sse():
                is_complete = await self._handle_sse_event(
                    sse,
                    ctx.read_stream_writer,
                    resumption_callback=(ctx.metadata.on_resumption_token_update if ctx.metadata else None),
                    is_initialization=is_initialization,
                )
                # If the SSE event indicates completion, like returning response/error
                # break the loop
                if is_complete:
                    break
        except Exception as e:
            logger.exception("Error reading SSE stream:")
            await ctx.read_stream_writer.send(e)

    async def _handle_unexpected_content_type(
        self,
        content_type: str,
        read_stream_writer: StreamWriter,
    ) -> None:
        """Handle unexpected content type in response."""
        error_msg = f"Unexpected content type: {content_type}"
        logger.error(error_msg)
        await read_stream_writer.send(ValueError(error_msg))

    async def _send_session_terminated_error(
        self,
        read_stream_writer: StreamWriter,
        request_id: RequestId,
    ) -> None:
        """Send a session terminated error response."""
        jsonrpc_error = JSONRPCError(
            jsonrpc="2.0",
            id=request_id,
            error=ErrorData(code=32600, message="Session terminated"),
        )
        session_message = SessionMessage(JSONRPCMessage(jsonrpc_error))
        await read_stream_writer.send(session_message)

    async def post_writer(
        self,
        client: httpx.AsyncClient,
        write_stream_reader: StreamReader,
        read_stream_writer: StreamWriter,
        write_stream: MemoryObjectSendStream[SessionMessage],
        start_get_stream: Callable[[], None],
        tg: TaskGroup,
    ) -> None:
        """Handle writing requests to the server."""
        try:
            async with write_stream_reader:
                async for session_message in write_stream_reader:
                    message = session_message.message
                    metadata = (
                        session_message.metadata
                        if isinstance(session_message.metadata, ClientMessageMetadata)
                        else None
                    )

                    # Check if this is a resumption request
                    is_resumption = bool(metadata and metadata.resumption_token)

                    logger.debug(f"Sending client message: {message}")

                    # Handle initialized notification
                    if self._is_initialized_notification(message):
                        start_get_stream()

                    ctx = RequestContext(
                        client=client,
                        headers=self.request_headers,
                        session_id=self.session_id,
                        session_message=session_message,
                        metadata=metadata,
                        read_stream_writer=read_stream_writer,
                        sse_read_timeout=self.sse_read_timeout,
                        webhook_tools=self.webhook_tools,
                    )

                    async def handle_request_async():
                        if is_resumption:
                            await self._handle_resumption_request(ctx)
                        else:
                            await self._handle_post_request(ctx)

                    # If this is a request, start a new task to handle it
                    if isinstance(message.root, JSONRPCRequest):
                        tg.start_soon(handle_request_async)
                    else:
                        await handle_request_async()

        except Exception:
            logger.exception("Error in post_writer")
        finally:
            await read_stream_writer.aclose()
            await write_stream.aclose()

    async def terminate_session(self, client: httpx.AsyncClient) -> None:
        """Terminate the session by sending a DELETE request."""
        if not self.session_id:
            return

        try:
            headers = self._prepare_request_headers(self.request_headers)
            response = await client.delete(self.url, headers=headers)

            if response.status_code == 405:
                logger.debug("Server does not allow session termination")
            elif response.status_code not in (200, 204):
                logger.warning(f"Session termination failed: {response.status_code}")
        except Exception as exc:
            logger.warning(f"Session termination failed: {exc}")

    def get_session_id(self) -> str | None:
        """Get the current session ID."""
        return self.session_id


class StreamableHTTPWebhookClient:
    """
    High-level client for StreamableHTTP + Webhook transport.

    Provides both HTTP streaming and webhook handling in a single client.
    """

    def __init__(self, config: StreamableHTTPWebhookClientConfig, webhook_path: str = "/webhook"):
        """
        Initialize the client.

        Args:
            config: Client configuration
            webhook_path: Path for webhook endpoint (for reference)
        """
        self.transport = StreamableHTTPWebhookTransport(
            url=config.server_url,
            webhook_url=config.webhook_url,
            timeout=config.timeout_seconds,
        )
        self.webhook_path = webhook_path

        # HTTP client for sending requests
        self.http_client: Optional[httpx.AsyncClient] = None

        # Streams for communication
        self.read_stream_writer: Optional[MemoryObjectSendStream[SessionMessage | Exception]] = None
        self.read_stream: Optional[MemoryObjectReceiveStream[SessionMessage | Exception]] = None
        self.write_stream: Optional[MemoryObjectSendStream[SessionMessage]] = None

    async def handle_webhook_request(self, request: Request) -> Response:
        """
        Handle incoming webhook request with MCP protocol compliance.

        This method should be integrated with your web framework to handle
        webhook deliveries from the server.
        """
        try:
            body = await request.body()
            session_message = parse_webhook_request(body)

            # Extract session ID from headers
            received_session_id = request.headers.get(MCP_SESSION_ID_HEADER)

            # Validate session ID matches
            if received_session_id and received_session_id != self.transport.session_id:
                logger.warning(f"Session ID mismatch: expected {self.transport.session_id}, got {received_session_id}")

            # Forward to read stream - remove old transport method call
            if self.read_stream_writer:
                await self.read_stream_writer.send(session_message)

            return Response(
                content=orjson.dumps({"status": "success"}),
                media_type="application/json",
                status_code=200,
            )

        except Exception as e:
            logger.error(f"Error handling webhook request: {e}")
            if self.read_stream_writer:
                await self.read_stream_writer.send(e)

            return Response(
                content=orjson.dumps({"error": str(e)}),
                media_type="application/json",
                status_code=400,
            )

    async def get_webhook_callback(self):
        """Get callback function for external app integration."""
        return self.handle_webhook_request

    def get_streams(
        self,
    ) -> Tuple[MemoryObjectReceiveStream[SessionMessage | Exception], MemoryObjectSendStream[SessionMessage]]:
        """Get direct access to read/write streams for advanced users."""
        if not self.read_stream or not self.write_stream:
            raise RuntimeError("Streams not initialized. Use within client context manager.")
        return self.read_stream, self.write_stream

    async def send_request(self, session_message: SessionMessage) -> None:
        """Send HTTP request to server using write stream."""
        # This will be handled by the write stream and post_writer task
        # No direct sending needed - the context manager handles this
        pass

    def set_webhook_tools(self, webhook_tools: Set[str]) -> None:
        """Set the webhook tools for this client."""
        self.transport.set_webhook_tools(webhook_tools)
        logger.debug(f"Updated webhook tools: {webhook_tools}")

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
async def streamable_http_webhook_client(
    config: StreamableHTTPWebhookClientConfig,
    webhook_path: str = "/webhook",
    terminate_on_close: bool = True,
    httpx_client_factory: McpHttpClientFactory = create_mcp_http_client,
) -> AsyncGenerator[
    Tuple[
        MemoryObjectReceiveStream[SessionMessage | Exception],
        MemoryObjectSendStream[SessionMessage],
        StreamableHTTPWebhookClient,
    ],
    None,
]:
    """
    Create a StreamableHTTP + Webhook client transport.

    Returns:
        A tuple of (read_stream, write_stream, client) where:
        - read_stream: Receives messages from the server (both HTTP and webhook)
        - write_stream: Sends messages to the server
        - client: StreamableHTTPWebhookClient instance with get_webhook_callback() method

    Example:
        async with streamable_http_webhook_client(config) as (read, write, client):
            # Get callback for your web app
            callback = await client.get_webhook_callback()
            # Add to your routes: app.add_route("/webhook", callback, methods=["POST"])

            # Use read/write streams for standard MCP communication
            # Webhook tool results will arrive via the webhook callback
    """
    # Create transport
    transport = StreamableHTTPWebhookTransport(
        url=config.server_url,
        webhook_url=config.webhook_url,
        client_id=config.client_id,
        timeout=config.timeout_seconds,
    )

    # Create client wrapper
    client = StreamableHTTPWebhookClient(config, webhook_path)
    client.transport = transport

    # Create streams
    read_stream_writer, read_stream = anyio.create_memory_object_stream[SessionMessage | Exception](0)
    write_stream, write_stream_reader = anyio.create_memory_object_stream[SessionMessage](0)

    # Assign streams to client
    client.read_stream_writer = read_stream_writer
    client.read_stream = read_stream
    client.write_stream = write_stream

    async with anyio.create_task_group() as tg:
        try:
            logger.debug(f"Connecting to StreamableHTTP + Webhook endpoint: {config.server_url}")

            async with httpx_client_factory(
                headers=transport.request_headers,
                timeout=httpx.Timeout(transport.timeout, read=transport.sse_read_timeout),
                auth=transport.auth,
            ) as http_client:
                client.http_client = http_client

                def start_get_stream() -> None:
                    tg.start_soon(transport.handle_get_stream, http_client, read_stream_writer)

                tg.start_soon(
                    transport.post_writer,
                    http_client,
                    write_stream_reader,
                    read_stream_writer,
                    write_stream,
                    start_get_stream,
                    tg,
                )

                try:
                    yield (
                        read_stream,
                        write_stream,
                        client,
                    )
                finally:
                    if transport.session_id and terminate_on_close:
                        await transport.terminate_session(http_client)
                    tg.cancel_scope.cancel()
        finally:
            await read_stream_writer.aclose()
            await write_stream.aclose()
