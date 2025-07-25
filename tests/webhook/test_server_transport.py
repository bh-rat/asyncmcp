"""
Comprehensive tests for webhook server transport module.
"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

import anyio
import httpx
from mcp.types import JSONRPCMessage, JSONRPCRequest, JSONRPCResponse, JSONRPCNotification
from mcp.shared.message import SessionMessage
from mcp.server.lowlevel import Server
from asyncmcp.webhook.server import WebhookTransport, webhook_server
from asyncmcp.webhook.manager import WebhookSessionManager
from asyncmcp.webhook.utils import WebhookTransportConfig, send_webhook_response, SessionInfo
from asyncmcp.common.outgoing_event import OutgoingMessageEvent

from .shared_fixtures import (
    mock_http_client,
    sample_jsonrpc_request,
    sample_jsonrpc_initialize_request,
    sample_jsonrpc_response,
    server_transport_config,
    mock_mcp_server,
)


@pytest.fixture
def transport_config(server_transport_config):
    """Create a test transport configuration - using shared fixture."""
    return server_transport_config


class TestWebhookTransport:
    """Test the WebhookTransport class."""

    @pytest.mark.anyio
    async def test_transport_init(self, transport_config, mock_http_client):
        """Test WebhookTransport initialization."""
        session_id = "test-session-123"
        webhook_url = "http://localhost:8001/webhook/response"

        transport = WebhookTransport(
            config=transport_config,
            http_client=mock_http_client,
            session_id=session_id,
            webhook_url=webhook_url,
        )

        assert transport.session_id == session_id
        assert transport.webhook_url == webhook_url
        assert not transport.is_terminated
        assert transport.http_client == mock_http_client

    @pytest.mark.anyio
    async def test_transport_connect_and_cleanup(self, transport_config, mock_http_client):
        """Test transport connect and cleanup."""
        transport = WebhookTransport(config=transport_config, http_client=mock_http_client, session_id="test-session")

        async with transport.connect() as (read_stream, write_stream):
            assert transport._read_stream is not None
            assert transport._write_stream is not None

        # After context exits, streams should be cleaned up
        assert transport._read_stream is None
        assert transport._write_stream is None

    @pytest.mark.anyio
    async def test_set_webhook_url(self, transport_config, mock_http_client):
        """Test setting webhook URL."""
        transport = WebhookTransport(config=transport_config, http_client=mock_http_client, session_id="test-session")

        new_webhook_url = "http://localhost:8002/webhook/response"
        transport.set_webhook_url(new_webhook_url)

        assert transport.webhook_url == new_webhook_url

    @pytest.mark.anyio
    async def test_send_to_client_webhook_success(self, transport_config, mock_http_client, sample_jsonrpc_response):
        """Test sending message to client webhook successfully."""
        webhook_url = "http://localhost:8001/webhook/response"
        transport = WebhookTransport(
            config=transport_config,
            http_client=mock_http_client,
            session_id="test-session",
            webhook_url=webhook_url,
        )

        session_message = SessionMessage(sample_jsonrpc_response)

        with patch("asyncmcp.webhook.server.send_webhook_response") as mock_send:
            await transport.send_to_client_webhook(session_message)

            mock_send.assert_called_once_with(mock_http_client, webhook_url, session_message, "test-session", None)

    @pytest.mark.anyio
    async def test_send_to_client_webhook_no_url(self, transport_config, mock_http_client, sample_jsonrpc_response):
        """Test sending message when no webhook URL is set."""
        transport = WebhookTransport(
            config=transport_config,
            http_client=mock_http_client,
            session_id="test-session",
            webhook_url=None,
        )

        session_message = SessionMessage(sample_jsonrpc_response)

        # Should not raise an exception, just log a warning
        await transport.send_to_client_webhook(session_message)

    @pytest.mark.anyio
    async def test_send_to_client_webhook_http_error(self, transport_config, mock_http_client, sample_jsonrpc_response):
        """Test handling HTTP error when sending to webhook."""
        webhook_url = "http://localhost:8001/webhook/response"
        transport = WebhookTransport(
            config=transport_config,
            http_client=mock_http_client,
            session_id="test-session",
            webhook_url=webhook_url,
        )

        session_message = SessionMessage(sample_jsonrpc_response)

        with patch("asyncmcp.webhook.server.send_webhook_response") as mock_send:
            mock_send.side_effect = httpx.HTTPError("Network error")

            # Should not raise - HTTP errors are caught and logged
            await transport.send_to_client_webhook(session_message)

    @pytest.mark.anyio
    async def test_send_to_client_webhook_unexpected_error(
        self, transport_config, mock_http_client, sample_jsonrpc_response
    ):
        """Test handling unexpected error when sending to webhook."""
        webhook_url = "http://localhost:8001/webhook/response"
        transport = WebhookTransport(
            config=transport_config,
            http_client=mock_http_client,
            session_id="test-session",
            webhook_url=webhook_url,
        )

        session_message = SessionMessage(sample_jsonrpc_response)

        with patch("asyncmcp.webhook.server.send_webhook_response") as mock_send:
            mock_send.side_effect = ValueError("Unexpected error")

            # Unexpected errors are logged but not re-raised
            await transport.send_to_client_webhook(session_message)

    @pytest.mark.anyio
    async def test_send_to_client_webhook_terminated_session(
        self, transport_config, mock_http_client, sample_jsonrpc_response
    ):
        """Test sending to webhook when session is terminated."""
        webhook_url = "http://localhost:8001/webhook/response"
        transport = WebhookTransport(
            config=transport_config,
            http_client=mock_http_client,
            session_id="test-session",
            webhook_url=webhook_url,
        )

        # Terminate the transport
        await transport.terminate()

        session_message = SessionMessage(sample_jsonrpc_response)

        with patch("asyncmcp.webhook.server.send_webhook_response") as mock_send:
            await transport.send_to_client_webhook(session_message)
            # Should not call send_webhook_response for terminated sessions
            mock_send.assert_not_called()


class TestWebhookServerContextManager:
    """Test the webhook_server context manager function."""

    @pytest.mark.anyio
    async def test_server_context_manager_basic(self, transport_config, mock_http_client):
        """Test basic server context manager functionality."""
        # Skip this test for now due to context manager complexity
        pytest.skip("Server context manager test needs more complex mocking")


class TestWebhookSessionManager:
    """Test the WebhookSessionManager class."""

    @pytest.mark.anyio
    async def test_session_manager_init(self, transport_config, mock_mcp_server):
        """Test WebhookSessionManager initialization."""
        manager = WebhookSessionManager(app=mock_mcp_server, config=transport_config)

        assert manager.app == mock_mcp_server
        assert manager.config == transport_config
        assert not manager.stateless
        assert len(manager._transport_instances) == 0
        assert len(manager._sessions) == 0

    @pytest.mark.anyio
    async def test_session_manager_run_lifecycle(self, transport_config, mock_mcp_server):
        """Test session manager run lifecycle."""
        manager = WebhookSessionManager(app=mock_mcp_server, config=transport_config)

        with patch("asyncmcp.webhook.manager.WebhookSessionManager._start_http_server") as mock_start_server:
            mock_start_server.return_value = AsyncMock()

            with anyio.move_on_after(0.1):  # Short timeout for test
                async with manager.run():
                    assert manager._task_group is not None

            # After context exits, task group should be None
            assert manager._task_group is None

    @pytest.mark.anyio
    async def test_handle_initialize_request(self, transport_config, mock_mcp_server):
        """Test handling initialize request."""
        manager = WebhookSessionManager(app=mock_mcp_server, config=transport_config)

        # Mock HTTP client
        mock_http_client = AsyncMock()
        manager.http_client = mock_http_client

        # Create initialize request
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
        session_message = SessionMessage(init_request)

        with (
            patch.object(manager._task_group, "start", new_callable=AsyncMock)
            if manager._task_group
            else patch("anyio.create_task_group")
        ):
            # Mock task group for session startup
            manager._task_group = MagicMock()
            manager._task_group.start = AsyncMock()

            response = await manager._handle_initialize_request(session_message, "test-client", None)

            assert response.status_code == 200
            assert len(manager._sessions) == 1
            assert len(manager._transport_instances) == 1

    @pytest.mark.anyio
    async def test_handle_initialized_notification(self, transport_config, mock_mcp_server):
        """Test handling initialized notification."""
        manager = WebhookSessionManager(app=mock_mcp_server, config=transport_config)
        session_id = "test-session-123"
        manager._sessions[session_id] = SessionInfo(
            session_id=session_id,
            client_id="test-client",
            webhook_url="http://localhost:8001/webhook/response",
            state="init_pending",
        )
        manager._transport_instances[session_id] = WebhookTransport(
            config=transport_config,
            http_client=AsyncMock(),
            session_id=session_id,
            webhook_url="http://localhost:8001/webhook/response",
        )

        # Create a valid initialized notification session_message
        notification = JSONRPCMessage(
            root=JSONRPCNotification(jsonrpc="2.0", method="notifications/initialized", params={})
        )
        session_message = SessionMessage(notification)
        response = await manager._handle_initialized_notification(session_message, session_id)
        assert response.status_code == 200
        assert manager._sessions[session_id].state == "initialized"

    @pytest.mark.anyio
    async def test_terminate_session(self, transport_config, mock_mcp_server):
        """Test terminating a specific session."""
        manager = WebhookSessionManager(app=mock_mcp_server, config=transport_config)

        # Create a transport and session manually
        mock_http_client = AsyncMock()
        transport = WebhookTransport(
            config=transport_config, http_client=mock_http_client, session_id="test-session-123"
        )

        session_info = SessionInfo(
            session_id="test-session-123",
            client_id="test-client",
            webhook_url="http://localhost:8001/webhook/response",
            state="initialized",
        )

        manager._transport_instances["test-session-123"] = transport
        manager._sessions["test-session-123"] = session_info
        manager._client_sessions["test-client"] = "test-session-123"

        # Terminate the session
        result = await manager.terminate_session("test-session-123")

        assert result is True
        assert len(manager._transport_instances) == 0
        assert len(manager._sessions) == 0
        assert len(manager._client_sessions) == 0

    @pytest.mark.anyio
    async def test_get_all_sessions(self, transport_config, mock_mcp_server):
        """Test getting all session information."""
        manager = WebhookSessionManager(app=mock_mcp_server, config=transport_config)

        # Create a transport and session manually
        mock_http_client = AsyncMock()
        transport = WebhookTransport(
            config=transport_config, http_client=mock_http_client, session_id="test-session-123"
        )

        session_info = SessionInfo(
            session_id="test-session-123",
            client_id="test-client",
            webhook_url="http://localhost:8001/webhook/response",
            state="initialized",
        )

        manager._transport_instances["test-session-123"] = transport
        manager._sessions["test-session-123"] = session_info

        sessions = manager.get_all_sessions()

        assert len(sessions) == 1
        assert "test-session-123" in sessions
        session_data = sessions["test-session-123"]
        assert session_data["client_id"] == "test-client"
        assert session_data["webhook_url"] == "http://localhost:8001/webhook/response"
        assert session_data["state"] == "initialized"

    @pytest.mark.anyio
    async def test_session_limit_exceeded(self, transport_config, mock_mcp_server):
        """Test that session creation fails when max sessions exceeded."""
        manager = WebhookSessionManager(app=mock_mcp_server, config=transport_config)
        manager._max_sessions = 1  # Set low limit for testing

        # Mock HTTP client
        mock_http_client = AsyncMock()
        manager.http_client = mock_http_client

        # Create first session (should succeed)
        init_request = JSONRPCMessage(
            root=JSONRPCRequest(
                jsonrpc="2.0",
                id=1,
                method="initialize",
                params={
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test-client-1", "version": "1.0"},
                    "_meta": {"webhookUrl": "http://localhost:8001/webhook/response"},
                },
            )
        )
        session_message1 = SessionMessage(init_request)

        with (
            patch.object(manager._task_group, "start", new_callable=AsyncMock)
            if manager._task_group
            else patch("anyio.create_task_group")
        ):
            manager._task_group = MagicMock()
            manager._task_group.start = AsyncMock()

            response1 = await manager._handle_initialize_request(session_message1, "test-client-1", None)
            assert response1.status_code == 200

            # Try to create second session (should fail due to limit)
            init_request2 = JSONRPCMessage(
                root=JSONRPCRequest(
                    jsonrpc="2.0",
                    id=2,
                    method="initialize",
                    params={
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "test-client-2", "version": "1.0"},
                        "_meta": {"webhookUrl": "http://localhost:8002/webhook/response"},
                    },
                )
            )
            session_message2 = SessionMessage(init_request2)

            response2 = await manager._handle_initialize_request(session_message2, "test-client-2", None)
            assert response2.status_code == 503  # Service unavailable

            # Verify first session still exists, second was not created
            assert len(manager._sessions) == 1


class TestSendWebhookResponse:
    """Test the send_webhook_response utility function."""

    @pytest.mark.anyio
    async def test_send_webhook_response_success(self, sample_jsonrpc_response):
        """Test successful webhook response sending."""
        mock_http_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_http_client.post.return_value = mock_response

        webhook_url = "http://localhost:8001/webhook/response"
        session_message = SessionMessage(sample_jsonrpc_response)

        await send_webhook_response(mock_http_client, webhook_url, session_message, "test-session", "test-client")

        mock_http_client.post.assert_called_once()
        call_args = mock_http_client.post.call_args
        # Check positional args (url is first arg)
        assert call_args[0][0] == webhook_url
        # Check kwargs for headers and content
        assert "headers" in call_args.kwargs
        assert "content" in call_args.kwargs

    @pytest.mark.anyio
    async def test_send_webhook_response_http_error(self, sample_jsonrpc_response):
        """Test webhook response sending with HTTP error."""
        mock_http_client = AsyncMock()
        mock_http_client.post.side_effect = httpx.HTTPError("Network error")

        webhook_url = "http://localhost:8001/webhook/response"
        session_message = SessionMessage(sample_jsonrpc_response)

        with pytest.raises(httpx.HTTPError):
            await send_webhook_response(mock_http_client, webhook_url, session_message, "test-session", "test-client")


class TestOutgoingMessageEvent:
    """Test the OutgoingMessageEvent class."""

    def test_outgoing_message_event_creation(self, sample_jsonrpc_response):
        """Test creating an OutgoingMessageEvent."""
        session_message = SessionMessage(sample_jsonrpc_response)
        event = OutgoingMessageEvent(session_id="test-session-123", message=session_message)

        assert event.session_id == "test-session-123"
        assert event.message == session_message
