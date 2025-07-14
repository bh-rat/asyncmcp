from dataclasses import dataclass
from typing import Any, Dict, Optional

import anyio
import anyio.lowlevel
from anyio.streams.memory import MemoryObjectSendStream
from pydantic_core import ValidationError

from asyncmcp.sns_sqs.utils import _to_session_message, _delete_sqs_message
from mcp.shared.message import SessionMessage


@dataclass
class SqsTransportConfig:
    """Configuration for SQS only transport."""

    read_queue_url: str
    write_queue_url: str
    max_messages: int = 10
    wait_time_seconds: int = 20
    visibility_timeout_seconds: int = 30
    message_attributes: Optional[Dict[str, Any]] = None
    poll_interval_seconds: float = 5.0
    client_id: Optional[str] = None
    transport_timeout_seconds: Optional[float] = None


async def process_sqs_message(
    messages: list[Dict[str, Any]],
    sqs_client: Any,
    queue_url: str,
    read_stream_writer: MemoryObjectSendStream[SessionMessage | Exception],
) -> None:
    """Process a batch of SQS messages."""

    async def process_single_message(sqs_message: Dict[str, Any]) -> None:
        try:
            session_message = await _to_session_message(sqs_message)
            await read_stream_writer.send(session_message)
            await _delete_sqs_message(sqs_client, queue_url, sqs_message["ReceiptHandle"])
        except (ValidationError, Exception) as exc:  # noqa: PERF203
            await read_stream_writer.send(exc)
            await _delete_sqs_message(sqs_client, queue_url, sqs_message["ReceiptHandle"])

    await anyio.lowlevel.checkpoint()

    async with anyio.create_task_group() as tg:
        for msg in messages:
            tg.start_soon(process_single_message, msg)
