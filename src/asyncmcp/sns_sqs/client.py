import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict

import anyio
import anyio.lowlevel
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from collections.abc import AsyncGenerator

import mcp.types as types
from mcp.shared.message import SessionMessage

from asyncmcp import SnsSqsTransportConfig
from asyncmcp.sns_sqs.utils import process_sqs_message

logger = logging.getLogger(__name__)


async def _create_sns_message_attributes(
    session_message: SessionMessage,
    client_id: str,
    config: SnsSqsTransportConfig,
) -> Dict[str, Any]:
    """Create SNS message attributes."""
    base_attrs = {
        "MessageType": {"DataType": "String", "StringValue": "jsonrpc"},
        "ClientId": {"DataType": "String", "StringValue": client_id},
        "Timestamp": {"DataType": "Number", "StringValue": str(int(time.time()))},
    }

    message_root = session_message.message.root
    if isinstance(message_root, types.JSONRPCRequest):
        base_attrs.update(
            {
                "RequestId": {"DataType": "String", "StringValue": str(message_root.id)},
                "Method": {"DataType": "String", "StringValue": message_root.method},
            }
        )
    elif isinstance(message_root, types.JSONRPCNotification):
        base_attrs["Method"] = {"DataType": "String", "StringValue": message_root.method}

    if config.message_attributes:
        base_attrs.update(config.message_attributes)

    return base_attrs


@asynccontextmanager
async def sns_sqs_client(
    config: SnsSqsTransportConfig,
    sqs_client: Any,
    sns_client: Any,
) -> AsyncGenerator[
    tuple[MemoryObjectReceiveStream[SessionMessage | Exception], MemoryObjectSendStream[SessionMessage]], None
]:
    read_stream_writer: MemoryObjectSendStream[SessionMessage | Exception]
    read_stream: MemoryObjectReceiveStream[SessionMessage | Exception]
    read_stream_writer, read_stream = anyio.create_memory_object_stream(0)

    write_stream: MemoryObjectSendStream[SessionMessage]
    write_stream_reader: MemoryObjectReceiveStream[SessionMessage]
    write_stream, write_stream_reader = anyio.create_memory_object_stream(0)
    client_id = config.client_id or f"mcp-client-{uuid.uuid4().hex[:8]}"

    async def sqs_reader():
        async with read_stream_writer:
            while True:
                await anyio.lowlevel.checkpoint()

                response = await anyio.to_thread.run_sync(
                    lambda: sqs_client.receive_message(
                        QueueUrl=config.sqs_queue_url,
                        MaxNumberOfMessages=config.max_messages,
                        WaitTimeSeconds=config.wait_time_seconds,
                        VisibilityTimeout=config.visibility_timeout_seconds,
                        MessageAttributeNames=["All"],
                    )
                )
                messages = response.get("Messages", [])
                if messages:
                    await process_sqs_message(messages, sqs_client, config, read_stream_writer)
                else:
                    await anyio.sleep(config.poll_interval_seconds)

    async def sns_writer():
        async with write_stream_reader:
            async for session_message in write_stream_reader:
                await anyio.lowlevel.checkpoint()

                try:
                    json_message = session_message.message.model_dump_json(by_alias=True, exclude_none=True)
                    message_attributes = await _create_sns_message_attributes(session_message, client_id, config)

                    await anyio.to_thread.run_sync(
                        lambda: sns_client.publish(
                            TopicArn=config.sns_topic_arn, Message=json_message, MessageAttributes=message_attributes
                        )
                    )
                except Exception:
                    # Continue processing other messages
                    continue

    if config.transport_timeout_seconds is None:
        async with anyio.create_task_group() as tg:
            tg.start_soon(sqs_reader)
            tg.start_soon(sns_writer)
            try:
                yield read_stream, write_stream
            finally:
                await read_stream_writer.aclose()
                await write_stream.aclose()
    else:
        with anyio.move_on_after(config.transport_timeout_seconds):
            async with anyio.create_task_group() as tg:
                tg.start_soon(sqs_reader)
                tg.start_soon(sns_writer)
                try:
                    yield read_stream, write_stream
                finally:
                    await read_stream_writer.aclose()
                    await write_stream.aclose()
