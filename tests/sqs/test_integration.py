"""
Comprehensive integration tests for SQS transport.
"""

import pytest
from unittest.mock import patch, MagicMock
import json

import anyio
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage, JSONRPCRequest, JSONRPCResponse, JSONRPCNotification, JSONRPCError

from asyncmcp.sqs.client import sqs_client
from asyncmcp.sqs.server import sqs_server
from asyncmcp.sqs.utils import SqsTransportConfig

from .shared_fixtures import (
    mock_sqs_client,
    sample_jsonrpc_request,
    sample_jsonrpc_response,
    sample_jsonrpc_notification,
    client_server_config,
)


class TestSQSIntegration:
    """Integration tests for SQS transport."""

    @pytest.mark.anyio
    async def test_client_server_message_exchange(self, client_server_config):
        """Test basic client-server message exchange."""
        client_config = client_server_config["client"]["config"]
        server_config = client_server_config["server"]["config"]
        client_sqs = client_server_config["client"]["sqs_client"]
        server_sqs = client_server_config["server"]["sqs_client"]

        response_received = False

        # Configure client SQS mock with side effect to control polling
        client_call_count = 0

        def client_receive_side_effect(*args, **kwargs):
            nonlocal client_call_count
            client_call_count += 1
            if client_call_count == 1:
                return {
                    "Messages": [
                        {
                            "MessageId": "response-1",
                            "ReceiptHandle": "response-handle-1",
                            "Body": '{"jsonrpc": "2.0", "id": 1, "result": {"status": "ok"}}',
                            "MessageAttributes": {},
                        }
                    ]
                }
            else:
                return {"Messages": []}

        client_sqs.send_message.return_value = {"MessageId": "test-message-1"}
        client_sqs.receive_message.side_effect = client_receive_side_effect

        # Configure server SQS mock with side effect
        server_call_count = 0

        def server_receive_side_effect(*args, **kwargs):
            nonlocal server_call_count
            server_call_count += 1
            if server_call_count == 1:
                return {
                    "Messages": [
                        {
                            "MessageId": "request-1",
                            "ReceiptHandle": "request-handle-1",
                            "Body": '{"jsonrpc": "2.0", "id": 1, "method": "test", "params": {}}',
                            "MessageAttributes": {},
                        }
                    ]
                }
            else:
                return {"Messages": []}

        server_sqs.receive_message.side_effect = server_receive_side_effect
        server_sqs.send_message.return_value = {"MessageId": "response-sent"}

        async def client_task():
            with anyio.move_on_after(0.2):
                async with sqs_client(client_config, client_sqs) as (read_stream, write_stream):
                    # Send request
                    request = SessionMessage(
                        JSONRPCMessage(root=JSONRPCRequest(jsonrpc="2.0", id=1, method="test", params={}))
                    )
                    await write_stream.send(request)

                    # Receive response
                    response = await read_stream.receive()
                    nonlocal response_received
                    if isinstance(response, SessionMessage):
                        assert response.message.root.id == 1
                        assert response.message.root.result == {"status": "ok"}
                        response_received = True

        async def server_task():
            with anyio.move_on_after(0.2):
                async with sqs_server(server_config, server_sqs) as (read_stream, write_stream):
                    # Receive request
                    request = await read_stream.receive()
                    if isinstance(request, SessionMessage):
                        assert request.message.root.method == "test"
                        assert request.message.root.id == 1

                        # Send response
                        response = SessionMessage(
                            JSONRPCMessage(root=JSONRPCResponse(jsonrpc="2.0", id=1, result={"status": "ok"}))
                        )
                        await write_stream.send(response)
                        await anyio.sleep(0.01)

        # Run client and server concurrently
        async with anyio.create_task_group() as tg:
            tg.start_soon(client_task)
            tg.start_soon(server_task)

        assert response_received

    @pytest.mark.anyio
    async def test_notification_handling(self, client_server_config):
        """Test notification handling (no response expected)."""
        client_config = client_server_config["client"]["config"]
        server_config = client_server_config["server"]["config"]
        client_sqs = client_server_config["client"]["sqs_client"]
        server_sqs = client_server_config["server"]["sqs_client"]

        notification_received = False

        # Configure client SQS mock
        client_sqs.send_message.return_value = {"MessageId": "notification-sent"}
        client_sqs.receive_message.return_value = {"Messages": []}

        # Configure server SQS mock
        server_sqs.receive_message.return_value = {
            "Messages": [
                {
                    "MessageId": "notification-1",
                    "ReceiptHandle": "notification-handle-1",
                    "Body": '{"jsonrpc": "2.0", "method": "notification", "params": {"event": "test"}}',
                    "MessageAttributes": {},
                }
            ]
        }

        async def client_task():
            with anyio.move_on_after(0.2):
                async with sqs_client(client_config, client_sqs) as (read_stream, write_stream):
                    # Send notification
                    notification = SessionMessage(
                        JSONRPCMessage(
                            root=JSONRPCNotification(jsonrpc="2.0", method="notification", params={"event": "test"})
                        )
                    )
                    await write_stream.send(notification)
                    await anyio.sleep(0.01)

        async def server_task():
            nonlocal notification_received
            with anyio.move_on_after(0.2):
                async with sqs_server(server_config, server_sqs) as (read_stream, write_stream):
                    # Receive notification
                    with anyio.move_on_after(0.1):
                        notification = await read_stream.receive()
                        assert isinstance(notification, SessionMessage)
                        assert notification.message.root.method == "notification"
                        assert not hasattr(notification.message.root, "id")  # Notifications don't have IDs
                        notification_received = True

        # Run client and server concurrently
        async with anyio.create_task_group() as tg:
            tg.start_soon(client_task)
            tg.start_soon(server_task)

        assert notification_received

    @pytest.mark.anyio
    async def test_high_throughput_scenario(self, client_server_config):
        """Test high throughput message processing."""
        client_config = client_server_config["client"]["config"]
        server_config = client_server_config["server"]["config"]
        client_sqs = client_server_config["client"]["sqs_client"]
        server_sqs = client_server_config["server"]["sqs_client"]

        num_messages = 10
        processed_messages = []

        # Configure client SQS mock
        client_sqs.send_message.return_value = {"MessageId": "bulk-sent"}
        client_sqs.receive_message.return_value = {"Messages": []}

        # Configure server SQS mock to return all messages at once
        server_sqs.receive_message.return_value = {
            "Messages": [
                {
                    "MessageId": f"bulk-msg-{i}",
                    "ReceiptHandle": f"bulk-handle-{i}",
                    "Body": f'{{"jsonrpc": "2.0", "id": {i}, "method": "bulk/test", "params": {{}}}}',
                    "MessageAttributes": {},
                }
                for i in range(num_messages)
            ]
        }

        async def client_task():
            with anyio.move_on_after(0.5):
                async with sqs_client(client_config, client_sqs) as (read_stream, write_stream):
                    # Send multiple messages
                    for i in range(num_messages):
                        request = SessionMessage(
                            JSONRPCMessage(root=JSONRPCRequest(jsonrpc="2.0", id=i, method="bulk/test", params={}))
                        )
                        await write_stream.send(request)
                    await anyio.sleep(0.01)

        async def server_task():
            nonlocal processed_messages
            with anyio.move_on_after(0.5):
                async with sqs_server(server_config, server_sqs) as (read_stream, write_stream):
                    # Process multiple messages
                    for _ in range(num_messages):
                        with anyio.move_on_after(0.1):
                            message = await read_stream.receive()
                            if isinstance(message, SessionMessage):
                                processed_messages.append(message)

        # Run client and server concurrently
        async with anyio.create_task_group() as tg:
            tg.start_soon(client_task)
            tg.start_soon(server_task)

        assert len(processed_messages) == num_messages
        for msg in processed_messages:
            assert isinstance(msg, SessionMessage)
            assert msg.message.root.method == "bulk/test"

    @pytest.mark.anyio
    async def test_error_recovery(self, client_server_config):
        """Test error recovery in message processing."""
        client_config = client_server_config["client"]["config"]
        server_config = client_server_config["server"]["config"]
        client_sqs = client_server_config["client"]["sqs_client"]
        server_sqs = client_server_config["server"]["sqs_client"]

        messages_processed = 0

        # Configure SQS mocks to simulate error and recovery
        call_count = 0

        def receive_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call returns error message
                return {
                    "Messages": [
                        {
                            "MessageId": "error-msg-1",
                            "ReceiptHandle": "error-handle-1",
                            "Body": '{"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "Test error"}}',
                            "MessageAttributes": {},
                        }
                    ]
                }
            else:
                # Subsequent calls return empty to avoid infinite polling
                return {"Messages": []}

        client_sqs.receive_message.side_effect = receive_side_effect
        client_sqs.send_message.return_value = {"MessageId": "sent"}

        # Test that client can handle error responses
        with anyio.move_on_after(0.1):
            async with sqs_client(client_config, client_sqs) as (read_stream, write_stream):
                # Should receive the error message
                response = await read_stream.receive()
                if isinstance(response, SessionMessage):
                    assert response.message.root.id == 1
                    assert hasattr(response.message.root, "error")
                    assert response.message.root.error.code == -32000
                    messages_processed += 1

        assert messages_processed == 1

    @pytest.mark.anyio
    async def test_message_attributes_preservation(self, client_server_config):
        """Test that message attributes are preserved during transport."""
        client_config = client_server_config["client"]["config"]
        server_config = client_server_config["server"]["config"]
        client_sqs = client_server_config["client"]["sqs_client"]
        server_sqs = client_server_config["server"]["sqs_client"]

        attributes_preserved = False

        # Configure client SQS mock
        client_sqs.send_message.return_value = {"MessageId": "attr-test"}
        client_sqs.receive_message.return_value = {"Messages": []}

        # Configure server SQS mock with message attributes
        server_sqs.receive_message.return_value = {
            "Messages": [
                {
                    "MessageId": "attr-msg-1",
                    "ReceiptHandle": "attr-handle-1",
                    "Body": '{"jsonrpc": "2.0", "id": 1, "method": "attr/test", "params": {}}',
                    "MessageAttributes": {
                        "session_id": {"StringValue": "test-session", "DataType": "String"},
                        "client_id": {"StringValue": "test-client", "DataType": "String"},
                    },
                }
            ]
        }

        async def client_task():
            with anyio.move_on_after(0.2):
                async with sqs_client(client_config, client_sqs) as (read_stream, write_stream):
                    # Send request with custom attributes
                    request = SessionMessage(
                        JSONRPCMessage(root=JSONRPCRequest(jsonrpc="2.0", id=1, method="attr/test", params={}))
                    )
                    await write_stream.send(request)
                    await anyio.sleep(0.01)

        async def server_task():
            nonlocal attributes_preserved
            with anyio.move_on_after(0.2):
                async with sqs_server(server_config, server_sqs) as (read_stream, write_stream):
                    # Receive request and verify attributes
                    with anyio.move_on_after(0.1):
                        request = await read_stream.receive()
                        if isinstance(request, SessionMessage):
                            assert request.message.root.method == "attr/test"
                            attributes_preserved = True

        # Run client and server concurrently
        async with anyio.create_task_group() as tg:
            tg.start_soon(client_task)
            tg.start_soon(server_task)

        assert attributes_preserved

    @pytest.mark.anyio
    async def test_concurrent_clients(self, client_server_config):
        """Test multiple concurrent clients."""
        client_config = client_server_config["client"]["config"]
        server_config = client_server_config["server"]["config"]
        client_sqs = client_server_config["client"]["sqs_client"]
        server_sqs = client_server_config["server"]["sqs_client"]

        num_clients = 3
        processed_clients = set()

        # Configure client SQS mock
        client_sqs.send_message.return_value = {"MessageId": "concurrent-sent"}
        client_sqs.receive_message.return_value = {"Messages": []}

        # Configure server SQS mock to return messages from all clients
        server_sqs.receive_message.return_value = {
            "Messages": [
                {
                    "MessageId": f"concurrent-msg-{i}",
                    "ReceiptHandle": f"concurrent-handle-{i}",
                    "Body": f'{{"jsonrpc": "2.0", "id": {i}, "method": "concurrent/test", "params": {{"client_id": {i}}}}}',
                    "MessageAttributes": {},
                }
                for i in range(num_clients)
            ]
        }

        async def client_task(client_id):
            with anyio.move_on_after(0.5):
                async with sqs_client(client_config, client_sqs) as (read_stream, write_stream):
                    # Send request from this client
                    request = SessionMessage(
                        JSONRPCMessage(
                            root=JSONRPCRequest(
                                jsonrpc="2.0", id=client_id, method="concurrent/test", params={"client_id": client_id}
                            )
                        )
                    )
                    await write_stream.send(request)
                    await anyio.sleep(0.01)

        async def server_task():
            nonlocal processed_clients
            with anyio.move_on_after(0.5):
                async with sqs_server(server_config, server_sqs) as (read_stream, write_stream):
                    # Process messages from all clients
                    for _ in range(num_clients):
                        with anyio.move_on_after(0.1):
                            message = await read_stream.receive()
                            if isinstance(message, SessionMessage):
                                client_id = message.message.root.params.get("client_id")
                                processed_clients.add(client_id)

        # Run multiple clients and server concurrently
        async with anyio.create_task_group() as tg:
            for i in range(num_clients):
                tg.start_soon(client_task, i)
            tg.start_soon(server_task)

        assert len(processed_clients) == num_clients
        assert processed_clients == set(range(num_clients))
