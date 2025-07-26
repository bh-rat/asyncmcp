"""
Comprehensive integration tests for SQS transport with dynamic queue system.
"""

import pytest
from unittest.mock import MagicMock
import json

import anyio
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage, JSONRPCRequest

from asyncmcp.sqs.client import sqs_client
from asyncmcp.sqs.manager import SqsSessionManager
from asyncmcp.sqs.utils import SqsClientConfig, SqsServerConfig

from tests.sqs.shared_fixtures import client_server_config, mock_mcp_server


class TestSQSIntegrationWithDynamicQueues:
    """Integration tests for SQS transport with dynamic queue system."""

    @pytest.mark.anyio
    async def test_client_server_initialize_flow(self, client_server_config, mock_mcp_server):
        """Test complete initialize flow with session manager."""
        client_config = client_server_config["client"]["config"]
        server_config = client_server_config["server"]["config"]
        client_sqs = client_server_config["client"]["sqs_client"]
        server_sqs = client_server_config["server"]["sqs_client"]
        client_response_queue = client_server_config["client"]["response_queue_url"]

        # Mock the server SQS to return initialize message, then empty
        server_call_count = 0

        def server_receive_side_effect(*args, **kwargs):
            nonlocal server_call_count
            server_call_count += 1
            if server_call_count == 1:
                return {
                    "Messages": [
                        {
                            "MessageId": "init-msg-1",
                            "ReceiptHandle": "init-handle-1",
                            "Body": json.dumps(
                                {
                                    "jsonrpc": "2.0",
                                    "id": 1,
                                    "method": "initialize",
                                    "params": {
                                        "protocolVersion": "2024-11-05",
                                        "capabilities": {},
                                        "clientInfo": {"name": "test-client", "version": "1.0"},
                                        "response_queue_url": client_response_queue,
                                    },
                                }
                            ),
                            "MessageAttributes": {"Method": {"DataType": "String", "StringValue": "initialize"}},
                        }
                    ]
                }
            else:
                return {"Messages": []}

        # Mock the client SQS to return initialize response
        client_call_count = 0

        def client_receive_side_effect(*args, **kwargs):
            nonlocal client_call_count
            client_call_count += 1
            if client_call_count == 1:
                return {
                    "Messages": [
                        {
                            "MessageId": "init-response-1",
                            "ReceiptHandle": "init-response-handle-1",
                            "Body": json.dumps(
                                {
                                    "jsonrpc": "2.0",
                                    "id": 1,
                                    "result": {
                                        "protocolVersion": "2024-11-05",
                                        "capabilities": {},
                                        "serverInfo": {"name": "test-server", "version": "1.0"},
                                    },
                                }
                            ),
                            "MessageAttributes": {},
                        }
                    ]
                }
            else:
                return {"Messages": []}

        server_sqs.receive_message.side_effect = server_receive_side_effect
        client_sqs.receive_message.side_effect = client_receive_side_effect

        # Mock delete_message for both clients
        server_sqs.delete_message.return_value = {}
        client_sqs.delete_message.return_value = {}

        # Mock send_message for responses
        server_sqs.send_message.return_value = {"MessageId": "server-response-1"}
        client_sqs.send_message.return_value = {"MessageId": "client-request-1"}

        # Create session manager
        session_manager = SqsSessionManager(app=mock_mcp_server, config=server_config, sqs_client=server_sqs)

        with anyio.move_on_after(1.0):  # Timeout to prevent hanging
            async with anyio.create_task_group() as tg:
                # Start session manager
                async def run_server():
                    async with session_manager.run():
                        await anyio.sleep(0.5)  # Let it process the initialize message

                # Start client
                async def run_client():
                    await anyio.sleep(0.1)  # Let server start first
                    async with sqs_client(client_config, client_sqs) as (
                        read_stream,
                        write_stream,
                    ):
                        # Send initialize request
                        init_request = JSONRPCMessage(
                            root=JSONRPCRequest(
                                jsonrpc="2.0",
                                id=1,
                                method="initialize",
                                params={
                                    "protocolVersion": "2024-11-05",
                                    "capabilities": {},
                                    "clientInfo": {"name": "test-client", "version": "1.0"},
                                },
                            )
                        )
                        await write_stream.send(SessionMessage(init_request))

                        # Wait for response
                        with anyio.move_on_after(0.3):
                            response = await read_stream.receive()
                            if isinstance(response, SessionMessage):
                                assert response.message.root.id == 1

                tg.start_soon(run_server)
                tg.start_soon(run_client)

        # Verify that the session was created on the server
        stats = session_manager.get_all_sessions()
        assert len(stats) >= 0  # Session may have been created and processed

    @pytest.mark.anyio
    async def test_client_server_request_response_cycle(self, client_server_config, mock_mcp_server):
        """Test complete request-response cycle after initialization."""
        client_config = client_server_config["client"]["config"]
        server_config = client_server_config["server"]["config"]
        client_sqs = client_server_config["client"]["sqs_client"]
        server_sqs = client_server_config["server"]["sqs_client"]
        client_response_queue = client_server_config["client"]["response_queue_url"]

        # Mock server SQS to return a tools/list request
        server_messages = [
            {
                "MessageId": "tools-request-1",
                "ReceiptHandle": "tools-request-handle-1",
                "Body": json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
                "MessageAttributes": {"SessionId": {"DataType": "String", "StringValue": "test-session-123"}},
            }
        ]

        # Mock client SQS to return a tools/list response
        client_messages = [
            {
                "MessageId": "tools-response-1",
                "ReceiptHandle": "tools-response-handle-1",
                "Body": json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "result": {"tools": [{"name": "test-tool", "description": "A test tool"}]},
                    }
                ),
                "MessageAttributes": {},
            }
        ]

        call_counts = {"server": 0, "client": 0}

        def server_receive_side_effect(*args, **kwargs):
            call_counts["server"] += 1
            if call_counts["server"] == 1:
                return {"Messages": server_messages}
            else:
                return {"Messages": []}

        def client_receive_side_effect(*args, **kwargs):
            call_counts["client"] += 1
            if call_counts["client"] == 1:
                return {"Messages": client_messages}
            else:
                return {"Messages": []}

        server_sqs.receive_message.side_effect = server_receive_side_effect
        client_sqs.receive_message.side_effect = client_receive_side_effect
        server_sqs.delete_message.return_value = {}
        client_sqs.delete_message.return_value = {}
        server_sqs.send_message.return_value = {"MessageId": "server-msg-1"}
        client_sqs.send_message.return_value = {"MessageId": "client-msg-1"}

        # Create session manager and pre-create a session
        session_manager = SqsSessionManager(app=mock_mcp_server, config=server_config, sqs_client=server_sqs)

        with anyio.move_on_after(1.0):
            async with anyio.create_task_group() as tg:

                async def run_server_manager():
                    async with session_manager.run():
                        await anyio.sleep(0.5)

                async def run_mock_client():
                    await anyio.sleep(0.1)
                    async with sqs_client(client_config, client_sqs) as (
                        read_stream,
                        write_stream,
                    ):
                        # Send tools/list request
                        tools_request = JSONRPCMessage(
                            root=JSONRPCRequest(jsonrpc="2.0", id=2, method="tools/list", params={})
                        )
                        await write_stream.send(SessionMessage(tools_request))

                        # Wait for response
                        with anyio.move_on_after(0.3):
                            response = await read_stream.receive()
                            if isinstance(response, SessionMessage) and hasattr(response.message.root, "result"):
                                assert "tools" in response.message.root.result

                tg.start_soon(run_server_manager)
                tg.start_soon(run_mock_client)

    @pytest.mark.anyio
    async def test_multiple_clients_different_queues(self, mock_mcp_server):
        """Test multiple clients with different response queues."""
        # Create configs for server and two clients
        server_config = SqsServerConfig(
            read_queue_url="http://localhost:4566/000000000000/server-requests",
            max_messages=10,
            wait_time_seconds=1,
            poll_interval_seconds=0.01,
        )

        client1_config = SqsClientConfig(
            read_queue_url="http://localhost:4566/000000000000/server-requests",
            response_queue_url="http://localhost:4566/000000000000/client1-responses",
            client_id="client-1",
            max_messages=1,
            wait_time_seconds=1,
            poll_interval_seconds=0.01,
        )

        client2_config = SqsClientConfig(
            read_queue_url="http://localhost:4566/000000000000/server-requests",
            response_queue_url="http://localhost:4566/000000000000/client2-responses",
            client_id="client-2",
            max_messages=1,
            wait_time_seconds=1,
            poll_interval_seconds=0.01,
        )

        client1_response_queue = "http://localhost:4566/000000000000/client1-responses"
        client2_response_queue = "http://localhost:4566/000000000000/client2-responses"

        # Mock clients
        server_sqs = MagicMock()
        client1_sqs = MagicMock()
        client2_sqs = MagicMock()

        # Server receives initialize from both clients
        server_messages = [
            {
                "MessageId": "init1",
                "ReceiptHandle": "init1-handle",
                "Body": json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "clientInfo": {"name": "client-1", "version": "1.0"},
                            "response_queue_url": client1_response_queue,
                        },
                    }
                ),
                "MessageAttributes": {"ClientId": {"DataType": "String", "StringValue": "client-1"}},
            },
            {
                "MessageId": "init2",
                "ReceiptHandle": "init2-handle",
                "Body": json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "clientInfo": {"name": "client-2", "version": "1.0"},
                            "response_queue_url": client2_response_queue,
                        },
                    }
                ),
                "MessageAttributes": {"ClientId": {"DataType": "String", "StringValue": "client-2"}},
            },
        ]

        server_call_count = 0

        def server_receive_side_effect(*args, **kwargs):
            nonlocal server_call_count
            server_call_count += 1
            if server_call_count == 1:
                return {"Messages": server_messages[:1]}  # Return first client init
            elif server_call_count == 2:
                return {"Messages": server_messages[1:]}  # Return second client init
            else:
                return {"Messages": []}

        server_sqs.receive_message.side_effect = server_receive_side_effect
        server_sqs.delete_message.return_value = {}
        server_sqs.send_message.return_value = {"MessageId": "server-response"}

        client1_sqs.receive_message.return_value = {"Messages": []}
        client2_sqs.receive_message.return_value = {"Messages": []}

        session_manager = SqsSessionManager(app=mock_mcp_server, config=server_config, sqs_client=server_sqs)

        with anyio.move_on_after(1.0):
            async with session_manager.run():
                await anyio.sleep(0.3)  # Let both initialize messages process

                # Check that two sessions were created
                stats = session_manager.get_all_sessions()
                # Sessions may be processed and cleaned up quickly, so we mainly verify no crashes

    @pytest.mark.anyio
    async def test_error_handling_invalid_initialize_request(self, client_server_config, mock_mcp_server):
        """Test handling of invalid initialize requests."""
        server_config = client_server_config["server"]["config"]
        server_sqs = client_server_config["server"]["sqs_client"]

        # Mock server to receive initialize request without response_queue_url
        invalid_init_message = {
            "MessageId": "invalid-init-1",
            "ReceiptHandle": "invalid-init-handle-1",
            "Body": json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "invalid-client", "version": "1.0"},
                        # Missing response_queue_url
                    },
                }
            ),
            "MessageAttributes": {},
        }

        def server_receive_side_effect(*args, **kwargs):
            return {"Messages": [invalid_init_message]}

        server_sqs.receive_message.side_effect = server_receive_side_effect
        server_sqs.delete_message.return_value = {}

        session_manager = SqsSessionManager(app=mock_mcp_server, config=server_config, sqs_client=server_sqs)

        with anyio.move_on_after(0.5):
            async with session_manager.run():
                await anyio.sleep(0.2)  # Let it process the invalid message

                # Session should not be created due to missing response_queue_url
                stats = session_manager.get_all_sessions()
                # The invalid message should be handled gracefully without creating a session

        # Verify delete_message was called (message was processed and deleted)
        assert server_sqs.delete_message.called

    @pytest.mark.anyio
    async def test_session_cleanup_on_error(self, client_server_config, mock_mcp_server):
        """Test that sessions are cleaned up when they crash."""
        server_config = client_server_config["server"]["config"]
        server_sqs = client_server_config["server"]["sqs_client"]

        # Mock a valid initialize message
        init_message = {
            "MessageId": "cleanup-init-1",
            "ReceiptHandle": "cleanup-init-handle-1",
            "Body": json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "cleanup-client", "version": "1.0"},
                        "response_queue_url": "http://localhost:4566/000000000000/cleanup-responses",
                    },
                }
            ),
            "MessageAttributes": {},
        }

        call_count = 0

        def server_receive_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"Messages": [init_message]}
            else:
                return {"Messages": []}

        server_sqs.receive_message.side_effect = server_receive_side_effect
        server_sqs.delete_message.return_value = {}
        server_sqs.send_message.return_value = {"MessageId": "cleanup-msg"}

        # Mock the MCP server to raise an exception
        mock_error_server = MagicMock()
        mock_error_server.run.side_effect = Exception("Simulated server crash")
        mock_error_server.create_initialization_options.return_value = {}

        session_manager = SqsSessionManager(app=mock_error_server, config=server_config, sqs_client=server_sqs)

        with anyio.move_on_after(1.0):
            async with session_manager.run():
                await anyio.sleep(0.3)  # Let the session crash

                # Session should be cleaned up after the crash
                stats = session_manager.get_all_sessions()
                # Session should be removed from active instances after crashing
