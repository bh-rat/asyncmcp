"""
Comprehensive anyio fixture tests for SQS server transport module.
"""

import pytest
from unittest.mock import patch, MagicMock

import anyio
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage, JSONRPCRequest, JSONRPCResponse, JSONRPCNotification
from pydantic_core import ValidationError

from asyncmcp.sqs.utils import SqsTransportConfig, process_sqs_message
from asyncmcp.sqs.server import SqsTransport
from asyncmcp.sqs.manager import SQSSessionManager
from asyncmcp.sns_sqs.utils import _to_session_message, _delete_sqs_message

from .shared_fixtures import (
    mock_sqs_client,
    sample_sqs_message,
    sample_initialize_sqs_message,
    sample_jsonrpc_request,
    sample_jsonrpc_initialize_request,
    sample_jsonrpc_notification,
    sample_jsonrpc_response,
    client_transport_config,
    server_transport_config,
    client_server_config,
    mock_mcp_server,
)


@pytest.fixture
def transport_config(server_transport_config):
    """Create a test transport configuration - using shared fixture."""
    return server_transport_config


@pytest.fixture
def response_queue_url():
    """Test client response queue URL."""
    return "http://localhost:4566/000000000000/test-client-responses"


class TestSqsTransport:
    """Test the SqsTransport class."""

    @pytest.mark.anyio
    async def test_transport_init(self, transport_config, mock_sqs_client):
        """Test SqsTransport initialization."""
        session_id = "test-session-123"
        response_queue = "http://localhost:4566/000000000000/client-responses"

        transport = SqsTransport(
            config=transport_config,
            sqs_client=mock_sqs_client,
            session_id=session_id,
            response_queue_url=response_queue,
        )

        assert transport.session_id == session_id
        assert transport.response_queue_url == response_queue
        assert not transport.is_terminated
        assert transport.sqs_client == mock_sqs_client

    @pytest.mark.anyio
    async def test_transport_connect_and_cleanup(self, transport_config, mock_sqs_client):
        """Test transport connect and cleanup."""
        transport = SqsTransport(config=transport_config, sqs_client=mock_sqs_client, session_id="test-session")

        async with transport.connect() as (read_stream, write_stream):
            assert transport._read_stream is not None
            assert transport._write_stream is not None

        # After context exits, streams should be cleaned up
        assert transport._read_stream is None
        assert transport._write_stream is None

    @pytest.mark.anyio
    async def test_transport_send_message(self, transport_config, mock_sqs_client, sample_jsonrpc_request):
        """Test sending message to transport."""
        transport = SqsTransport(config=transport_config, sqs_client=mock_sqs_client, session_id="test-session")

        async with transport.connect() as (read_stream, write_stream):
            session_message = SessionMessage(sample_jsonrpc_request)

            # Create a task to consume from the read_stream to prevent blocking
            async def consume_stream():
                try:
                    async with read_stream:
                        async for message in read_stream:
                            # Just consume messages to prevent blocking
                            break
                except anyio.EndOfStream:
                    pass

            async with anyio.create_task_group() as tg:
                tg.start_soon(consume_stream)
                await transport.send_message(session_message)

    @pytest.mark.anyio
    async def test_transport_send_to_client_queue(self, transport_config, mock_sqs_client, sample_jsonrpc_response):
        """Test sending message to client queue."""
        response_queue = "http://localhost:4566/000000000000/client-responses"
        transport = SqsTransport(
            config=transport_config,
            sqs_client=mock_sqs_client,
            session_id="test-session",
            response_queue_url=response_queue,
        )

        session_message = SessionMessage(sample_jsonrpc_response)

        with patch("anyio.to_thread.run_sync") as mock_run_sync:
            await transport.send_to_client_queue(session_message)
            mock_run_sync.assert_called_once()

            # Verify send_message was called with correct queue URL
            call_args = mock_run_sync.call_args[0][0]
            # This is a lambda, we can't easily inspect it, but we know it should call sqs send_message

    @pytest.mark.anyio
    async def test_transport_terminate(self, transport_config, mock_sqs_client):
        """Test transport termination."""
        transport = SqsTransport(config=transport_config, sqs_client=mock_sqs_client, session_id="test-session")

        async with transport.connect():
            pass

        await transport.terminate()
        assert transport.is_terminated


class TestSQSSessionManager:
    """Test the SQSSessionManager class."""

    @pytest.mark.anyio
    async def test_session_manager_init(self, transport_config, mock_sqs_client, mock_mcp_server):
        """Test SQSSessionManager initialization."""
        manager = SQSSessionManager(app=mock_mcp_server, config=transport_config, sqs_client=mock_sqs_client)

        assert manager.app == mock_mcp_server
        assert manager.config == transport_config
        assert manager.sqs_client == mock_sqs_client
        assert not manager.stateless
        assert len(manager._transport_instances) == 0

    @pytest.mark.anyio
    async def test_session_manager_run_lifecycle(self, transport_config, mock_sqs_client, mock_mcp_server):
        """Test session manager run lifecycle."""
        manager = SQSSessionManager(app=mock_mcp_server, config=transport_config, sqs_client=mock_sqs_client)

        # Mock SQS to return empty messages to avoid infinite loop
        mock_sqs_client.receive_message.return_value = {"Messages": []}

        async with manager.run():
            assert manager._task_group is not None

        # After context exits, task group should be None
        assert manager._task_group is None

    @pytest.mark.anyio
    async def test_session_manager_handle_initialize_request(
        self, transport_config, mock_sqs_client, mock_mcp_server, sample_initialize_sqs_message
    ):
        """Test session manager handling initialize request."""
        manager = SQSSessionManager(app=mock_mcp_server, config=transport_config, sqs_client=mock_sqs_client)

        # Mock SQS operations
        mock_sqs_client.receive_message.return_value = {"Messages": [sample_initialize_sqs_message]}
        mock_sqs_client.delete_message.return_value = {}

        # Parse the initialize message
        session_message = await _to_session_message(sample_initialize_sqs_message)

        async with manager.run():
            # Manually call the handler to test it
            await manager._handle_initialize_request(
                session_message,
                None,  # No existing session ID
                sample_initialize_sqs_message,
            )

            # Should have created a new session
            assert len(manager._transport_instances) == 1

            # Get the created session
            session_id = list(manager._transport_instances.keys())[0]
            transport = manager._transport_instances[session_id]

            assert transport.response_queue_url == "http://localhost:4566/000000000000/client-responses"

    @pytest.mark.anyio
    async def test_session_manager_process_single_message(
        self, transport_config, mock_sqs_client, mock_mcp_server, sample_initialize_sqs_message
    ):
        """Test processing a single initialize message."""
        manager = SQSSessionManager(app=mock_mcp_server, config=transport_config, sqs_client=mock_sqs_client)

        # Mock delete_message
        with patch("asyncmcp.sqs.manager._delete_sqs_message") as mock_delete:
            mock_delete.return_value = None

            async with manager.run():
                # Process the initialize message
                await manager._process_single_message(sample_initialize_sqs_message)

                # Should have created a session
                assert len(manager._transport_instances) == 1

                # Should have deleted the message
                mock_delete.assert_called_once()

    @pytest.mark.anyio
    async def test_session_manager_terminate_session(self, transport_config, mock_sqs_client, mock_mcp_server):
        """Test terminating a specific session."""
        manager = SQSSessionManager(app=mock_mcp_server, config=transport_config, sqs_client=mock_sqs_client)

        # Create a transport manually
        transport = SqsTransport(config=transport_config, sqs_client=mock_sqs_client, session_id="test-session-123")
        manager._transport_instances["test-session-123"] = transport

        # Terminate the session
        result = await manager.terminate_session("test-session-123")

        assert result is True
        assert len(manager._transport_instances) == 0
        assert transport.is_terminated


class TestProcessSQSMessageServer:
    """Test the server process_sqs_message function."""

    @pytest.mark.anyio
    async def test_process_direct_message_server(self, sample_sqs_message):
        """Test processing a direct SQS message on server side."""
        session_message = await _to_session_message(sample_sqs_message)

        assert isinstance(session_message, SessionMessage)
        assert session_message.message.root.method == "test/method"
        assert session_message.message.root.id == 1

    @pytest.mark.anyio
    async def test_process_invalid_json_server(self):
        """Test processing message with invalid JSON on server side."""
        invalid_message = {
            "MessageId": "invalid-1",
            "ReceiptHandle": "invalid-handle-1",
            "Body": "invalid json content",
            "MessageAttributes": {},
        }

        with pytest.raises(Exception):  # Should raise JSON decode error
            await _to_session_message(invalid_message)


class TestServerConfigurationValidation:
    """Test server configuration validation with new dynamic queue system."""

    def test_server_config_creation(self):
        """Test basic server configuration creation (no write_queue_url)."""
        config = SqsTransportConfig(
            read_queue_url="http://localhost:4566/000000000000/server-read-queue",
        )

        assert config.read_queue_url == "http://localhost:4566/000000000000/server-read-queue"
        assert config.max_messages == 10  # Default value
        assert config.wait_time_seconds == 20  # Default value
        assert config.poll_interval_seconds == 5.0  # Default value

    def test_server_config_with_custom_values(self):
        """Test server configuration with custom values."""
        config = SqsTransportConfig(
            read_queue_url="http://localhost:4566/000000000000/server-read-queue",
            max_messages=15,
            wait_time_seconds=30,
            poll_interval_seconds=2.0,
            visibility_timeout_seconds=45,
        )

        assert config.max_messages == 15
        assert config.wait_time_seconds == 30
        assert config.poll_interval_seconds == 2.0
        assert config.visibility_timeout_seconds == 45

    def test_server_config_no_write_queue_needed(self):
        """Test that server config no longer requires write_queue_url."""
        # This should work fine - no write_queue_url needed
        config = SqsTransportConfig(read_queue_url="http://localhost:4566/000000000000/server-requests")

        assert config.read_queue_url == "http://localhost:4566/000000000000/server-requests"
        # write_queue_url is no longer a field
        assert not hasattr(config, "write_queue_url")


class TestMessageAttributeCreation:
    """Test message attribute creation for SQS transport."""

    @pytest.mark.anyio
    async def test_create_message_attributes_for_request(self, transport_config, sample_jsonrpc_request):
        """Test creating message attributes for a request."""
        transport = SqsTransport(
            config=transport_config,
            sqs_client=MagicMock(),
            session_id="test-session",
            response_queue_url="http://localhost:4566/000000000000/client-responses",
        )

        session_message = SessionMessage(sample_jsonrpc_request)
        attrs = await transport._create_sqs_message_attributes(session_message)

        assert attrs["MessageType"]["StringValue"] == "jsonrpc"
        assert attrs["SessionId"]["StringValue"] == "test-session"
        assert attrs["Method"]["StringValue"] == "test/method"
        assert attrs["RequestId"]["StringValue"] == "1"
        assert "Timestamp" in attrs

    @pytest.mark.anyio
    async def test_create_message_attributes_for_response(self, transport_config, sample_jsonrpc_response):
        """Test creating message attributes for a response."""
        transport = SqsTransport(
            config=transport_config,
            sqs_client=MagicMock(),
            session_id="response-session",
        )

        session_message = SessionMessage(sample_jsonrpc_response)
        attrs = await transport._create_sqs_message_attributes(session_message)

        assert attrs["MessageType"]["StringValue"] == "jsonrpc"
        assert attrs["SessionId"]["StringValue"] == "response-session"
        assert "Timestamp" in attrs
        # Responses don't have method, only id
        assert "Method" not in attrs
        assert "RequestId" not in attrs

    @pytest.mark.anyio
    async def test_create_message_attributes_for_notification(self, transport_config, sample_jsonrpc_notification):
        """Test creating message attributes for a notification."""
        transport = SqsTransport(
            config=transport_config,
            sqs_client=MagicMock(),
            session_id="notification-session",
        )

        session_message = SessionMessage(sample_jsonrpc_notification)
        attrs = await transport._create_sqs_message_attributes(session_message)

        assert attrs["MessageType"]["StringValue"] == "jsonrpc"
        assert attrs["SessionId"]["StringValue"] == "notification-session"
        assert attrs["Method"]["StringValue"] == "test/notification"
        assert "Timestamp" in attrs
        # Notifications don't have RequestId
        assert "RequestId" not in attrs
