# src/asyncmcp/sns_sqs/manager.py
import json
import logging
import threading
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional
from uuid import uuid4

import anyio
import anyio.lowlevel
import anyio.to_thread
from anyio.abc import TaskStatus
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream

import mcp.types as types
from mcp.shared.message import SessionMessage
from mcp.server.lowlevel.server import Server as MCPServer

from asyncmcp.sns_sqs.server import SnsSqsTransport, OutgoingMessageEvent
from asyncmcp.sns_sqs.utils import SnsSqsServerConfig
from asyncmcp.common.aws_queue_utils import to_session_message, delete_sqs_message

logger = logging.getLogger(__name__)


class SnsSqsSessionManager:
    def __init__(
        self, app: MCPServer, config: SnsSqsServerConfig, sqs_client: Any, sns_client: Any, stateless: bool = False
    ):
        self.app = app
        self.config = config
        self.sqs_client = sqs_client
        self.sns_client = sns_client
        self.stateless = stateless

        self._session_lock = anyio.Lock()
        self._transport_instances: Dict[str, SnsSqsTransport] = {}

        self._outgoing_message_sender: Optional[MemoryObjectSendStream[OutgoingMessageEvent]] = None
        self._outgoing_message_receiver: Optional[MemoryObjectReceiveStream[OutgoingMessageEvent]] = None

        self._task_group: Optional[anyio.abc.TaskGroup] = None
        self._run_lock = threading.Lock()
        self._has_started = False

    @asynccontextmanager
    async def run(self):
        """
        Run the session manager with SNS/SQS message processing.

        Starts the main SQS listener that handles initialize requests
        and routes messages to appropriate sessions.
        """
        with self._run_lock:
            if self._has_started:
                raise RuntimeError("SnsSqsSessionManager.run() can only be called once per instance.")
            self._has_started = True

        self._outgoing_message_sender, self._outgoing_message_receiver = anyio.create_memory_object_stream[
            OutgoingMessageEvent
        ](1000)

        async with anyio.create_task_group() as tg:
            self._task_group = tg
            tg.start_soon(self._sqs_message_processor)
            tg.start_soon(self._event_driven_message_sender)

            logger.info("SNS/SQS session manager started")

            try:
                yield
            finally:
                logger.info("SNS/SQS session manager shutting down")
                tg.cancel_scope.cancel()
                await self._shutdown_all_sessions()

                if self._outgoing_message_sender:
                    await self._outgoing_message_sender.aclose()
                if self._outgoing_message_receiver:
                    await self._outgoing_message_receiver.aclose()

                self._task_group = None

    async def _sqs_message_processor(self) -> None:
        """
        Main SQS message processing loop.

        Listens for messages and either:
        1. Creates new session for 'initialize' requests
        2. Routes to existing session based on SessionId
        """
        logger.info("Starting SNS/SQS message processor")

        try:
            while True:
                # Check for cancellation
                await anyio.lowlevel.checkpoint()

                try:
                    # Poll SQS for messages
                    response = await anyio.to_thread.run_sync(
                        lambda: self.sqs_client.receive_message(
                            QueueUrl=self.config.sqs_queue_url,
                            MaxNumberOfMessages=self.config.max_messages,
                            WaitTimeSeconds=self.config.wait_time_seconds,
                            VisibilityTimeout=self.config.visibility_timeout_seconds,
                            MessageAttributeNames=["All"],
                        )
                    )

                    messages = response.get("Messages", [])
                    if messages:
                        # Process each message
                        for sqs_message in messages:
                            await self._process_single_message(sqs_message)
                    else:
                        await anyio.sleep(self.config.poll_interval_seconds)

                except Exception as e:
                    logger.error(f"Error in SNS/SQS message processor: {e}")
                    await anyio.sleep(min(self.config.poll_interval_seconds, 1.0))
        except anyio.get_cancelled_exc_class():
            logger.info("SNS/SQS message processor cancelled")
            raise
        except Exception as e:
            logger.error(f"Fatal error in SNS/SQS message processor: {e}")
            raise

    async def _process_single_message(self, sqs_message: Dict[str, Any]) -> None:
        try:
            session_message = await to_session_message(sqs_message)
            message_root = session_message.message.root

            # Extract SessionId from SQS message attributes or SNS notification
            session_id = None
            message_attrs = sqs_message.get("MessageAttributes", {})

            if "SessionId" in message_attrs:
                session_id = message_attrs["SessionId"]["StringValue"]
            else:
                # Check if this is an SNS notification and extract attributes from there
                try:
                    body = json.loads(sqs_message["Body"])
                    if "MessageAttributes" in body:
                        sns_attrs = body["MessageAttributes"]
                        if "SessionId" in sns_attrs:
                            session_id = sns_attrs["SessionId"]["Value"]
                except (json.JSONDecodeError, KeyError):
                    pass

            is_initialize_request = (
                isinstance(message_root, types.JSONRPCRequest) and message_root.method == "initialize"
            )

            if is_initialize_request:
                await self._handle_initialize_request(session_message, session_id, sqs_message)
            else:
                if session_id and session_id in self._transport_instances:
                    transport = self._transport_instances[session_id]
                    await transport.send_message(session_message)
                else:
                    logger.warning(f"No session found for message with SessionId: {session_id}")

            await delete_sqs_message(self.sqs_client, self.config.sqs_queue_url, sqs_message["ReceiptHandle"])

        except Exception as e:
            logger.error(f"Error processing SQS message: {e}")
            await delete_sqs_message(self.sqs_client, self.config.sqs_queue_url, sqs_message["ReceiptHandle"])

    async def _handle_initialize_request(
        self, session_message: SessionMessage, session_id: str | None, sqs_message: Dict[str, Any]
    ) -> None:
        if session_id and session_id in self._transport_instances:
            transport = self._transport_instances[session_id]
            await transport.send_message(session_message)
            return

        client_topic_arn = None
        message_root = session_message.message.root
        if isinstance(message_root, types.JSONRPCRequest) and message_root.params:
            if isinstance(message_root.params, dict):
                client_topic_arn = message_root.params.get("client_topic_arn")

        if not client_topic_arn:
            logger.error("Initialize request missing required 'client_topic_arn' parameter")
            await delete_sqs_message(self.sqs_client, self.config.sqs_queue_url, sqs_message["ReceiptHandle"])
            return

        async with self._session_lock:
            if not session_id:
                session_id = uuid4().hex

            transport = SnsSqsTransport(
                config=self.config,
                sqs_client=self.sqs_client,
                sns_client=self.sns_client,
                session_id=session_id,
                client_topic_arn=client_topic_arn,
                outgoing_message_sender=self._outgoing_message_sender,
            )

            self._transport_instances[session_id] = transport

            async def run_session(*, task_status: TaskStatus[None] = anyio.TASK_STATUS_IGNORED):
                try:
                    async with transport.connect() as (read_stream, write_stream):
                        task_status.started()
                        logger.info(f"Started SNS/SQS session: {session_id}")

                        await self.app.run(
                            read_stream,
                            write_stream,
                            self.app.create_initialization_options(),
                            stateless=self.stateless,
                        )
                except Exception as e:
                    logger.error(f"Session {session_id} crashed: {e}", exc_info=True)
                finally:
                    async with self._session_lock:
                        if session_id in self._transport_instances:
                            logger.info(f"Cleaning up session: {session_id}")
                            del self._transport_instances[session_id]

            assert self._task_group is not None
            await self._task_group.start(run_session)

            await transport.send_message(session_message)

    async def _event_driven_message_sender(self) -> None:
        """
        Background task to send outgoing messages from sessions back to SNS topics.
        """
        logger.info("Starting SNS message sender")

        if not self._outgoing_message_receiver:
            logger.error("No outgoing message receiver configured")
            return

        try:
            async with self._outgoing_message_receiver:
                async for message_event in self._outgoing_message_receiver:
                    try:
                        if not message_event.session_id:
                            logger.warning("Received message event with no session_id")
                            continue
                        transport = self._transport_instances.get(message_event.session_id)
                        if not transport or transport.is_terminated:
                            logger.warning(
                                f"Received message for unknown/terminated session: {message_event.session_id}"
                            )
                            continue

                        await transport.send_to_client_topic(message_event.message)

                    except Exception as e:
                        logger.error(f"Error processing outgoing message for session {message_event.session_id}: {e}")

        except anyio.get_cancelled_exc_class():
            logger.info("SNS message sender cancelled")
            raise
        except Exception as e:
            logger.error(f"Fatal error in SNS message sender: {e}")
            raise

    async def terminate_session(self, session_id: str) -> bool:
        async with self._session_lock:
            if session_id in self._transport_instances:
                transport = self._transport_instances[session_id]
                await transport.terminate()
                del self._transport_instances[session_id]
                logger.info(f"Terminated session: {session_id}")
                return True
            return False

    def get_all_sessions(self) -> Dict[str, dict]:
        return {
            session_id: {"session_id": session_id, "terminated": transport.is_terminated}
            for session_id, transport in self._transport_instances.items()
        }

    async def _shutdown_all_sessions(self) -> None:
        sessions_to_terminate = list(self._transport_instances.keys())

        for session_id in sessions_to_terminate:
            try:
                await self.terminate_session(session_id)
            except Exception as e:
                logger.error(f"Error terminating session {session_id}: {e}")

        self._transport_instances.clear()
