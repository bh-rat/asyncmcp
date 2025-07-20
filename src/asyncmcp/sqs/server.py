# src/asyncmcp/sqs/transport.py
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, Optional

import anyio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream

from mcp.shared.message import SessionMessage
from .utils import SqsTransportConfig

logger = logging.getLogger(__name__)


class SqsTransport:
    """
    SQS transport for handling a single MCP session.
    """

    def __init__(
        self,
        config: SqsTransportConfig,
        sqs_client: Any,
        session_id: str | None = None,
        response_queue_url: str | None = None,
    ):
        self.config = config
        self.sqs_client = sqs_client
        self.session_id = session_id
        self.response_queue_url = response_queue_url

        self._terminated = False

        self._read_stream_writer: Optional[MemoryObjectSendStream] = None
        self._read_stream: Optional[MemoryObjectReceiveStream] = None
        self._write_stream_reader: Optional[MemoryObjectReceiveStream] = None
        self._write_stream: Optional[MemoryObjectSendStream] = None

    def set_response_queue_url(self, response_queue_url: str) -> None:
        """Set the client-specific response queue URL."""
        self.response_queue_url = response_queue_url

    @property
    def is_terminated(self) -> bool:
        return self._terminated

    @asynccontextmanager
    async def connect(self):
        """
        Connect and provide bidirectional streams.

        The actual SQS polling will be handled by the session manager.
        This just sets up the internal streams for this session.
        """
        if self._terminated:
            raise RuntimeError("Cannot connect to terminated transport")

        # Create memory streams for this session
        read_stream_writer, read_stream = anyio.create_memory_object_stream[SessionMessage | Exception](0)
        write_stream, write_stream_reader = anyio.create_memory_object_stream[SessionMessage](0)

        # Store streams
        self._read_stream_writer = read_stream_writer
        self._read_stream = read_stream
        self._write_stream_reader = write_stream_reader
        self._write_stream = write_stream

        try:
            yield read_stream, write_stream
        finally:
            await self._cleanup()

    async def terminate(self) -> None:
        """Terminate this transport session."""
        if self._terminated:
            return

        logger.info(f"Terminating SQS transport session: {self.session_id}")
        self._terminated = True
        await self._cleanup()

    async def _cleanup(self) -> None:
        """Clean up streams and resources."""
        try:
            if self._read_stream_writer:
                await self._read_stream_writer.aclose()
            if self._read_stream:
                await self._read_stream.aclose()
            if self._write_stream_reader:
                await self._write_stream_reader.aclose()
            if self._write_stream:
                await self._write_stream.aclose()
        except Exception as e:
            logger.debug(f"Error during cleanup: {e}")
        finally:
            # Set stream references to None after cleanup
            self._read_stream_writer = None
            self._read_stream = None
            self._write_stream_reader = None
            self._write_stream = None

    async def send_message(self, session_message: SessionMessage) -> None:
        """Send a message to this session's read stream."""
        if self._terminated or not self._read_stream_writer:
            return

        try:
            await self._read_stream_writer.send(session_message)
        except Exception as e:
            logger.warning(f"Error sending message to session {self.session_id}: {e}")

    async def get_outgoing_message(self) -> SessionMessage | None:
        """Get the next outgoing message from this session."""
        if self._terminated or not self._write_stream_reader:
            return None

        try:
            # TODO: Remove wait and move to event driven approach
            with anyio.move_on_after(0.1):
                message = await self._write_stream_reader.receive()
                return message
        except anyio.EndOfStream:
            return None
        except Exception as e:
            logger.warning(f"Error receiving message from session {self.session_id}: {e}")

        return None

    async def _create_sqs_message_attributes(self, session_message: SessionMessage) -> dict:
        """Create SQS message attributes for outgoing messages."""
        attrs = {
            "MessageType": {"DataType": "String", "StringValue": "jsonrpc"},
            "SessionId": {"DataType": "String", "StringValue": self.session_id or ""},
            "Timestamp": {"DataType": "Number", "StringValue": str(int(time.time()))},
        }

        message_root = session_message.message.root
        if hasattr(message_root, "method"):
            attrs["Method"] = {"DataType": "String", "StringValue": message_root.method}
        if hasattr(message_root, "id") and hasattr(message_root, "method"):
            attrs["RequestId"] = {"DataType": "String", "StringValue": str(message_root.id)}

        if self.config.message_attributes:
            attrs.update(self.config.message_attributes)

        return attrs

    async def send_to_client_queue(self, session_message: SessionMessage) -> None:
        """Send a message to the client's response queue."""
        if not self.response_queue_url:
            logger.warning(f"No response queue URL set for session {self.session_id}")
            return

        try:
            json_message = session_message.message.model_dump_json(by_alias=True, exclude_none=True)

            message_attributes = await self._create_sqs_message_attributes(session_message)

            await anyio.to_thread.run_sync(
                lambda: self.sqs_client.send_message(
                    QueueUrl=self.response_queue_url,
                    MessageBody=json_message,
                    MessageAttributes=message_attributes,
                )
            )

            logger.info(f"Successfully sent response to client queue")

        except Exception as e:
            logger.error(f"Error sending message to client queue {self.response_queue_url}: {e}")
            raise


@asynccontextmanager
async def sqs_server(config: SqsTransportConfig, sqs_client: Any, response_queue_url: str):
    """Backward compatible SQS server transport - now requires response_queue_url parameter."""
    transport = SqsTransport(config, sqs_client, response_queue_url=response_queue_url)

    async with transport.connect() as (read_stream, write_stream):
        yield read_stream, write_stream
