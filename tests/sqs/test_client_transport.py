"""
Comprehensive anyio fixture tests for SQS client transport module.
"""

import pytest
from unittest.mock import patch, MagicMock

import anyio
from mcp.shared.message import SessionMessage

# Updated imports to use correct modules
from asyncmcp.sqs.utils import SqsClientConfig
from asyncmcp.sqs.client import sqs_client, _create_sqs_message_attributes
from asyncmcp.common.aws_queue_utils import to_session_message

from .shared_fixtures import (
    mock_sqs_client,
    sample_sqs_message,
    sample_jsonrpc_request,
    sample_jsonrpc_initialize_request,
    sample_jsonrpc_notification,
    client_transport_config,
    client_response_queue_url,
)


@pytest.fixture
def transport_config(client_transport_config):
    """Create a test transport configuration - using shared fixture."""
    return client_transport_config


class TestSqsClient:
    """Test the sqs_client function with new dynamic queue system."""

    @pytest.mark.anyio
    async def test_client_context_manager_basic(self, transport_config, client_response_queue_url, mock_sqs_client):
        """Test basic client context manager functionality."""
        with patch("anyio.to_thread.run_sync") as mock_run_sync:
            mock_run_sync.return_value = {"Messages": []}

            with anyio.move_on_after(0.1):  # Add timeout to prevent hanging
                async with sqs_client(transport_config, mock_sqs_client) as (
                    read_stream,
                    write_stream,
                ):
                    assert read_stream is not None
                    assert write_stream is not None
                    await anyio.sleep(0.01)  # Give background tasks time to start

    @pytest.mark.anyio
    async def test_client_send_initialize_request(
        self, transport_config, client_response_queue_url, mock_sqs_client, sample_jsonrpc_initialize_request
    ):
        """Test client sending initialize request with response queue URL."""
        session_message = SessionMessage(sample_jsonrpc_initialize_request)

        # Mock SQS client methods
        mock_sqs_client.send_message.return_value = {"MessageId": "init-msg-id"}
        mock_sqs_client.receive_message.return_value = {"Messages": []}

        with anyio.move_on_after(0.5):
            async with sqs_client(transport_config, mock_sqs_client) as (
                read_stream,
                write_stream,
            ):
                # Send initialize request
                await write_stream.send(session_message)
                await anyio.sleep(0.1)  # Allow message to be processed

                # Verify send_message was called
                assert mock_sqs_client.send_message.called

                # Get the call arguments to verify response_queue_url was added
                call_args = mock_sqs_client.send_message.call_args
                message_body = call_args[1]["MessageBody"]

                # Parse the message body and check it contains response_queue_url
                import json

                parsed_message = json.loads(message_body)
                assert "params" in parsed_message
                assert "response_queue_url" in parsed_message["params"]
                assert parsed_message["params"]["response_queue_url"] == client_response_queue_url

    @pytest.mark.anyio
    async def test_client_send_regular_request(
        self, transport_config, client_response_queue_url, mock_sqs_client, sample_jsonrpc_request
    ):
        """Test client sending regular (non-initialize) request."""
        session_message = SessionMessage(sample_jsonrpc_request)

        # Mock SQS client methods
        mock_sqs_client.send_message.return_value = {"MessageId": "request-msg-id"}
        mock_sqs_client.receive_message.return_value = {"Messages": []}

        with anyio.move_on_after(0.5):
            async with sqs_client(transport_config, mock_sqs_client) as (
                read_stream,
                write_stream,
            ):
                # Send regular request
                await write_stream.send(session_message)
                await anyio.sleep(0.1)

                # Verify send_message was called
                assert mock_sqs_client.send_message.called

                # Regular requests should not have response_queue_url added
                call_args = mock_sqs_client.send_message.call_args
                message_body = call_args[1]["MessageBody"]

                import json

                parsed_message = json.loads(message_body)
                # Should be the original message without response_queue_url added
                assert parsed_message["method"] == "test/method"

    @pytest.mark.anyio
    async def test_client_receive_response(self, transport_config, client_response_queue_url, mock_sqs_client):
        """Test client receiving responses from its response queue."""
        call_count = 0

        def mock_receive(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {
                    "Messages": [
                        {
                            "MessageId": "response-123",
                            "ReceiptHandle": "response-handle-123",
                            "Body": '{"jsonrpc": "2.0", "id": 1, "result": {"status": "success"}}',
                            "MessageAttributes": {},
                        }
                    ]
                }
            else:
                return {"Messages": []}

        with patch("anyio.to_thread.run_sync") as mock_run_sync:
            mock_run_sync.side_effect = lambda func: func()
            mock_sqs_client.receive_message.side_effect = mock_receive
            mock_sqs_client.delete_message.return_value = {}

            with anyio.move_on_after(0.5):
                async with sqs_client(transport_config, mock_sqs_client) as (
                    read_stream,
                    write_stream,
                ):
                    # Wait for response
                    with anyio.move_on_after(0.2):
                        response = await read_stream.receive()
                        assert isinstance(response, SessionMessage)
                        assert response.message.root.result == {"status": "success"}

    @pytest.mark.anyio
    async def test_client_error_handling_in_reader(self, client_response_queue_url, mock_sqs_client):
        """Test error handling in client SQS reader task."""
        # Create a custom config with fast polling for this test
        fast_config = SqsClientConfig(
            read_queue_url="http://localhost:4566/000000000000/server-requests",
            response_queue_url="http://localhost:4566/000000000000/client-responses",
            client_id="test-client",
            poll_interval_seconds=0.01,  # Very fast polling for quick retries
        )

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                # First two calls raise an exception
                raise Exception("Simulated SQS error")
            else:
                # Subsequent calls return empty
                return {"Messages": []}

        mock_sqs_client.receive_message.side_effect = side_effect

        with anyio.move_on_after(0.2):
            async with sqs_client(fast_config, mock_sqs_client) as (
                read_stream,
                write_stream,
            ):
                # The reader should continue running despite the errors
                await anyio.sleep(0.1)

                # Verify that receive_message was called multiple times (indicating recovery)
                assert mock_sqs_client.receive_message.call_count >= 2

    @pytest.mark.anyio
    async def test_client_error_handling_in_writer(
        self, transport_config, client_response_queue_url, sample_jsonrpc_request, mock_sqs_client
    ):
        """Test error handling in client SQS writer task."""
        session_message = SessionMessage(sample_jsonrpc_request)

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call raises an exception
                raise Exception("Simulated SQS send error")
            else:
                # Subsequent calls succeed
                return {"MessageId": "success-msg-id"}

        mock_sqs_client.send_message.side_effect = side_effect
        mock_sqs_client.receive_message.return_value = {"Messages": []}

        with anyio.move_on_after(0.5):
            async with sqs_client(transport_config, mock_sqs_client) as (
                read_stream,
                write_stream,
            ):
                # Send first message (should fail but not crash)
                await write_stream.send(session_message)
                await anyio.sleep(0.1)

                # Send second message (should succeed)
                await write_stream.send(session_message)
                await anyio.sleep(0.1)

                # Verify both send attempts were made
                assert mock_sqs_client.send_message.call_count >= 2


class TestProcessSQSMessage:
    """Test the process_sqs_message function."""

    @pytest.mark.anyio
    async def test_process_direct_message(self, sample_sqs_message):
        """Test processing a direct SQS message."""
        session_message = await to_session_message(sample_sqs_message)

        assert isinstance(session_message, SessionMessage)
        assert session_message.message.root.method == "test/method"
        assert session_message.message.root.id == 1

    @pytest.mark.anyio
    async def test_process_invalid_json(self):
        """Test processing message with invalid JSON."""
        invalid_message = {
            "MessageId": "invalid-1",
            "ReceiptHandle": "invalid-handle-1",
            "Body": "invalid json content",
            "MessageAttributes": {},
        }

        with pytest.raises(ValueError):
            await to_session_message(invalid_message)


class TestCreateSQSMessageAttributesClient:
    """Test the client _create_sqs_message_attributes function."""

    @pytest.mark.anyio
    async def test_create_basic_attributes(self, transport_config, sample_jsonrpc_request):
        """Test creating basic message attributes for client requests."""
        session_message = SessionMessage(sample_jsonrpc_request)
        attrs = await _create_sqs_message_attributes(
            session_message, transport_config, "test-client-123", "test-session-123"
        )

        assert attrs["MessageType"]["StringValue"] == "jsonrpc"
        assert attrs["MessageType"]["DataType"] == "String"
        assert attrs["ClientId"]["StringValue"] == "test-client-123"
        assert attrs["RequestId"]["StringValue"] == "1"
        assert attrs["Method"]["StringValue"] == "test/method"
        assert "Timestamp" in attrs
        assert attrs["SessionId"]["StringValue"] == "test-session-123"

    @pytest.mark.anyio
    async def test_create_attributes_for_notification(self, transport_config, sample_jsonrpc_notification):
        """Test creating message attributes for client notifications."""
        session_message = SessionMessage(sample_jsonrpc_notification)
        attrs = await _create_sqs_message_attributes(session_message, transport_config, "notif-client", "notif-session")

        assert attrs["MessageType"]["StringValue"] == "jsonrpc"
        assert attrs["ClientId"]["StringValue"] == "notif-client"
        assert attrs["Method"]["StringValue"] == "test/notification"
        assert "RequestId" not in attrs  # Notifications don't have request IDs
        assert attrs["SessionId"]["StringValue"] == "notif-session"

    @pytest.mark.anyio
    async def test_create_attributes_with_custom_config(self, transport_config, sample_jsonrpc_request):
        """Test creating message attributes with custom configuration."""
        transport_config.message_attributes = {"CustomAttr": "custom-value"}
        session_message = SessionMessage(sample_jsonrpc_request)
        attrs = await _create_sqs_message_attributes(
            session_message, transport_config, "custom-client", "custom-session"
        )

        assert attrs["MessageType"]["StringValue"] == "jsonrpc"
        assert attrs["ClientId"]["StringValue"] == "custom-client"
        assert attrs["CustomAttr"]["StringValue"] == "custom-value"
        assert attrs["SessionId"]["StringValue"] == "custom-session"

    @pytest.mark.anyio
    async def test_create_attributes_without_session_id(self, transport_config, sample_jsonrpc_request):
        """Test creating message attributes without session ID (initialize request)."""
        session_message = SessionMessage(sample_jsonrpc_request)
        attrs = await _create_sqs_message_attributes(session_message, transport_config, "init-client", None)

        assert attrs["MessageType"]["StringValue"] == "jsonrpc"
        assert attrs["ClientId"]["StringValue"] == "init-client"
        assert "SessionId" not in attrs  # Should not include SessionId when None


class TestClientConfigurationValidation:
    """Test client configuration validation with new dynamic queue system."""

    def test_client_config_creation(self):
        """Test basic client configuration creation."""
        config = SqsClientConfig(
            read_queue_url="http://localhost:4566/000000000000/server-requests",
            response_queue_url="http://localhost:4566/000000000000/client-responses",
        )

        assert config.read_queue_url == "http://localhost:4566/000000000000/server-requests"
        assert config.response_queue_url == "http://localhost:4566/000000000000/client-responses"
        assert config.max_messages == 10  # Default value
        assert config.wait_time_seconds == 20  # Default value
        assert config.poll_interval_seconds == 5.0  # Default value

    def test_client_config_with_custom_values(self):
        """Test client configuration with custom values."""
        config = SqsClientConfig(
            read_queue_url="http://localhost:4566/000000000000/server-requests",
            response_queue_url="http://localhost:4566/000000000000/client-responses",
            max_messages=5,
            wait_time_seconds=10,
            poll_interval_seconds=1.0,
            client_id="custom-client-id",
        )

        assert config.max_messages == 5
        assert config.wait_time_seconds == 10
        assert config.poll_interval_seconds == 1.0
        assert config.client_id == "custom-client-id"

    def test_client_config_no_write_queue_needed(self):
        """Test that client config no longer requires write_queue_url."""
        # This should work fine - no write_queue_url needed
        config = SqsClientConfig(
            read_queue_url="http://localhost:4566/000000000000/server-requests",
            response_queue_url="http://localhost:4566/000000000000/client-responses",
        )

        assert config.read_queue_url == "http://localhost:4566/000000000000/server-requests"
        assert config.response_queue_url == "http://localhost:4566/000000000000/client-responses"
        # write_queue_url is no longer a field
        assert not hasattr(config, "write_queue_url")
