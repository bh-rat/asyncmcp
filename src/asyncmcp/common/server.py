import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Optional, Union

import anyio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp.shared.message import SessionMessage

from asyncmcp.common.outgoing_event import OutgoingMessageEvent
from asyncmcp.common.protocols import ServerTransportProtocol

logger = logging.getLogger(__name__)


class ServerTransport(ServerTransportProtocol):
    """Base class for defining the server-side transport."""

    def __init__(
        self,
        config: Any,
        session_id: Optional[str],
        outgoing_message_sender: Optional[MemoryObjectSendStream[OutgoingMessageEvent]] = None,
    ):
        self.config = config
        self._outgoing_message_sender = outgoing_message_sender
        self.session_id = session_id

        self._terminated = False

        self._read_stream_writer: Optional[MemoryObjectSendStream[Union[SessionMessage, Exception]]] = None
        self._read_stream: Optional[MemoryObjectReceiveStream[Union[SessionMessage, Exception]]] = None
        self._write_stream_reader: Optional[MemoryObjectReceiveStream[SessionMessage]] = None
        self._write_stream: Optional[MemoryObjectSendStream[SessionMessage]] = None

    @property
    def is_terminated(self) -> bool:
        """Returns whether the transport has been terminated."""
        return self._terminated

    @asynccontextmanager
    async def connect(
        self,
    ) -> AsyncGenerator[
        tuple[
            MemoryObjectReceiveStream[Union[SessionMessage, Exception]],
            MemoryObjectSendStream[SessionMessage],
        ],
        None,
    ]:
        """
        Connect and provide bidirectional streams.

        The actual SQS polling will be handled by the session manager.
        This just sets up the internal streams for this session.
        """
        if self._terminated:
            raise RuntimeError("Cannot connect to terminated transport")

        # Create memory streams for this session
        read_stream_writer, read_stream = anyio.create_memory_object_stream[Union[SessionMessage, Exception]](0)
        write_stream, write_stream_reader = anyio.create_memory_object_stream[SessionMessage](0)

        # Store streams
        self._read_stream_writer = read_stream_writer
        self._read_stream = read_stream
        self._write_stream_reader = write_stream_reader
        self._write_stream = write_stream

        try:
            if self._outgoing_message_sender:
                async with anyio.create_task_group() as tg:
                    tg.start_soon(self._message_forwarder)
                    try:
                        yield read_stream, write_stream
                    finally:
                        tg.cancel_scope.cancel()
            else:
                yield read_stream, write_stream
        finally:
            await self.cleanup()

    async def terminate(self) -> None:
        """Terminate this transport session."""
        if self._terminated:
            return
        self._terminated = True
        await self.cleanup()

    async def send_message(self, session_message: SessionMessage) -> None:
        """Send a message to this session's read stream."""
        if self._terminated or not self._read_stream_writer:
            return

        try:
            await self._read_stream_writer.send(session_message)
        except Exception as e:
            logger.warning(f"Error sending message to session {self.session_id}: {e}")

    async def cleanup(self) -> None:
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

    async def _message_forwarder(self) -> None:
        if not self._write_stream_reader or not self._outgoing_message_sender:
            return

        try:
            async with self._write_stream_reader:
                async for message in self._write_stream_reader:
                    if self._terminated:
                        break

                    event = OutgoingMessageEvent(session_id=self.session_id, message=message)

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
