import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

import anyio
import anyio.lowlevel
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from collections.abc import AsyncGenerator
import json

import mcp.types as types
from mcp.shared.message import SessionMessage

from asyncmcp.sqs.utils import SqsTransportConfig
from asyncmcp.common.client_state import ClientState
from asyncmcp.common.aws_queue_utils import create_common_client_message_attributes, sqs_reader as common_sqs_reader

logger = logging.getLogger(__name__)


async def _create_sqs_message_attributes(
    session_message: SessionMessage, config: SqsTransportConfig, client_id: str, session_id: Optional[str]
) -> Dict[str, Any]:
    """Creates SQS message attributes."""
    attrs = create_common_client_message_attributes(
        session_message=session_message,
        client_id=client_id,
        session_id=session_id,
    )

    if config.message_attributes:
        for key, value in config.message_attributes.items():
            attrs[key] = {"DataType": "String", "StringValue": str(value)}

    return attrs


@asynccontextmanager
async def sqs_client(
    config: SqsTransportConfig, sqs_client: Any, response_queue_url: str
) -> AsyncGenerator[
    tuple[MemoryObjectReceiveStream[SessionMessage | Exception], MemoryObjectSendStream[SessionMessage]],
    None,
]:
    state = ClientState(client_id=config.client_id or f"mcp-client-{uuid.uuid4().hex[:8]}", session_id=None)

    read_stream_writer: MemoryObjectSendStream[SessionMessage | Exception]
    read_stream: MemoryObjectReceiveStream[SessionMessage | Exception]
    read_stream_writer, read_stream = anyio.create_memory_object_stream(0)

    write_stream_reader: MemoryObjectReceiveStream[SessionMessage]
    write_stream: MemoryObjectSendStream[SessionMessage]
    write_stream, write_stream_reader = anyio.create_memory_object_stream(0)

    async def sqs_reader():
        await common_sqs_reader(read_stream_writer, sqs_client, config, response_queue_url, state)

    async def sqs_writer() -> None:
        async with write_stream_reader:
            async for session_message in write_stream_reader:
                await anyio.lowlevel.checkpoint()
                try:
                    json_message = session_message.message.model_dump_json(by_alias=True, exclude_none=True)
                    message_attributes = await _create_sqs_message_attributes(
                        session_message, config, state.client_id, state.session_id
                    )

                    # For initialize requests, add response_queue_url to the params
                    if (
                        isinstance(session_message.message.root, types.JSONRPCRequest)
                        and session_message.message.root.method == "initialize"
                    ):
                        message_dict = session_message.message.model_dump(by_alias=True, exclude_none=True)
                        if "params" not in message_dict:
                            message_dict["params"] = {}
                        message_dict["params"]["response_queue_url"] = response_queue_url
                        json_message = json.dumps(message_dict)

                    await anyio.to_thread.run_sync(
                        lambda: sqs_client.send_message(
                            QueueUrl=config.read_queue_url,
                            MessageBody=json_message,
                            MessageAttributes=message_attributes,
                        )
                    )
                except Exception as e:
                    logger.warning(f"Error sending message to SQS: {e}")
                    # Continue processing other messages even if one fails

    if config.transport_timeout_seconds is None:
        async with anyio.create_task_group() as tg:
            tg.start_soon(sqs_reader)
            tg.start_soon(sqs_writer)
            try:
                yield read_stream, write_stream
            finally:
                tg.cancel_scope.cancel()
    else:
        with anyio.move_on_after(config.transport_timeout_seconds):
            async with anyio.create_task_group() as tg:
                tg.start_soon(sqs_reader)
                tg.start_soon(sqs_writer)
                yield read_stream, write_stream
