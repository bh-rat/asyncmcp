# src/asyncmcp/sqs/manager.py
import logging
import threading
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional
from uuid import uuid4

import anyio
from anyio.abc import TaskStatus

import mcp.types as types
from mcp.shared.message import SessionMessage
from mcp.server.lowlevel.server import Server as MCPServer

from .server import SqsTransport
from .utils import SqsTransportConfig, _to_session_message, _delete_sqs_message

logger = logging.getLogger(__name__)


class SQSSessionManager:
    def __init__(self, app: MCPServer, config: SqsTransportConfig, sqs_client: Any, stateless: bool = False):
        self.app = app
        self.config = config
        self.sqs_client = sqs_client
        # TODO: yet to be implemented
        self.stateless = stateless

        self._session_lock = anyio.Lock()
        self._transport_instances: Dict[str, SqsTransport] = {}

        self._task_group: Optional[anyio.abc.TaskGroup] = None
        self._run_lock = threading.Lock()
        self._has_started = False

    @asynccontextmanager
    async def run(self):
        """
        Run the session manager with SQS message processing.

        Starts the main SQS listener that handles initialize requests
        and routes messages to appropriate sessions.
        """
        with self._run_lock:
            if self._has_started:
                raise RuntimeError("SQSSessionManager.run() can only be called once per instance.")
            self._has_started = True

        async with anyio.create_task_group() as tg:
            self._task_group = tg
            tg.start_soon(self._sqs_message_processor)
            tg.start_soon(self._sqs_message_sender)

            logger.info("SQS session manager started")

            try:
                yield
            finally:
                logger.info("SQS session manager shutting down")
                tg.cancel_scope.cancel()
                await self._shutdown_all_sessions()
                self._task_group = None

    async def _sqs_message_processor(self) -> None:
        """
        Main SQS message processing loop.

        Listens for messages and either:
        1. Creates new session for 'initialize' requests
        2. Routes to existing session based on SessionId
        """
        logger.info("Starting SQS message processor")

        try:
            while True:
                # Check for cancellation
                await anyio.lowlevel.checkpoint()

                try:
                    # Poll SQS for messages
                    response = await anyio.to_thread.run_sync(
                        lambda: self.sqs_client.receive_message(
                            QueueUrl=self.config.read_queue_url,
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
                    logger.error(f"Error in SQS message processor: {e}")
                    await anyio.sleep(min(self.config.poll_interval_seconds, 1.0))
        except anyio.get_cancelled_exc_class():
            logger.info("SQS message processor cancelled")
            raise
        except Exception as e:
            logger.error(f"Fatal error in SQS message processor: {e}")
            raise

    async def _process_single_message(self, sqs_message: Dict[str, Any]) -> None:
        try:
            session_message = await _to_session_message(sqs_message)
            message_root = session_message.message.root

            message_attrs = sqs_message.get("MessageAttributes", {})
            session_id = None
            if "SessionId" in message_attrs:
                session_id = message_attrs["SessionId"]["StringValue"]

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

            await _delete_sqs_message(self.sqs_client, self.config.read_queue_url, sqs_message["ReceiptHandle"])

        except Exception as e:
            logger.error(f"Error processing SQS message: {e}")
            await _delete_sqs_message(self.sqs_client, self.config.read_queue_url, sqs_message["ReceiptHandle"])

    async def _handle_initialize_request(
        self, session_message: SessionMessage, session_id: str | None, sqs_message: Dict[str, Any]
    ) -> None:
        if session_id and session_id in self._transport_instances:
            transport = self._transport_instances[session_id]
            await transport.send_message(session_message)
            return

        response_queue_url = None
        message_root = session_message.message.root
        if isinstance(message_root, types.JSONRPCRequest) and message_root.params:
            if isinstance(message_root.params, dict):
                response_queue_url = message_root.params.get("response_queue_url")

        if not response_queue_url:
            logger.error("Initialize request missing required 'response_queue_url' parameter")
            await _delete_sqs_message(self.sqs_client, self.config.read_queue_url, sqs_message["ReceiptHandle"])
            return

        async with self._session_lock:
            if not session_id:
                session_id = uuid4().hex

            transport = SqsTransport(
                config=self.config,
                sqs_client=self.sqs_client,
                session_id=session_id,
                response_queue_url=response_queue_url,
            )

            self._transport_instances[session_id] = transport

            async def run_session(*, task_status: TaskStatus[None] = anyio.TASK_STATUS_IGNORED):
                try:
                    async with transport.connect() as (read_stream, write_stream):
                        task_status.started()
                        logger.info(f"Started SQS session: {session_id}")

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

    async def _sqs_message_sender(self) -> None:
        """
        Background task to send outgoing messages from sessions back to SQS.
        """
        logger.info("Starting SQS message sender")

        try:
            while True:
                # Check for cancellation
                await anyio.lowlevel.checkpoint()

                try:
                    # Check all sessions for outgoing messages
                    sessions_to_check = list(self._transport_instances.items())

                    for session_id, transport in sessions_to_check:
                        if transport.is_terminated:
                            continue

                        # Get outgoing message from this session
                        outgoing_message = await transport.get_outgoing_message()
                        if outgoing_message:
                            await self._send_message_to_sqs(outgoing_message, transport)

                    # Small delay to prevent busy waiting
                    await anyio.sleep(0.01)  # Reduced from 0.1 to 0.01 for better responsiveness

                except Exception as e:
                    logger.error(f"Error in SQS message sender: {e}")
                    await anyio.sleep(1.0)
        except anyio.get_cancelled_exc_class():
            logger.info("SQS message sender cancelled")
            raise
        except Exception as e:
            logger.error(f"Fatal error in SQS message sender: {e}")
            raise

    async def _send_message_to_sqs(self, session_message: SessionMessage, transport: SqsTransport) -> None:
        await transport.send_to_client_queue(session_message)

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
        # Stats removed from transport layer
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
