"""Proxy session implementation.

This module contains the ProxySession class that represents a single
proxy session maintaining a connection to the backend transport.
"""

import asyncio
import logging
from typing import Any, Dict, Optional, Union

import anyio
from anyio.abc import TaskGroup
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp.shared.message import SessionMessage
from mcp.types import ErrorData, JSONRPCMessage

from .client import ProxyClient

logger = logging.getLogger(__name__)


class ProxySession:
    """Represents a single proxy session.

    Each session maintains a connection to the backend transport and
    manages message routing between the client and backend.
    """

    def __init__(
        self,
        session_id: str,
        backend_client: ProxyClient,
        response_sender: MemoryObjectSendStream[SessionMessage],
    ):
        """Initialize a proxy session.

        Args:
            session_id: Unique session identifier
            backend_client: Client for connecting to backend transport
            response_sender: Stream for sending responses to the client
        """
        self.session_id = session_id
        self.backend_client = backend_client
        self.response_sender = response_sender

        self._task_group: Optional[TaskGroup] = None
        self._read_stream: Optional[MemoryObjectReceiveStream[Union[SessionMessage, Exception]]] = None
        self._write_stream: Optional[MemoryObjectSendStream[SessionMessage]] = None
        self._running = False
        self._connected_event = anyio.Event()

    async def start(self):
        """Start the proxy session."""
        if self._running:
            return

        self._running = True

        async with anyio.create_task_group() as tg:
            self._task_group = tg

            try:
                async with self.backend_client.connect() as (read_stream, write_stream):
                    self._read_stream = read_stream
                    self._write_stream = write_stream

                    # Signal that we're connected
                    self._connected_event.set()

                    tg.start_soon(self._forward_responses)

                    # Keep session alive until cancelled
                    try:
                        await anyio.Event().wait()
                    except asyncio.CancelledError:
                        logger.info(f"Proxy session {self.session_id} cancelled")
                        raise
            except Exception as e:
                logger.error(f"Failed to connect to backend for session {self.session_id}: {type(e).__name__}: {e}")
                raise

    async def stop(self):
        """Stop the proxy session."""
        if not self._running:
            return

        self._running = False

        if self._task_group:
            self._task_group.cancel_scope.cancel()

    async def send_message(self, message_data: Dict[str, Any]):
        """Send a message to the backend.

        Args:
            message_data: JSON-RPC message data to send
        """
        logger.info(
            f"Session {self.session_id} sending message: "
            f"method={message_data.get('method', 'unknown')}, "
            f"id={message_data.get('id', 'none')}"
        )

        # Wait for connection to be established
        await self._connected_event.wait()

        if not self._write_stream:
            logger.error(f"Write stream is None for session {self.session_id}")
            raise RuntimeError("Session not connected")

        try:
            # Parse and validate message
            jsonrpc_message = JSONRPCMessage.model_validate(message_data)
            session_message = SessionMessage(jsonrpc_message)

            await self._write_stream.send(session_message)

        except Exception as e:
            logger.error(f"Session {self.session_id}: Error sending message: {e}")
            raise

    async def _forward_responses(self):
        """Forward responses from backend to client."""
        if not self._read_stream:
            return

        try:
            async for message in self._read_stream:
                if isinstance(message, Exception):
                    # Handle backend errors
                    logger.error(f"Backend error for session {self.session_id}: {message}")
                    error_response = self._create_error_response(str(message))
                    await self.response_sender.send(error_response)
                else:
                    # Forward response to client
                    await self.response_sender.send(message)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Session {self.session_id}: Error forwarding responses: {e}")

    def _create_error_response(self, error_message: str) -> SessionMessage:
        """Create an error response message."""
        error_data = ErrorData(
            code=-32603,  # Internal error
            message=error_message,
        )
        jsonrpc_message = JSONRPCMessage.model_validate(
            {
                "jsonrpc": "2.0",
                "error": error_data.model_dump(),
            }
        )
        return SessionMessage(jsonrpc_message)
