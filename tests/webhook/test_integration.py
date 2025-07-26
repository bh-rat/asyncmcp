"""
Comprehensive integration tests for webhook transport.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import httpx
import orjson
import pytest
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage, JSONRPCRequest, JSONRPCResponse
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import Response

from asyncmcp.webhook.client import webhook_client
from asyncmcp.webhook.manager import WebhookSessionManager
from asyncmcp.webhook.server import WebhookTransport
from asyncmcp.webhook.utils import SessionInfo, WebhookClientConfig, WebhookServerConfig


class TestWebhookIntegration:
    """Integration tests for webhook transport."""

    @pytest.mark.anyio
    async def test_client_server_initialize_flow(self, client_server_config, mock_mcp_server):
        """Test complete initialize flow with session manager."""
        client_config = client_server_config["client"]["config"]
        server_config = client_server_config["server"]["config"]
        client_http = client_server_config["client"]["http_client"]
        server_http = client_server_config["server"]["http_client"]

        # Mock successful HTTP responses
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"status": "success"}'
        client_http.post.return_value = mock_response
        server_http.post.return_value = mock_response

        # Create session manager
        session_manager = WebhookSessionManager(app=mock_mcp_server, config=server_config, stateless=False)

        # Mock the webhook session manager's HTTP server to handle requests
        received_requests = []

        async def mock_handle_request(session_message, client_id, session_id):
            received_requests.append({"message": session_message, "client_id": client_id, "session_id": session_id})
            # Return a mock response
            return Response(
                content=orjson.dumps({"status": "session_created", "session_id": "test-session-123"}),
                media_type="application/json",
                status_code=200,
            )

        with anyio.move_on_after(1.0):  # Timeout to prevent hanging
            # Test with mocked components instead of actual HTTP servers
            with patch.object(session_manager, "_handle_initialize_request", side_effect=mock_handle_request):
                async with anyio.create_task_group() as tg:

                    async def run_mock_client():
                        # Use refactored webhook client (no start_webhook_server to mock)
                        webhook_path = "/webhook/response"
                        async with webhook_client(client_config, webhook_path) as (read_stream, write_stream, client):
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
                                        "_meta": {"webhookUrl": "http://localhost:8001/webhook/response"},
                                    },
                                )
                            )
                            await write_stream.send(SessionMessage(init_request))

                            # Give some time for processing
                            await anyio.sleep(0.1)

                    tg.start_soon(run_mock_client)
                    await anyio.sleep(0.2)  # Let the test complete

    @pytest.mark.anyio
    async def test_client_server_request_response_cycle(self, client_server_config, mock_mcp_server):
        """Test complete request-response cycle after initialization."""
        client_config = client_server_config["client"]["config"]
        server_config = client_server_config["server"]["config"]
        client_http = client_server_config["client"]["http_client"]
        server_http = client_server_config["server"]["http_client"]

        # Mock HTTP responses
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.status_code = 200
        client_http.post.return_value = mock_response
        server_http.post.return_value = mock_response

        # Create session manager
        session_manager = WebhookSessionManager(app=mock_mcp_server, config=server_config, stateless=False)

        # Track messages
        processed_messages = []

        # Mock the MCP server to return a tools/list response
        mock_mcp_server.run = AsyncMock()

        async def mock_server_run(read_stream, write_stream, options, stateless=False):
            # Read from the stream and send a response
            try:
                async for message in read_stream:
                    if isinstance(message, SessionMessage):
                        processed_messages.append(message)
                        # Send a mock response back
                        response = JSONRPCMessage(
                            root=JSONRPCResponse(
                                jsonrpc="2.0",
                                id=message.message.root.id,
                                result={"tools": [{"name": "test-tool", "description": "A test tool"}]},
                            )
                        )
                        await write_stream.send(SessionMessage(response))
                        break
            except anyio.EndOfStream:
                pass

        mock_mcp_server.run.side_effect = mock_server_run

        with anyio.move_on_after(1.0):
            async with anyio.create_task_group() as tg:

                async def run_mock_client():
                    await anyio.sleep(0.1)  # Let server start first

                    # Use refactored webhook client (no start_webhook_server to mock)
                    webhook_path = "/webhook/response"
                    async with webhook_client(client_config, webhook_path) as (read_stream, write_stream, client):
                        # Send tools/list request
                        tools_request = JSONRPCMessage(
                            root=JSONRPCRequest(jsonrpc="2.0", id=2, method="tools/list", params={})
                        )
                        await write_stream.send(SessionMessage(tools_request))

                        # Wait for response (with timeout)
                        with anyio.move_on_after(0.3):
                            response = await read_stream.receive()
                            if isinstance(response, SessionMessage) and hasattr(response.message.root, "result"):
                                assert "tools" in response.message.root.result

                async def run_mock_session_manager():
                    # Mock the session manager components
                    # No longer need to patch _start_http_server as it doesn't exist
                    with patch.object(session_manager, "_event_driven_message_sender"):
                        async with session_manager.run():
                            await anyio.sleep(0.5)

                tg.start_soon(run_mock_session_manager)
                tg.start_soon(run_mock_client)

    @pytest.mark.anyio
    async def test_multiple_clients_different_webhooks(self, mock_mcp_server):
        """Test multiple clients with different webhook URLs."""
        # Create configs for server and two clients
        server_config = WebhookServerConfig(
            timeout_seconds=5.0,
        )

        client1_config = WebhookClientConfig(
            server_url="http://localhost:8000/mcp/request",
            client_id="client-1",
            timeout_seconds=5.0,
        )

        client2_config = WebhookClientConfig(
            server_url="http://localhost:8000/mcp/request",
            client_id="client-2",
            timeout_seconds=5.0,
        )

        # Mock HTTP clients
        server_http = AsyncMock()
        client1_http = AsyncMock()
        client2_http = AsyncMock()

        # Mock responses
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.status_code = 200
        server_http.post.return_value = mock_response
        client1_http.post.return_value = mock_response
        client2_http.post.return_value = mock_response

        # Create session manager
        session_manager = WebhookSessionManager(app=mock_mcp_server, config=server_config, stateless=False)

        # Track sessions created
        sessions_created = []

        async def mock_handle_init(session_message, client_id, session_id):
            sessions_created.append({"client_id": client_id, "session_id": session_id})
            return Response(
                content=orjson.dumps({"status": "session_created", "session_id": f"session-{client_id}"}),
                media_type="application/json",
                status_code=200,
            )

        with anyio.move_on_after(1.0):
            async with anyio.create_task_group() as tg:

                async def run_client(client_config, client_id):
                    await anyio.sleep(0.1)  # Stagger clients

                    # Use refactored webhook client (no start_webhook_server to mock)
                    webhook_path = "/webhook/response"
                    async with webhook_client(client_config, webhook_path) as (read_stream, write_stream, client):
                        # Send initialize request
                        init_request = JSONRPCMessage(
                            root=JSONRPCRequest(
                                jsonrpc="2.0",
                                id=1,
                                method="initialize",
                                params={
                                    "protocolVersion": "2024-11-05",
                                    "capabilities": {},
                                    "clientInfo": {"name": client_id, "version": "1.0"},
                                    "_meta": {
                                        "webhookUrl": f"http://localhost:{8001 if client_id == 'client-1' else 8002}"
                                        f"/webhook/response"
                                    },
                                },
                            )
                        )
                        await write_stream.send(SessionMessage(init_request))
                        await anyio.sleep(0.1)

                async def run_mock_session_manager():
                    session_manager.http_client = server_http

                    with patch.object(session_manager, "_handle_initialize_request", side_effect=mock_handle_init):
                        # No longer need to patch _start_http_server as it doesn't exist
                        with patch.object(session_manager, "_event_driven_message_sender"):
                            async with session_manager.run():
                                await anyio.sleep(0.5)

                tg.start_soon(run_mock_session_manager)
                tg.start_soon(run_client, client1_config, "client-1")
                tg.start_soon(run_client, client2_config, "client-2")

    @pytest.mark.anyio
    async def test_client_disconnect_cleanup(self, client_server_config, mock_mcp_server):
        """Test proper cleanup when client disconnects."""
        server_config = client_server_config["server"]["config"]
        server_http = client_server_config["server"]["http_client"]

        # Mock responses
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.status_code = 200
        server_http.post.return_value = mock_response

        # Create session manager
        session_manager = WebhookSessionManager(app=mock_mcp_server, config=server_config, stateless=False)

        # Pre-create a session to test cleanup
        session_id = "test-session-123"
        session_info = SessionInfo(
            session_id=session_id,
            client_id="test-client",
            webhook_url="http://localhost:8001/webhook/response",
            state="initialized",
        )

        transport = WebhookTransport(
            config=server_config,
            http_client=server_http,
            session_id=session_id,
            webhook_url="http://localhost:8001/webhook/response",
        )

        session_manager._sessions[session_id] = session_info
        session_manager._transport_instances[session_id] = transport
        session_manager._client_sessions["test-client"] = session_id

        # Verify session exists
        assert len(session_manager._sessions) == 1
        assert len(session_manager._transport_instances) == 1

        # Terminate session (simulating client disconnect)
        result = await session_manager.terminate_session(session_id)

        # Verify cleanup
        assert result is True
        assert len(session_manager._sessions) == 0
        assert len(session_manager._transport_instances) == 0
        assert len(session_manager._client_sessions) == 0

    @pytest.mark.anyio
    async def test_malformed_request_handling(self, client_server_config, mock_mcp_server):
        """Test handling of malformed requests."""
        server_config = client_server_config["server"]["config"]
        session_manager = WebhookSessionManager(app=mock_mcp_server, config=server_config, stateless=False)

        # Mock a malformed HTTP request
        mock_request = MagicMock(spec=Request)
        mock_request.method = "POST"
        mock_request.body = AsyncMock(return_value=b"invalid json")
        mock_request.headers = Headers({"X-Client-ID": "test-client"})

        response = await session_manager._handle_client_request(mock_request)

        # Should return parse error response (400 Bad Request for malformed JSON)
        assert response.status_code == 400
        response_body = json.loads(response.body.decode())
        # Check JSON-RPC error response structure
        assert "error" in response_body
        assert "message" in response_body["error"]

    @pytest.mark.anyio
    async def test_missing_client_id_header(self, client_server_config, mock_mcp_server):
        """Test handling of requests missing client ID header."""
        server_config = client_server_config["server"]["config"]
        session_manager = WebhookSessionManager(app=mock_mcp_server, config=server_config, stateless=False)

        # Mock request without client ID
        mock_request = MagicMock(spec=Request)
        mock_request.method = "POST"
        mock_request.body = AsyncMock(
            return_value=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "test/method", "params": {}}).encode()
        )
        mock_request.headers = Headers({})  # No X-Client-ID header

        response = await session_manager._handle_client_request(mock_request)

        # Should return error for missing client ID
        assert response.status_code == 400
        response_body = json.loads(response.body.decode())
        # Check JSON-RPC error response structure
        assert "error" in response_body
        assert "message" in response_body["error"]
        assert "Missing X-Client-ID header" in response_body["error"]["message"]

    @pytest.mark.anyio
    async def test_utf8_decoding_error(self, client_server_config, mock_mcp_server):
        """Test handling of invalid UTF-8 encoding in request body."""
        server_config = client_server_config["server"]["config"]
        session_manager = WebhookSessionManager(app=mock_mcp_server, config=server_config, stateless=False)

        # Mock request with invalid UTF-8 bytes
        mock_request = MagicMock(spec=Request)
        mock_request.method = "POST"
        mock_request.body = AsyncMock(return_value=b"\xff\xfe\x00\x00invalid utf8")
        mock_request.headers = Headers({"X-Client-ID": "test-client"})

        response = await session_manager._handle_client_request(mock_request)

        # Should return parse error for invalid UTF-8
        assert response.status_code == 400
        response_body = json.loads(response.body.decode())
        assert "error" in response_body
        assert "Invalid UTF-8 encoding" in response_body["error"]["message"]

    @pytest.mark.anyio
    async def test_session_termination_check_before_validation(self, client_server_config, mock_mcp_server):
        """Test that session termination check happens before other validations."""
        server_config = client_server_config["server"]["config"]
        session_manager = WebhookSessionManager(app=mock_mcp_server, config=server_config, stateless=False)

        # Create a terminated session
        session_id = "terminated-session"
        transport = WebhookTransport(
            config=server_config,
            http_client=AsyncMock(),
            session_id=session_id,
            webhook_url="http://localhost:8001/webhook/response",
        )
        await transport.terminate()
        session_manager._transport_instances[session_id] = transport

        # Mock request with terminated session ID but missing other headers
        mock_request = MagicMock(spec=Request)
        mock_request.method = "POST"
        mock_request.body = AsyncMock(
            return_value=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "test/method", "params": {}}).encode()
        )
        mock_request.headers = Headers({"X-Session-ID": session_id})  # No X-Client-ID header

        response = await session_manager._handle_client_request(mock_request)

        # Should return session terminated error (404) rather than missing client ID error (400)
        assert response.status_code == 404
        response_body = json.loads(response.body.decode())
        assert "error" in response_body
        assert "Session has been terminated" in response_body["error"]["message"]

    @pytest.mark.anyio
    async def test_invalid_protocol_version(self, client_server_config, mock_mcp_server):
        """Test handling of invalid protocol version."""
        server_config = client_server_config["server"]["config"]
        session_manager = WebhookSessionManager(app=mock_mcp_server, config=server_config, stateless=False)

        # Mock request with invalid protocol version
        mock_request = MagicMock(spec=Request)
        mock_request.method = "POST"
        mock_request.body = AsyncMock(
            return_value=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "test/method", "params": {}}).encode()
        )
        mock_request.headers = Headers(
            {
                "X-Client-ID": "test-client",
                "X-Protocol-Version": "1.5",  # Invalid version
            }
        )

        response = await session_manager._handle_client_request(mock_request)

        # Should return protocol version error
        assert response.status_code == 400
        response_body = json.loads(response.body.decode())
        assert "error" in response_body
        assert "Unsupported protocol version: 1.5" in response_body["error"]["message"]
        assert "Supported versions:" in response_body["error"]["message"]

    @pytest.mark.anyio
    async def test_invalid_session_id_format(self, client_server_config, mock_mcp_server):
        """Test handling of invalid session ID format."""
        server_config = client_server_config["server"]["config"]
        session_manager = WebhookSessionManager(app=mock_mcp_server, config=server_config, stateless=False)

        # Mock request with invalid session ID format
        mock_request = MagicMock(spec=Request)
        mock_request.method = "POST"
        mock_request.body = AsyncMock(
            return_value=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "test/method", "params": {}}).encode()
        )
        mock_request.headers = Headers(
            {
                "X-Client-ID": "test-client",
                "X-Session-ID": "invalid session id",  # Contains spaces
            }
        )

        response = await session_manager._handle_client_request(mock_request)

        # Should return session ID validation error
        assert response.status_code == 400
        response_body = json.loads(response.body.decode())
        assert "error" in response_body
        assert "Invalid session ID format" in response_body["error"]["message"]

    @pytest.mark.anyio
    async def test_method_not_allowed(self, client_server_config, mock_mcp_server):
        """Test handling of unsupported HTTP methods."""
        server_config = client_server_config["server"]["config"]
        session_manager = WebhookSessionManager(app=mock_mcp_server, config=server_config, stateless=False)

        # Mock request with unsupported method
        mock_request = MagicMock(spec=Request)
        mock_request.method = "GET"  # Only POST and DELETE are supported
        mock_request.headers = Headers({"X-Client-ID": "test-client"})

        response = await session_manager._handle_client_request(mock_request)

        # Should return method not allowed
        assert response.status_code == 405
        assert response.headers["Allow"] == "POST, DELETE"
        response_body = json.loads(response.body.decode())
        assert "error" in response_body
        assert "Method Not Allowed" in response_body["error"]["message"]

    @pytest.mark.anyio
    async def test_delete_session_termination_missing_session_id(self, client_server_config, mock_mcp_server):
        """Test DELETE request without session ID header."""
        server_config = client_server_config["server"]["config"]
        session_manager = WebhookSessionManager(app=mock_mcp_server, config=server_config, stateless=False)

        # Mock DELETE request without session ID
        mock_request = MagicMock(spec=Request)
        mock_request.method = "DELETE"
        mock_request.headers = Headers({})  # No X-Session-ID header

        response = await session_manager._handle_delete_request(mock_request)

        # Should return error for missing session ID
        assert response.status_code == 400
        response_body = json.loads(response.body.decode())
        assert "error" in response_body
        assert "Missing X-Session-ID header" in response_body["error"]["message"]

    @pytest.mark.anyio
    async def test_delete_session_termination_invalid_session_id(self, client_server_config, mock_mcp_server):
        """Test DELETE request with invalid session ID format."""
        server_config = client_server_config["server"]["config"]
        session_manager = WebhookSessionManager(app=mock_mcp_server, config=server_config, stateless=False)

        # Mock DELETE request with invalid session ID
        mock_request = MagicMock(spec=Request)
        mock_request.method = "DELETE"
        mock_request.headers = Headers({"X-Session-ID": "invalid session"})  # Contains space

        response = await session_manager._handle_delete_request(mock_request)

        # Should return session ID validation error
        assert response.status_code == 400
        response_body = json.loads(response.body.decode())
        assert "error" in response_body
        assert "Invalid session ID format" in response_body["error"]["message"]

    @pytest.mark.anyio
    async def test_initialize_request_validation_missing_webhook_url(self, client_server_config, mock_mcp_server):
        """Test initialize request validation when webhook URL is missing."""
        server_config = client_server_config["server"]["config"]
        session_manager = WebhookSessionManager(app=mock_mcp_server, config=server_config, stateless=False)

        # Mock initialize request without webhook URL in _meta
        init_request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0"},
                "_meta": {},  # Missing webhookUrl
            },
        }

        mock_request = MagicMock(spec=Request)
        mock_request.method = "POST"
        mock_request.body = AsyncMock(return_value=json.dumps(init_request).encode())
        mock_request.headers = Headers({"X-Client-ID": "test-client"})

        response = await session_manager._handle_client_request(mock_request)

        # Should return error for missing webhook URL
        assert response.status_code == 400
        response_body = json.loads(response.body.decode())
        assert "error" in response_body
        assert "Missing webhookUrl in _meta field" in response_body["error"]

    @pytest.mark.anyio
    async def test_initialized_notification_missing_session_id(self, client_server_config, mock_mcp_server):
        """Test initialized notification without session ID."""
        server_config = client_server_config["server"]["config"]
        session_manager = WebhookSessionManager(app=mock_mcp_server, config=server_config, stateless=False)

        # Mock initialized notification without session ID
        notification = {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}

        mock_request = MagicMock(spec=Request)
        mock_request.method = "POST"
        mock_request.body = AsyncMock(return_value=json.dumps(notification).encode())
        mock_request.headers = Headers({"X-Client-ID": "test-client"})  # No X-Session-ID

        response = await session_manager._handle_client_request(mock_request)

        # Should return error for missing session ID
        assert response.status_code == 400
        response_body = json.loads(response.body.decode())
        assert "error" in response_body
        assert "Missing X-Session-ID header for initialized notification" in response_body["error"]["message"]


class TestWebhookTransportFailures:
    """Test webhook transport failure scenarios."""

    @pytest.mark.anyio
    async def test_http_client_failure(self, client_server_config):
        """Test handling of HTTP client failures."""
        client_config = client_server_config["client"]["config"]
        client_http = client_server_config["client"]["http_client"]

        # Make HTTP client raise an exception
        client_http.post.side_effect = httpx.ConnectError("Connection failed")

        with anyio.move_on_after(0.5):
            # Use refactored webhook client (no start_webhook_server to mock)
            webhook_path = "/webhook/response"
            async with webhook_client(client_config, webhook_path) as (read_stream, write_stream, client):
                # Send a request that should fail
                request = JSONRPCMessage(root=JSONRPCRequest(jsonrpc="2.0", id=1, method="test/method", params={}))
                await write_stream.send(SessionMessage(request))

                # Should receive an exception on the read stream
                with anyio.move_on_after(0.2):
                    message = await read_stream.receive()
                    assert isinstance(message, Exception)

    @pytest.mark.anyio
    async def test_webhook_response_failure(self, client_server_config):
        """Test handling of webhook response failures."""
        server_config = client_server_config["server"]["config"]
        server_http = client_server_config["server"]["http_client"]

        # Make webhook response fail
        server_http.post.side_effect = httpx.ConnectError("Webhook unreachable")

        transport = WebhookTransport(
            config=server_config,
            http_client=server_http,
            session_id="test-session",
            webhook_url="http://localhost:8001/webhook/response",
        )

        # Sending to webhook should raise an exception
        response_message = SessionMessage(
            JSONRPCMessage(root=JSONRPCResponse(jsonrpc="2.0", id=1, result={"status": "success"}))
        )

        with pytest.raises(httpx.ConnectError):
            await transport.send_to_client_webhook(response_message)
