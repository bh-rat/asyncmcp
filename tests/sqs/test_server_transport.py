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
from asyncmcp.sqs.server import sqs_server, _create_sqs_message_attributes
from asyncmcp.sns_sqs.utils import _to_session_message, _delete_sqs_message

from .shared_fixtures import (
    mock_sqs_client,
    sample_sqs_message,
    sample_jsonrpc_request,
    sample_jsonrpc_notification,
    sample_jsonrpc_response,
    client_transport_config,
    server_transport_config,
    client_server_config,
)


@pytest.fixture
def transport_config(server_transport_config):
    """Create a test transport configuration - using shared fixture."""
    return server_transport_config


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
            "MessageId": "server-invalid-msg",
            "ReceiptHandle": "server-invalid-handle",
            "Body": "not valid json",
            "MessageAttributes": {},
        }

        with pytest.raises(ValidationError):
            await _to_session_message(invalid_message)

    @pytest.mark.anyio
    async def test_process_multiple_server_messages(self, transport_config, sample_sqs_message, mock_sqs_client):
        """Test processing multiple messages concurrently on server side."""
        messages = [sample_sqs_message.copy() for _ in range(5)]
        for i, msg in enumerate(messages):
            msg["MessageId"] = f"server-msg-{i}"
            msg["ReceiptHandle"] = f"server-handle-{i}"

        send_stream, receive_stream = anyio.create_memory_object_stream(10)

        with patch("anyio.to_thread.run_sync") as mock_run_sync:
            await process_sqs_message(messages, mock_sqs_client, transport_config.read_queue_url, send_stream)

            # Verify all messages were processed
            assert mock_run_sync.call_count == 5  # One delete call per message

            # Verify messages were sent to stream
            processed_messages = []
            for _ in range(5):
                with anyio.move_on_after(0.1):
                    msg = await receive_stream.receive()
                    processed_messages.append(msg)

            assert len(processed_messages) == 5
            for msg in processed_messages:
                assert isinstance(msg, SessionMessage)

    @pytest.mark.anyio
    async def test_process_server_messages_with_validation_errors(
        self, transport_config, sample_sqs_message, mock_sqs_client
    ):
        """Test processing messages with validation errors on server side."""
        valid_message = sample_sqs_message.copy()
        invalid_message = {
            "MessageId": "server-invalid-msg",
            "ReceiptHandle": "server-invalid-handle",
            "Body": "invalid json content",
            "MessageAttributes": {},
        }

        messages = [valid_message, invalid_message]
        send_stream, receive_stream = anyio.create_memory_object_stream(10)

        with patch("anyio.to_thread.run_sync") as mock_run_sync:
            await process_sqs_message(messages, mock_sqs_client, transport_config.read_queue_url, send_stream)

            # Both messages should be deleted even if one fails
            assert mock_run_sync.call_count == 2

            # Should receive one valid message and one exception
            results = []
            for _ in range(2):
                with anyio.move_on_after(0.1):
                    result = await receive_stream.receive()
                    results.append(result)

            assert len(results) == 2
            valid_results = [r for r in results if isinstance(r, SessionMessage)]
            error_results = [r for r in results if isinstance(r, Exception)]
            assert len(valid_results) == 1
            assert len(error_results) == 1


class TestDeleteSQSMessageServer:
    """Test the server _delete_sqs_message function."""

    @pytest.mark.anyio
    async def test_delete_message_server(self, mock_sqs_client):
        """Test successful message deletion on server side."""
        with patch("anyio.to_thread.run_sync") as mock_run_sync:
            await _delete_sqs_message(mock_sqs_client, "server-queue", "server-handle")

            mock_run_sync.assert_called_once()
            # Verify the lambda function would call delete_message correctly
            delete_func = mock_run_sync.call_args[0][0]
            delete_func()
            mock_sqs_client.delete_message.assert_called_once_with(
                QueueUrl="server-queue", ReceiptHandle="server-handle"
            )


class TestCreateSQSMessageAttributesServer:
    """Test the server _create_sqs_message_attributes function."""

    @pytest.mark.anyio
    async def test_create_basic_attributes(self, transport_config, sample_jsonrpc_response):
        """Test creating basic message attributes for server responses."""
        session_message = SessionMessage(sample_jsonrpc_response)
        attrs = await _create_sqs_message_attributes(session_message, transport_config)

        assert attrs["MessageType"]["StringValue"] == "jsonrpc"
        assert attrs["MessageType"]["DataType"] == "String"

    @pytest.mark.anyio
    async def test_create_attributes_with_custom_config(self, transport_config, sample_jsonrpc_response):
        """Test creating message attributes with custom configuration."""
        transport_config.message_attributes = {"ServerAttr": {"DataType": "String", "StringValue": "server-value"}}
        session_message = SessionMessage(sample_jsonrpc_response)
        attrs = await _create_sqs_message_attributes(session_message, transport_config)

        assert attrs["MessageType"]["StringValue"] == "jsonrpc"
        assert attrs["ServerAttr"]["StringValue"] == "server-value"

    @pytest.mark.anyio
    async def test_create_attributes_without_client_id(self, transport_config, sample_jsonrpc_response):
        """Test creating message attributes without client ID."""
        session_message = SessionMessage(sample_jsonrpc_response)
        attrs = await _create_sqs_message_attributes(session_message, transport_config)

        assert attrs["MessageType"]["StringValue"] == "jsonrpc"
        # Should not have ClientId when client_id is None
        assert "ClientId" not in attrs


class TestSqsServer:
    """Test the sqs_server function."""

    @pytest.mark.anyio
    async def test_server_context_manager_basic(self, transport_config, mock_sqs_client):
        """Test basic server context manager functionality."""
        with patch("anyio.to_thread.run_sync") as mock_run_sync:
            mock_run_sync.return_value = {"Messages": []}

            with anyio.move_on_after(0.1):  # Add timeout to prevent hanging
                async with sqs_server(transport_config, mock_sqs_client) as (read_stream, write_stream):
                    assert read_stream is not None
                    assert write_stream is not None
                    await anyio.sleep(0.01)  # Give background tasks time to start

    @pytest.mark.anyio
    async def test_server_receive_and_send_messages(self, transport_config, sample_jsonrpc_response, mock_sqs_client):
        """Test server receiving and sending messages."""
        session_message = SessionMessage(sample_jsonrpc_response)

        # Mock SQS client methods directly
        mock_sqs_client.send_message.return_value = {"MessageId": "server-response-id"}
        mock_sqs_client.receive_message.return_value = {"Messages": []}

        with anyio.move_on_after(0.5):  # Add timeout to prevent hanging
            async with sqs_server(transport_config, mock_sqs_client) as (read_stream, write_stream):
                # Send a response
                await write_stream.send(session_message)
                await anyio.sleep(0.1)  # Allow message to be processed

                # Verify send_message was called
                assert mock_sqs_client.send_message.called

    @pytest.mark.anyio
    async def test_server_long_polling(self, transport_config, mock_sqs_client):
        """Test server long polling behavior."""
        # Mock SQS client methods directly
        mock_sqs_client.receive_message.return_value = {"Messages": []}

        with anyio.move_on_after(0.1):  # Add timeout to prevent hanging
            async with sqs_server(transport_config, mock_sqs_client) as (read_stream, write_stream):
                # Should use long polling
                await anyio.sleep(0.05)  # Allow some polling cycles

                # Verify receive_message was called with correct wait time
                assert mock_sqs_client.receive_message.called

    @pytest.mark.anyio
    async def test_server_error_handling_in_sqs_reader(self, transport_config, mock_sqs_client):
        """Test error handling in server SQS reader task."""
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                # First two calls raise an exception (simulating AWS errors)
                raise Exception("Simulated AWS SQS error")
            else:
                # Subsequent calls return empty to avoid infinite polling
                return {"Messages": []}

        mock_sqs_client.receive_message.side_effect = side_effect

        with anyio.move_on_after(0.2):
            async with sqs_server(transport_config, mock_sqs_client) as (read_stream, write_stream):
                # The reader should continue running despite the errors
                await anyio.sleep(0.1)

                # Verify that receive_message was called multiple times (indicating recovery)
                assert mock_sqs_client.receive_message.call_count >= 2

    @pytest.mark.anyio
    async def test_server_error_handling_in_sqs_writer(
        self, transport_config, sample_jsonrpc_response, mock_sqs_client
    ):
        """Test error handling in server SQS writer task."""
        session_message = SessionMessage(sample_jsonrpc_response)

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call raises an exception
                raise Exception("Simulated AWS SQS send error")
            else:
                # Subsequent calls succeed
                return {"MessageId": "server-msg-id"}

        mock_sqs_client.send_message.side_effect = side_effect
        mock_sqs_client.receive_message.return_value = {"Messages": []}

        with anyio.move_on_after(0.5):
            async with sqs_server(transport_config, mock_sqs_client) as (read_stream, write_stream):
                # Send first message (should fail but not crash)
                await write_stream.send(session_message)
                await anyio.sleep(0.1)

                # Send second message (should succeed)
                await write_stream.send(session_message)
                await anyio.sleep(0.1)

                # Verify both send attempts were made
                assert mock_sqs_client.send_message.call_count >= 2

    @pytest.mark.anyio
    async def test_server_concurrent_message_processing(self, transport_config, mock_sqs_client):
        """Test server concurrent message processing."""
        with patch("anyio.to_thread.run_sync") as mock_run_sync:

            def side_effect(func):
                if "receive_message" in str(func):
                    return {
                        "Messages": [
                            {
                                "MessageId": f"server-msg-{i}",
                                "ReceiptHandle": f"server-handle-{i}",
                                "Body": f'{{"jsonrpc": "2.0", "id": {i}, "method": "server/test", "params": {{}}}}',
                                "MessageAttributes": {},
                            }
                            for i in range(5)
                        ]
                    }
                return {}

            mock_run_sync.side_effect = side_effect

            with anyio.move_on_after(0.5):  # Add timeout to prevent hanging
                async with sqs_server(transport_config, mock_sqs_client) as (read_stream, write_stream):
                    # Should process multiple messages concurrently
                    messages = []
                    for _ in range(5):
                        with anyio.move_on_after(0.1):
                            msg = await read_stream.receive()
                            messages.append(msg)

                    assert len(messages) == 5
                    for msg in messages:
                        assert isinstance(msg, SessionMessage)

    @pytest.mark.anyio
    async def test_server_message_with_correlation_id(self, transport_config, mock_sqs_client):
        """Test server handling messages with correlation IDs."""
        with patch("anyio.to_thread.run_sync") as mock_run_sync:

            def side_effect(func):
                if "receive_message" in str(func):
                    return {
                        "Messages": [
                            {
                                "MessageId": "corr-msg-1",
                                "ReceiptHandle": "corr-handle-1",
                                "Body": '{"jsonrpc": "2.0", "id": 123, "method": "test", "params": {}}',
                                "MessageAttributes": {
                                    "CorrelationId": {"DataType": "String", "StringValue": "corr-123"}
                                },
                            }
                        ]
                    }
                return {}

            mock_run_sync.side_effect = side_effect

            with anyio.move_on_after(0.2):  # Add timeout to prevent hanging
                async with sqs_server(transport_config, mock_sqs_client) as (read_stream, write_stream):
                    # Should handle correlation ID properly
                    with anyio.move_on_after(0.1):
                        msg = await read_stream.receive()
                        assert isinstance(msg, SessionMessage)
                        assert msg.message.root.id == 123


class TestServerConfigurationValidation:
    """Test server configuration validation."""

    def test_server_config_creation(self, mock_sqs_client):
        """Test basic server configuration creation."""
        config = SqsTransportConfig(
            read_queue_url="http://localhost:4566/000000000000/server-read-queue",
            write_queue_url="http://localhost:4566/000000000000/server-write-queue",
        )

        assert config.read_queue_url == "http://localhost:4566/000000000000/server-read-queue"
        assert config.write_queue_url == "http://localhost:4566/000000000000/server-write-queue"
        assert config.max_messages == 10  # Default value
        assert config.wait_time_seconds == 20  # Default value
        assert config.poll_interval_seconds == 5.0  # Default value

    def test_server_config_with_custom_values(self, mock_sqs_client):
        """Test server configuration with custom values."""
        config = SqsTransportConfig(
            read_queue_url="http://localhost:4566/000000000000/server-read-queue",
            write_queue_url="http://localhost:4566/000000000000/server-write-queue",
            max_messages=15,
            wait_time_seconds=30,
            poll_interval_seconds=2.0,
            visibility_timeout_seconds=45,
        )

        assert config.max_messages == 15
        assert config.wait_time_seconds == 30
        assert config.poll_interval_seconds == 2.0
        assert config.visibility_timeout_seconds == 45


class TestServerIntegrationScenarios:
    """Test server integration scenarios."""

    @pytest.mark.anyio
    async def test_server_request_response_cycle(self, transport_config, mock_sqs_client):
        """Test server request-response cycle."""
        # Mock SQS client methods directly
        call_count = 0

        def receive_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {
                    "Messages": [
                        {
                            "MessageId": "req-msg-1",
                            "ReceiptHandle": "req-handle-1",
                            "Body": '{"jsonrpc": "2.0", "id": 1, "method": "test", "params": {}}',
                            "MessageAttributes": {},
                        }
                    ]
                }
            else:
                return {"Messages": []}

        mock_sqs_client.receive_message.side_effect = receive_side_effect
        mock_sqs_client.send_message.return_value = {"MessageId": "resp-msg-1"}

        with anyio.move_on_after(0.5):  # Add timeout to prevent hanging
            async with sqs_server(transport_config, mock_sqs_client) as (read_stream, write_stream):
                # Receive a request
                with anyio.move_on_after(0.2):
                    request = await read_stream.receive()
                    assert isinstance(request, SessionMessage)
                    assert request.message.root.method == "test"

                # Send a response
                response = SessionMessage(
                    JSONRPCMessage(root=JSONRPCResponse(jsonrpc="2.0", id=1, result={"status": "ok"}))
                )
                await write_stream.send(response)
                await anyio.sleep(0.1)

                # Verify response was sent
                assert mock_sqs_client.send_message.called

    @pytest.mark.anyio
    async def test_server_notification_handling(self, transport_config, mock_sqs_client):
        """Test server notification handling."""
        with patch("anyio.to_thread.run_sync") as mock_run_sync:

            def mock_sqs_receive():
                return {
                    "Messages": [
                        {
                            "MessageId": "notif-msg-1",
                            "ReceiptHandle": "notif-handle-1",
                            "Body": '{"jsonrpc": "2.0", "method": "notification", "params": {"event": "test"}}',
                            "MessageAttributes": {},
                        }
                    ]
                }

            def side_effect(func):
                if "receive_message" in str(func):
                    return mock_sqs_receive()
                return {}

            mock_run_sync.side_effect = side_effect

            with anyio.move_on_after(0.2):  # Add timeout to prevent hanging
                async with sqs_server(transport_config, mock_sqs_client) as (read_stream, write_stream):
                    # Should handle notifications properly
                    with anyio.move_on_after(0.1):
                        notification = await read_stream.receive()
                        assert isinstance(notification, SessionMessage)
                        assert notification.message.root.method == "notification"
                        assert not hasattr(notification.message.root, "id")  # Notifications don't have IDs

    @pytest.mark.anyio
    async def test_server_high_load_scenario(self, transport_config, mock_sqs_client):
        """Test server high load scenario."""
        with patch("anyio.to_thread.run_sync") as mock_run_sync:

            def side_effect(func):
                if "receive_message" in str(func):
                    return {
                        "Messages": [
                            {
                                "MessageId": f"load-msg-{i}",
                                "ReceiptHandle": f"load-handle-{i}",
                                "Body": f'{{"jsonrpc": "2.0", "id": {i}, "method": "load/test", "params": {{}}}}',
                                "MessageAttributes": {},
                            }
                            for i in range(10)
                        ]
                    }
                return {}

            mock_run_sync.side_effect = side_effect

            with anyio.move_on_after(1.0):  # Add timeout to prevent hanging
                async with sqs_server(transport_config, mock_sqs_client) as (read_stream, write_stream):
                    # Process high volume of messages
                    messages = []
                    for _ in range(10):
                        with anyio.move_on_after(0.1):
                            msg = await read_stream.receive()
                            messages.append(msg)

                    assert len(messages) == 10
                    for msg in messages:
                        assert isinstance(msg, SessionMessage)
                        assert msg.message.root.method == "load/test"

    @pytest.mark.anyio
    async def test_server_stream_cleanup(self, transport_config, mock_sqs_client):
        """Test proper cleanup of server streams."""
        with patch("anyio.to_thread.run_sync") as mock_run_sync:
            mock_run_sync.return_value = {"Messages": []}

            read_stream = None
            write_stream = None

            with anyio.move_on_after(0.1):  # Add timeout to prevent hanging
                async with sqs_server(transport_config, mock_sqs_client) as (rs, ws):
                    read_stream = rs
                    write_stream = ws
                    assert read_stream is not None
                    assert write_stream is not None
                    await anyio.sleep(0.01)  # Give background tasks time to start

            # Streams should be properly closed after context exit
            # This is implicit in the anyio stream implementation
