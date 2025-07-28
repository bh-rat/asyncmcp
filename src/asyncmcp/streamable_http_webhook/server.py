"""
Streamable HTTP + Webhook server transport implementation.

This transport extends AsyncMCP's ServerTransport base class while providing
full MCP StreamableHTTP compatibility plus selective webhook tool routing.
"""

import json
import logging
from http import HTTPStatus
from typing import Awaitable, Callable, Dict, Optional, Set, Tuple

import anyio
import httpx
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp.server.transport_security import TransportSecurityMiddleware
from mcp.shared.message import ServerMessageMetadata, SessionMessage
from mcp.shared.version import SUPPORTED_PROTOCOL_VERSIONS
from mcp.types import (
    DEFAULT_NEGOTIATED_VERSION,
    INTERNAL_ERROR,
    INVALID_PARAMS,
    PARSE_ERROR,
    JSONRPCError,
    JSONRPCMessage,
    JSONRPCNotification,
    JSONRPCRequest,
    JSONRPCResponse,
    RequestId,
)
from pydantic import ValidationError
from sse_starlette import EventSourceResponse
from starlette.requests import Request
from starlette.types import Receive, Scope, Send

from asyncmcp.common.outgoing_event import OutgoingMessageEvent
from asyncmcp.common.server import ServerTransport

from .routing import ToolRouter, extract_tool_name_from_request
from .utils import (
    CONTENT_TYPE_JSON,
    CONTENT_TYPE_SSE,
    MCP_PROTOCOL_VERSION_HEADER,
    MCP_SESSION_ID_HEADER,
    EventMessage,
    ResponseType,
    StreamableHTTPWebhookConfig,
    create_error_response,
    create_event_data,
    create_json_response,
    send_webhook_response,
)

logger = logging.getLogger(__name__)

# Special key for the standalone GET stream
GET_STREAM_KEY = "_GET_stream"


class StreamableHTTPWebhookTransport(ServerTransport):
    """
    StreamableHTTP transport with webhook tool support.

    Extends AsyncMCP's ServerTransport while providing full MCP StreamableHTTP
    compatibility including SSE streaming, JSON responses, session management,
    and adds selective webhook routing for marked tools.
    """

    def __init__(
        self,
        config: StreamableHTTPWebhookConfig,
        http_client: httpx.AsyncClient,
        session_id: Optional[str],
        webhook_url: Optional[str] = None,
        webhook_tools: Optional[Set[str]] = None,
        outgoing_message_sender: Optional[MemoryObjectSendStream[OutgoingMessageEvent]] = None,
        on_initialized: Optional[Callable[[str], Awaitable[None]]] = None,
    ):
        """
        Initialize StreamableHTTP + Webhook transport.

        Args:
            config: Transport configuration
            http_client: HTTP client for webhook delivery
            session_id: MCP session identifier
            webhook_url: Client webhook URL for tool results
            webhook_tools: Set of tools that should use webhook delivery
            outgoing_message_sender: Stream for outgoing message events
        """
        super().__init__(config, session_id, outgoing_message_sender)
        self.http_client = http_client
        self.webhook_url = webhook_url
        self.webhook_tools = webhook_tools or set()
        self.on_initialized = on_initialized
        self.initialization_complete = False  # Track local initialization state

        # Tool router for message routing decisions
        self.tool_router = ToolRouter(self.webhook_tools, self._get_response_type)

        # Request streams for SSE (like MCP StreamableHTTP)
        self._request_streams: Dict[
            RequestId,
            Tuple[
                MemoryObjectSendStream[EventMessage],
                MemoryObjectReceiveStream[EventMessage],
                ResponseType,
            ],
        ] = {}

        # MCP StreamableHTTP compatibility features
        self.is_json_response_enabled = config.json_response

        logger.debug(
            f"StreamableHTTPWebhookTransport initialized with session {session_id}, "
            f"webhook_url={webhook_url}, webhook_tools={webhook_tools}"
        )

    def set_webhook_url(self, webhook_url: str) -> None:
        """Set the client-specific webhook URL."""
        self.webhook_url = webhook_url
        logger.debug(f"Updated webhook URL for session {self.session_id}: {webhook_url}")

    def update_webhook_tools(self, webhook_tools: Set[str]) -> None:
        """Update the set of webhook tools."""
        self.webhook_tools = webhook_tools
        self.tool_router = ToolRouter(webhook_tools, self._get_response_type)
        logger.debug(f"Updated webhook tools for session {self.session_id}: {webhook_tools}")

    def _get_response_type(self, request_id: str) -> Optional[str]:
        """Get the response type for a given request ID."""
        if request_id in self._request_streams:
            return self._request_streams[request_id][2]
        return None

    async def handle_request(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Application entry point that handles all HTTP requests (matches MCP StreamableHTTP interface)."""
        await self.handle_http_request(scope, receive, send)

    async def handle_http_request(self, scope: Scope, receive: Receive, send: Send) -> None:
        """
        Handle HTTP requests (POST, GET, DELETE) with full MCP compatibility.

        This method provides the same HTTP handling as MCP's StreamableHTTP
        while adding webhook routing for specific tool results.
        """
        request = Request(scope, receive)

        # Validate request headers for DNS rebinding protection (if configured)
        if self.config.security_settings:
            security = TransportSecurityMiddleware(self.config.security_settings)
            is_post = request.method == "POST"
            error_response = await security.validate_request(request, is_post=is_post)
            if error_response:
                await error_response(scope, receive, send)
                return

        if self._terminated:
            # If the session has been terminated, return 404 Not Found
            response = create_error_response(
                "Not Found: Session has been terminated",
                HTTPStatus.NOT_FOUND,
                session_id=self.session_id,
            )
            await response(scope, receive, send)
            return

        if request.method == "POST":
            await self._handle_post_request(request, scope, receive, send)
        elif request.method == "GET":
            await self._handle_get_request(request, scope, receive, send)
        elif request.method == "DELETE":
            await self._handle_delete_request(request, scope, receive, send)
        else:
            await self._handle_unsupported_method(request, scope, receive, send)

    def _check_accept_headers(self, request: Request) -> Tuple[bool, bool]:
        """Check if the request accepts the required media types."""
        accept_header = request.headers.get("accept", "")
        accept_types = [media_type.strip() for media_type in accept_header.split(",")]

        has_json = any(media_type.startswith(CONTENT_TYPE_JSON) for media_type in accept_types)
        has_sse = any(media_type.startswith(CONTENT_TYPE_SSE) for media_type in accept_types)

        return has_json, has_sse

    def _check_content_type(self, request: Request) -> bool:
        """Check if the request has the correct Content-Type."""
        content_type = request.headers.get("content-type", "")
        content_type_parts = [part.strip() for part in content_type.split(";")[0].split(",")]

        return any(part == CONTENT_TYPE_JSON for part in content_type_parts)

    async def _handle_post_request(self, request: Request, scope: Scope, receive: Receive, send: Send) -> None:
        """Handle POST requests containing JSON-RPC messages."""
        writer = self._read_stream_writer
        if writer is None:
            raise ValueError("No read stream writer available. Ensure connect() is called first.")

        try:
            # Check Accept headers
            has_json, has_sse = self._check_accept_headers(request)
            logger.debug(f"Request Accept header: {request.headers.get('accept', 'missing')}")
            logger.debug(f"Has JSON: {has_json}, Has SSE: {has_sse}")
            if not (has_json and has_sse):
                response = create_error_response(
                    "Not Acceptable: Client must accept both application/json and text/event-stream",
                    HTTPStatus.NOT_ACCEPTABLE,
                    session_id=self.session_id,
                )
                await response(scope, receive, send)
                return

            # Validate Content-Type
            content_type = request.headers.get("content-type", "missing")
            logger.debug(f"Request Content-Type header: {content_type}")
            if not self._check_content_type(request):
                response = create_error_response(
                    "Unsupported Media Type: Content-Type must be application/json",
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                    session_id=self.session_id,
                )
                await response(scope, receive, send)
                return

            # Parse the body
            body = await request.body()

            try:
                raw_message = json.loads(body)
            except json.JSONDecodeError as e:
                response = create_error_response(
                    f"Parse error: {str(e)}",
                    HTTPStatus.BAD_REQUEST,
                    PARSE_ERROR,
                    session_id=self.session_id,
                )
                await response(scope, receive, send)
                return

            try:
                message = JSONRPCMessage.model_validate(raw_message)
            except ValidationError as e:
                response = create_error_response(
                    f"Validation error: {str(e)}",
                    HTTPStatus.BAD_REQUEST,
                    INVALID_PARAMS,
                    session_id=self.session_id,
                )
                await response(scope, receive, send)
                return

            # Check if this is an initialization request
            is_initialization_request = isinstance(message.root, JSONRPCRequest) and message.root.method == "initialize"
            is_initialized_notification = (
                isinstance(message.root, JSONRPCNotification) and message.root.method == "notifications/initialized"
            )

            if is_initialization_request:
                # Extract webhook information from initialize request
                await self._handle_initialize_message(message)

                # Handle session validation for initialize requests
                if self.session_id:
                    request_session_id = request.headers.get(MCP_SESSION_ID_HEADER)
                    if request_session_id and request_session_id != self.session_id:
                        response = create_error_response(
                            "Not Found: Invalid or expired session ID",
                            HTTPStatus.NOT_FOUND,
                            session_id=self.session_id,
                        )
                        await response(scope, receive, send)
                        return
            elif not await self._validate_request_headers(request, send):
                return
            elif not is_initialized_notification and not self.initialization_complete and self.session_id:
                # Check if initialization is complete for non-init requests
                logger.warning(
                    f"Failed to validate request: Received request before initialization was complete "
                    f"for session {self.session_id}"
                )
                response = create_error_response(
                    "Invalid request: Session not fully initialized",
                    HTTPStatus.BAD_REQUEST,
                    error_code=-32602,  # Invalid params error code
                    session_id=self.session_id,
                )
                await response(scope, receive, send)
                return

            # For notifications, return 202 Accepted
            if not isinstance(message.root, JSONRPCRequest):
                response = create_json_response(
                    response_message=None,
                    session_id=self.session_id,
                    status_code=HTTPStatus.ACCEPTED,
                )
                await response(scope, receive, send)

                # Check if this is the initialized notification
                if isinstance(message.root, JSONRPCNotification) and message.root.method == "notifications/initialized":
                    # Mark initialization as complete locally
                    self.initialization_complete = True
                    logger.debug(f"Session {self.session_id} initialization marked as complete")

                    # Notify the session manager that initialization is complete
                    if self.on_initialized and self.session_id:
                        await self.on_initialized(self.session_id)

                # Process the message after sending the response
                metadata = ServerMessageMetadata(request_context=request)
                session_message = SessionMessage(message, metadata=metadata)
                await writer.send(session_message)
                return

            # Handle request
            request_id = str(message.root.id)

            # Determine response type based on tool call
            response_type: ResponseType = "sse"  # Default to SSE
            if isinstance(message.root, JSONRPCRequest) and message.root.method == "tools/call":
                # Extract tool name and check if it's a webhook tool
                metadata = ServerMessageMetadata(request_context=request)
                session_message_for_parsing = SessionMessage(message, metadata=metadata)
                tool_name = extract_tool_name_from_request(session_message_for_parsing)
                if tool_name and tool_name in self.webhook_tools:
                    response_type = "webhook"

            # Register this stream for the request ID with response type
            stream_tuple = anyio.create_memory_object_stream[EventMessage](0)
            self._request_streams[request_id] = (stream_tuple[0], stream_tuple[1], response_type)
            request_stream_reader = self._request_streams[request_id][1]

            if self.is_json_response_enabled:
                # JSON response mode
                await self._handle_json_response_mode(
                    message, request, writer, request_stream_reader, request_id, scope, receive, send
                )
            else:
                # SSE response mode
                await self._handle_sse_response_mode(
                    message, request, writer, request_stream_reader, request_id, scope, receive, send
                )

        except Exception as err:
            logger.exception("Error handling POST request")
            response = create_error_response(
                f"Error handling POST request: {err}",
                HTTPStatus.INTERNAL_SERVER_ERROR,
                INTERNAL_ERROR,
                session_id=self.session_id,
            )
            await response(scope, receive, send)
            if writer:
                await writer.send(Exception(err))

    async def _handle_json_response_mode(
        self,
        message: JSONRPCMessage,
        request: Request,
        writer: MemoryObjectSendStream,
        request_stream_reader: MemoryObjectReceiveStream[EventMessage],
        request_id: str,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        """Handle JSON response mode."""
        try:
            # Process the message
            metadata = ServerMessageMetadata(request_context=request)
            session_message = SessionMessage(message, metadata=metadata)
            await writer.send(session_message)

            # Wait for response
            response_message = None

            async for event_message in request_stream_reader:
                if isinstance(event_message.message.root, (JSONRPCResponse, JSONRPCError)):
                    response_message = event_message.message
                    break

            if response_message:
                response = create_json_response(
                    response_message,
                    session_id=self.session_id,
                )
                await response(scope, receive, send)
            else:
                logger.error("No response message received before stream closed")
                response = create_error_response(
                    "Error processing request: No response received",
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    session_id=self.session_id,
                )
                await response(scope, receive, send)

        except Exception:
            logger.exception("Error processing JSON response")
            response = create_error_response(
                "Error processing request",
                HTTPStatus.INTERNAL_SERVER_ERROR,
                INTERNAL_ERROR,
                session_id=self.session_id,
            )
            await response(scope, receive, send)
        finally:
            await self._clean_up_memory_streams(request_id)

    async def _handle_sse_response_mode(
        self,
        message: JSONRPCMessage,
        request: Request,
        writer: MemoryObjectSendStream,
        request_stream_reader: MemoryObjectReceiveStream[EventMessage],
        request_id: str,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        """Handle SSE response mode."""
        # Create SSE stream
        sse_stream_writer, sse_stream_reader = anyio.create_memory_object_stream[Dict[str, str]](0)

        async def sse_writer():
            try:
                async with sse_stream_writer, request_stream_reader:
                    async for event_message in request_stream_reader:
                        # Build the event data
                        event_data = create_event_data(event_message)
                        await sse_stream_writer.send(event_data)

                        # If response, close stream
                        if isinstance(event_message.message.root, (JSONRPCResponse, JSONRPCError)):
                            break
            except Exception:
                logger.exception("Error in SSE writer")
            finally:
                logger.debug("Closing SSE writer")
                await self._clean_up_memory_streams(request_id)

        # Set up headers
        headers = {
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "Content-Type": CONTENT_TYPE_SSE,
        }
        if self.session_id:
            headers[MCP_SESSION_ID_HEADER] = self.session_id

        response = EventSourceResponse(
            content=sse_stream_reader,
            data_sender_callable=sse_writer,
            headers=headers,
        )

        try:
            async with anyio.create_task_group() as tg:
                tg.start_soon(response, scope, receive, send)

                # Send the message to be processed
                metadata = ServerMessageMetadata(request_context=request)
                session_message = SessionMessage(message, metadata=metadata)
                await writer.send(session_message)
        except Exception:
            logger.exception("SSE response error")
            await sse_stream_writer.aclose()
            await sse_stream_reader.aclose()
            await self._clean_up_memory_streams(request_id)

    async def _handle_get_request(self, request: Request, scope: Scope, receive: Receive, send: Send) -> None:
        """Handle GET request to establish SSE stream."""
        writer = self._read_stream_writer
        if writer is None:
            raise ValueError("No read stream writer available. Ensure connect() is called first.")

        # Validate Accept header
        _, has_sse = self._check_accept_headers(request)
        if not has_sse:
            response = create_error_response(
                "Not Acceptable: Client must accept text/event-stream",
                HTTPStatus.NOT_ACCEPTABLE,
                session_id=self.session_id,
            )
            await response(scope, receive, send)
            return

        if not await self._validate_request_headers(request, send):
            return

        # Check if we already have an active GET stream
        if GET_STREAM_KEY in self._request_streams:
            response = create_error_response(
                "Conflict: Only one SSE stream is allowed per session",
                HTTPStatus.CONFLICT,
                session_id=self.session_id,
            )
            await response(scope, receive, send)
            return

        headers = {
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "Content-Type": CONTENT_TYPE_SSE,
        }
        if self.session_id:
            headers[MCP_SESSION_ID_HEADER] = self.session_id

        # Create SSE stream
        sse_stream_writer, sse_stream_reader = anyio.create_memory_object_stream[Dict[str, str]](0)

        async def standalone_sse_writer():
            try:
                stream_tuple = anyio.create_memory_object_stream[EventMessage](0)
                self._request_streams[GET_STREAM_KEY] = (stream_tuple[0], stream_tuple[1], "sse")
                standalone_stream_reader = self._request_streams[GET_STREAM_KEY][1]

                async with sse_stream_writer, standalone_stream_reader:
                    async for event_message in standalone_stream_reader:
                        event_data = create_event_data(event_message)
                        await sse_stream_writer.send(event_data)
            except Exception:
                logger.exception("Error in standalone SSE writer")
            finally:
                logger.debug("Closing standalone SSE writer")
                await self._clean_up_memory_streams(GET_STREAM_KEY)

        response = EventSourceResponse(
            content=sse_stream_reader,
            data_sender_callable=standalone_sse_writer,
            headers=headers,
        )

        try:
            await response(scope, receive, send)
        except Exception:
            logger.exception("Error in standalone SSE response")
            await sse_stream_writer.aclose()
            await sse_stream_reader.aclose()
            await self._clean_up_memory_streams(GET_STREAM_KEY)

    async def _handle_delete_request(self, request: Request, scope: Scope, receive: Receive, send: Send) -> None:
        """Handle DELETE requests for explicit session termination."""
        if not self.session_id:
            response = create_error_response(
                "Method Not Allowed: Session termination not supported",
                HTTPStatus.METHOD_NOT_ALLOWED,
                session_id=self.session_id,
            )
            await response(scope, receive, send)
            return

        if not await self._validate_request_headers(request, send):
            return

        await self.terminate()

        response = create_json_response(
            response_message=None,
            session_id=self.session_id,
            status_code=HTTPStatus.OK,
        )
        await response(scope, receive, send)

    async def _handle_unsupported_method(self, request: Request, scope: Scope, receive: Receive, send: Send) -> None:
        """Handle unsupported HTTP methods."""
        headers = {
            "Content-Type": CONTENT_TYPE_JSON,
            "Allow": "GET, POST, DELETE",
        }
        if self.session_id:
            headers[MCP_SESSION_ID_HEADER] = self.session_id

        response = create_error_response(
            "Method Not Allowed",
            HTTPStatus.METHOD_NOT_ALLOWED,
            headers=headers,
            session_id=self.session_id,
        )
        await response(scope, receive, send)

    async def _validate_request_headers(self, request: Request, send: Send) -> bool:
        """Validate request headers (session ID and protocol version)."""
        if not await self._validate_session(request, send):
            return False
        if not await self._validate_protocol_version(request, send):
            return False
        return True

    async def _validate_session(self, request: Request, send: Send) -> bool:
        """Validate the session ID in the request."""
        if not self.session_id:
            return True

        request_session_id = request.headers.get(MCP_SESSION_ID_HEADER)

        if not request_session_id:
            response = create_error_response(
                "Bad Request: Missing session ID",
                HTTPStatus.BAD_REQUEST,
                session_id=self.session_id,
            )
            await response(request.scope, request.receive, send)
            return False

        if request_session_id != self.session_id:
            response = create_error_response(
                "Not Found: Invalid or expired session ID",
                HTTPStatus.NOT_FOUND,
                session_id=self.session_id,
            )
            await response(request.scope, request.receive, send)
            return False

        return True

    async def _validate_protocol_version(self, request: Request, send: Send) -> bool:
        """Validate the protocol version header in the request."""
        protocol_version = request.headers.get(MCP_PROTOCOL_VERSION_HEADER)

        if protocol_version is None:
            protocol_version = DEFAULT_NEGOTIATED_VERSION

        if protocol_version not in SUPPORTED_PROTOCOL_VERSIONS:
            supported_versions = ", ".join(SUPPORTED_PROTOCOL_VERSIONS)
            response = create_error_response(
                f"Bad Request: Unsupported protocol version: {protocol_version}. "
                + f"Supported versions: {supported_versions}",
                HTTPStatus.BAD_REQUEST,
                session_id=self.session_id,
            )
            await response(request.scope, request.receive, send)
            return False

        return True

    async def _clean_up_memory_streams(self, request_id: RequestId) -> None:
        """Clean up memory streams for a given request ID."""
        if request_id in self._request_streams:
            try:
                await self._request_streams[request_id][0].aclose()
                await self._request_streams[request_id][1].aclose()
            except Exception:
                logger.debug("Error closing memory streams - may already be closed")
            finally:
                self._request_streams.pop(request_id, None)

    async def _handle_initialize_message(self, message: JSONRPCMessage) -> None:
        """Extract webhook information from initialize request."""
        from .utils import extract_webhook_url_from_meta

        if isinstance(message.root, JSONRPCRequest):
            # Extract webhook URL from _meta field
            webhook_url = extract_webhook_url_from_meta(message)

            if webhook_url:
                self.set_webhook_url(webhook_url)
                logger.debug(f"Extracted webhook URL from initialize: {webhook_url}")

    async def send_to_client_webhook(self, session_message: SessionMessage) -> None:
        """Send a message to the client's webhook URL."""
        if self._terminated:
            logger.debug(f"Session {self.session_id} is terminated, skipping webhook send")
            return

        if not self.webhook_url:
            logger.warning(f"No webhook URL set for session {self.session_id}")
            return

        try:
            await send_webhook_response(
                self.http_client,
                self.webhook_url,
                session_message,
                self.session_id,
                None,  # client_id not needed for this context
                max_retries=self.config.webhook_max_retries,
            )
            logger.debug(f"Successfully sent response to webhook {self.webhook_url}")

        except httpx.ConnectError as e:
            logger.error(f"Failed to connect to webhook {self.webhook_url}: {e}")
            raise
        except httpx.HTTPError as e:
            logger.warning(f"HTTP error sending message to webhook {self.webhook_url}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error sending message to webhook {self.webhook_url}: {e}")

    async def send_sse_response(self, session_message: SessionMessage) -> None:
        """Send a message via SSE streams."""
        message_root = session_message.message.root
        target_request_id = None

        # Determine target stream
        if isinstance(message_root, (JSONRPCResponse, JSONRPCError)):
            target_request_id = str(message_root.id)
        elif hasattr(session_message, "metadata") and session_message.metadata:
            if hasattr(session_message.metadata, "related_request_id"):
                target_request_id = str(session_message.metadata.related_request_id)

        request_stream_id = target_request_id if target_request_id else GET_STREAM_KEY

        if request_stream_id in self._request_streams:
            try:
                event_message = EventMessage(message=session_message.message, event_id=None)
                await self._request_streams[request_stream_id][0].send(event_message)
            except (anyio.BrokenResourceError, anyio.ClosedResourceError):
                # Stream might be closed, remove from registry
                self._request_streams.pop(request_stream_id, None)
        else:
            logger.debug(f"Request stream {request_stream_id} not found for message")

    async def _message_forwarder(self) -> None:
        """Message router that distributes messages to request streams and handles webhook routing."""
        if not self._write_stream_reader:
            return

        try:
            async with self._write_stream_reader:
                async for session_message in self._write_stream_reader:
                    if self._terminated:
                        break

                    # Determine which request stream(s) should receive this message
                    message = session_message.message
                    target_request_id = None

                    # For responses, route to specific request stream
                    if isinstance(message.root, (JSONRPCResponse, JSONRPCError)):
                        response_id = str(message.root.id)
                        if response_id in self._request_streams:
                            target_request_id = response_id
                    else:
                        # For requests/notifications, check for related_request_id
                        if (
                            session_message.metadata is not None
                            and hasattr(session_message.metadata, "related_request_id")
                            and session_message.metadata.related_request_id is not None
                        ):
                            target_request_id = str(session_message.metadata.related_request_id)

                    request_stream_id = target_request_id if target_request_id is not None else GET_STREAM_KEY
                    logger.debug(f"Message forwarded to request stream: {request_stream_id}")

                    # Route message based on tool type (webhook vs SSE)
                    await self.tool_router.route_message(
                        session_message, sse_handler=self.send_sse_response, webhook_handler=self.send_to_client_webhook
                    )

                    # Send to central message queue if available
                    if self._outgoing_message_sender:
                        event = OutgoingMessageEvent(session_id=self.session_id, message=session_message)
                        try:
                            self._outgoing_message_sender.send_nowait(event)
                        except anyio.WouldBlock:
                            logger.warning(f"Central message queue full for session {self.session_id}")
                        except anyio.BrokenResourceError:
                            break
        except anyio.EndOfStream:
            pass
        except Exception as e:
            logger.warning(f"Error in message forwarder for session {self.session_id}: {e}")

    async def cleanup(self) -> None:
        """Clean up streams and resources."""
        # Clean up request streams
        request_stream_keys = list(self._request_streams.keys())
        for key in request_stream_keys:
            await self._clean_up_memory_streams(key)
        self._request_streams.clear()

        # Call parent cleanup
        await super().cleanup()
