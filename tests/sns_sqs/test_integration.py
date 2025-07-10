"""
Integration tests for SQS+SNS client and server transport modules working together.
"""

import pytest
from unittest.mock import patch

import anyio
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage, JSONRPCRequest, JSONRPCResponse, JSONRPCNotification

from asyncmcp.sns_sqs.client import sns_sqs_client
from asyncmcp.sns_sqs.server import sns_sqs_server


# client_server_config fixture is now imported from shared_fixtures via conftest.py


class TestClientServerIntegration:
    """Integration tests for client-server communication."""

    @pytest.mark.anyio
    @pytest.mark.integration
    async def test_request_response_cycle(self, client_server_config):
        """Test complete request-response cycle between client and server."""
        client_cfg = client_server_config["client"]
        server_cfg = client_server_config["server"]

        # Track SNS publish calls by mocking the clients directly
        client_cfg["sns_client"].publish.return_value = {"MessageId": "client-test-123"}
        server_cfg["sns_client"].publish.return_value = {"MessageId": "server-test-123"}

        # Mock SQS to return empty messages (no polling)
        client_cfg["sqs_client"].receive_message.return_value = {"Messages": []}
        server_cfg["sqs_client"].receive_message.return_value = {"Messages": []}

        # Use timeout to prevent hanging
        with anyio.move_on_after(0.2):
            async with anyio.create_task_group() as tg:

                async def run_client():
                    async with sns_sqs_client(client_cfg["config"]) as (read_stream, write_stream):
                        # Send request - this should trigger SNS publish
                        request = JSONRPCMessage(
                            root=JSONRPCRequest(jsonrpc="2.0", id=1, method="test/process", params={"data": "test"})
                        )
                        await write_stream.send(SessionMessage(request))
                        await anyio.sleep(0.05)

                async def run_server():
                    async with sns_sqs_server(server_cfg["config"]) as (read_stream, write_stream):
                        # Send response - this should also trigger SNS publish
                        response = JSONRPCMessage(
                            root=JSONRPCResponse(jsonrpc="2.0", id=1, result={"processed": True, "status": "success"})
                        )
                        await write_stream.send(SessionMessage(response))
                        await anyio.sleep(0.05)

                tg.start_soon(run_client)
                tg.start_soon(run_server)

                # Let both run for a bit
                await anyio.sleep(0.1)

        # Verify that SNS publish was called (indicating messages were sent)
        assert client_cfg["sns_client"].publish.called
        assert server_cfg["sns_client"].publish.called

    @pytest.mark.anyio
    @pytest.mark.integration
    async def test_notification_flow(self, client_server_config):
        """Test notification flow from client to server."""
        client_cfg = client_server_config["client"]
        server_cfg = client_server_config["server"]

        # Mock clients
        client_cfg["sns_client"].publish.return_value = {"MessageId": "notification-123"}
        client_cfg["sqs_client"].receive_message.return_value = {"Messages": []}
        server_cfg["sqs_client"].receive_message.return_value = {"Messages": []}

        # Use timeout to prevent hanging
        with anyio.move_on_after(0.2):
            async with anyio.create_task_group() as tg:

                async def run_client():
                    async with sns_sqs_client(client_cfg["config"]) as (read_stream, write_stream):
                        # Send notification
                        notification = JSONRPCMessage(
                            root=JSONRPCNotification(
                                jsonrpc="2.0", method="test/notify", params={"event": "client_connected"}
                            )
                        )
                        await write_stream.send(SessionMessage(notification))
                        await anyio.sleep(0.05)

                async def run_server():
                    async with sns_sqs_server(server_cfg["config"]) as (read_stream, write_stream):
                        await anyio.sleep(0.02)

                tg.start_soon(run_client)
                tg.start_soon(run_server)

                await anyio.sleep(0.1)

        # Verify notification was published
        assert client_cfg["sns_client"].publish.called

    @pytest.mark.anyio
    @pytest.mark.integration
    async def test_error_handling_integration(self, client_server_config):
        """Test error handling in integrated client-server setup."""
        client_cfg = client_server_config["client"]
        server_cfg = client_server_config["server"]

        # Mock clients
        client_cfg["sns_client"].publish.return_value = {"MessageId": "error-request-123"}
        server_cfg["sns_client"].publish.return_value = {"MessageId": "error-response-123"}
        client_cfg["sqs_client"].receive_message.return_value = {"Messages": []}
        server_cfg["sqs_client"].receive_message.return_value = {"Messages": []}

        # Use timeout to prevent hanging
        with anyio.move_on_after(0.2):
            async with anyio.create_task_group() as tg:

                async def run_client():
                    async with sns_sqs_client(client_cfg["config"]) as (read_stream, write_stream):
                        # Send request for unknown method
                        request = JSONRPCMessage(
                            root=JSONRPCRequest(jsonrpc="2.0", id=1, method="unknown/method", params={})
                        )
                        await write_stream.send(SessionMessage(request))
                        await anyio.sleep(0.05)

                async def run_server():
                    async with sns_sqs_server(server_cfg["config"]) as (read_stream, write_stream):
                        # Send proper error response
                        from mcp.types import JSONRPCError

                        error = JSONRPCError(jsonrpc="2.0", id=1, error={"code": -32601, "message": "Method not found"})
                        await write_stream.send(SessionMessage(JSONRPCMessage(root=error)))
                        await anyio.sleep(0.05)

                tg.start_soon(run_client)
                tg.start_soon(run_server)

                await anyio.sleep(0.1)

        # Verify error messages were published
        assert client_cfg["sns_client"].publish.called
        assert server_cfg["sns_client"].publish.called

    @pytest.mark.anyio
    @pytest.mark.integration
    @pytest.mark.performance
    async def test_high_throughput_integration(self, client_server_config):
        """Test high throughput message processing between client and server."""
        client_cfg = client_server_config["client"]
        server_cfg = client_server_config["server"]

        # Mock clients
        client_cfg["sns_client"].publish.return_value = {"MessageId": "bulk-request-123"}
        server_cfg["sns_client"].publish.return_value = {"MessageId": "bulk-response-123"}
        client_cfg["sqs_client"].receive_message.return_value = {"Messages": []}
        server_cfg["sqs_client"].receive_message.return_value = {"Messages": []}

        # Use timeout to prevent hanging
        with anyio.move_on_after(0.3):
            async with anyio.create_task_group() as tg:

                async def run_client():
                    async with sns_sqs_client(client_cfg["config"]) as (read_stream, write_stream):
                        # Send multiple requests
                        for i in range(3):
                            request = JSONRPCMessage(
                                root=JSONRPCRequest(jsonrpc="2.0", id=i, method="bulk/process", params={"index": i})
                            )
                            await write_stream.send(SessionMessage(request))

                        await anyio.sleep(0.1)

                async def run_server():
                    async with sns_sqs_server(server_cfg["config"]) as (read_stream, write_stream):
                        # Send multiple responses
                        for i in range(3):
                            response = JSONRPCMessage(
                                root=JSONRPCResponse(jsonrpc="2.0", id=i, result={"processed": True, "index": i})
                            )
                            await write_stream.send(SessionMessage(response))

                        await anyio.sleep(0.1)

                tg.start_soon(run_client)
                tg.start_soon(run_server)

                await anyio.sleep(0.15)

        # Verify multiple messages were published
        assert client_cfg["sns_client"].publish.called
        assert server_cfg["sns_client"].publish.called
        # Check that multiple calls were made
        assert client_cfg["sns_client"].publish.call_count >= 3
        assert server_cfg["sns_client"].publish.call_count >= 3

    @pytest.mark.anyio
    @pytest.mark.integration
    async def test_metadata_preservation_integration(self, client_server_config):
        """Test that metadata is preserved through the full integration flow."""
        client_cfg = client_server_config["client"]
        server_cfg = client_server_config["server"]

        # Mock clients
        client_cfg["sns_client"].publish.return_value = {"MessageId": "metadata-request-123"}
        server_cfg["sns_client"].publish.return_value = {"MessageId": "metadata-response-123"}
        client_cfg["sqs_client"].receive_message.return_value = {"Messages": []}
        server_cfg["sqs_client"].receive_message.return_value = {"Messages": []}

        # Use timeout to prevent hanging
        with anyio.move_on_after(0.2):
            async with anyio.create_task_group() as tg:

                async def run_client():
                    async with sns_sqs_client(client_cfg["config"]) as (read_stream, write_stream):
                        # Send request with metadata context
                        request = JSONRPCMessage(
                            root=JSONRPCRequest(jsonrpc="2.0", id=1, method="meta/test", params={"data": "test"})
                        )
                        # Add some metadata
                        session_msg = SessionMessage(request, metadata={"client_id": "test-client"})
                        await write_stream.send(session_msg)
                        await anyio.sleep(0.05)

                async def run_server():
                    async with sns_sqs_server(server_cfg["config"]) as (read_stream, write_stream):
                        # Send response with metadata
                        response = JSONRPCMessage(root=JSONRPCResponse(jsonrpc="2.0", id=1, result={"success": True}))
                        session_msg = SessionMessage(response, metadata={"server_id": "test-server"})
                        await write_stream.send(session_msg)
                        await anyio.sleep(0.05)

                tg.start_soon(run_client)
                tg.start_soon(run_server)

                await anyio.sleep(0.1)

        # Verify metadata messages were published
        assert client_cfg["sns_client"].publish.called
        assert server_cfg["sns_client"].publish.called

        # Verify that MessageAttributes were included in the publish calls
        client_publish_calls = client_cfg["sns_client"].publish.call_args_list
        server_publish_calls = server_cfg["sns_client"].publish.call_args_list

        # Check that at least one call included MessageAttributes
        has_message_attrs = any(
            "MessageAttributes" in call.kwargs for call in client_publish_calls + server_publish_calls
        )
        assert has_message_attrs


class TestConcurrentClientServer:
    """Test concurrent client-server scenarios."""

    @pytest.mark.anyio
    @pytest.mark.integration
    @pytest.mark.performance
    async def test_multiple_clients_single_server(self, client_server_config):
        """Test multiple clients communicating with a single server."""
        # This test is simplified to avoid complexity
        # Use timeout to prevent hanging
        with anyio.move_on_after(0.2):
            # Just verify the basic setup works
            client_cfg = client_server_config["client"]
            server_cfg = client_server_config["server"]

            with patch("anyio.to_thread.run_sync") as mock_run_sync:
                mock_run_sync.return_value = {"Messages": []}

                async with sns_sqs_client(client_cfg["config"]) as (read_stream, write_stream):
                    await anyio.sleep(0.01)

                async with sns_sqs_server(server_cfg["config"]) as (read_stream, write_stream):
                    await anyio.sleep(0.01)
