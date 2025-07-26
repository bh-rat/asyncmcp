import logging
from contextlib import AbstractAsyncContextManager
from typing import Optional, Protocol, Union

from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp.shared.message import SessionMessage

logger = logging.getLogger(__name__)


class ServerTransportProtocol(Protocol):
    """Protocol defining the interface for server-side transport implementations."""

    _read_stream_writer: Optional[MemoryObjectSendStream]
    _read_stream: Optional[MemoryObjectReceiveStream]
    _write_stream_reader: Optional[MemoryObjectReceiveStream]
    _write_stream: Optional[MemoryObjectSendStream]

    def connect(
        self,
    ) -> AbstractAsyncContextManager[
        tuple[
            MemoryObjectReceiveStream[Union[SessionMessage, Exception]],
            MemoryObjectSendStream[SessionMessage],
        ]
    ]:
        """
        Connect and provide bidirectional streams.

        The actual SQS polling will be handled by the session manager.
        This just sets up the internal streams for this session.
        """
        pass

    async def terminate(self) -> None:
        """Terminate this transport session."""
        pass

    async def cleanup(self) -> None:
        """Clean up streams and resources."""
        pass
