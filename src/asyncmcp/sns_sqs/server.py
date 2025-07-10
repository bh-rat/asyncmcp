import logging
from contextlib import asynccontextmanager
from typing import Any, Dict

import anyio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream

from mcp.shared.message import SessionMessage

from asyncmcp.sns_sqs.utils import SnsSqsTransportConfig, process_sqs_message

logger = logging.getLogger(__name__)


async def _create_sns_message_attributes(
    session_message: SessionMessage, config: SnsSqsTransportConfig
) -> Dict[str, Any]:
    """Create SNS message attributes."""
    base_attrs = {"MessageType": {"DataType": "String", "StringValue": "jsonrpc"}}

    if config.message_attributes:
        base_attrs.update(config.message_attributes)

    if hasattr(session_message, "metadata") and session_message.metadata:
        if isinstance(session_message.metadata, dict) and "related_request_id" in session_message.metadata:
            base_attrs["OriginalSQSMessageId"] = {
                "DataType": "String",
                "StringValue": session_message.metadata["related_request_id"],
            }

    return base_attrs


@asynccontextmanager
async def sns_sqs_server(config: SnsSqsTransportConfig, sqs_client: Any, sns_client: Any):
    read_stream_writer: MemoryObjectSendStream[SessionMessage | Exception]
    read_stream: MemoryObjectReceiveStream[SessionMessage | Exception]
    read_stream_writer, read_stream = anyio.create_memory_object_stream(0)

    write_stream: MemoryObjectSendStream[SessionMessage]
    write_stream_reader: MemoryObjectReceiveStream[SessionMessage]
    write_stream, write_stream_reader = anyio.create_memory_object_stream(0)

    async def sqs_reader():
        async with read_stream_writer:
            while True:
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
                try:
                    json_message = session_message.message.model_dump_json(by_alias=True, exclude_none=True)
                    message_attributes = await _create_sns_message_attributes(session_message, config)

                    await anyio.to_thread.run_sync(
                        lambda: sns_client.publish(
                            TopicArn=config.sns_topic_arn, Message=json_message, MessageAttributes=message_attributes
                        )
                    )
                except Exception:
                    continue

    async with anyio.create_task_group() as tg:
        tg.start_soon(sqs_reader)
        tg.start_soon(sns_writer)
        yield read_stream, write_stream
