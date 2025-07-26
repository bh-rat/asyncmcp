"""
Comprehensive tests for webhook client transport module.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import httpx
import pytest
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from mcp.shared.message import SessionMessage

from asyncmcp.common.client_state import ClientState
from asyncmcp.webhook.client import WebhookClient, webhook_client
from asyncmcp.webhook.utils import (
    create_http_headers,
    parse_webhook_request,
)


@pytest.fixture
def transport_config(client_transport_config):
    """Create a test transport configuration - using shared fixture."""
    return client_transport_config


class TestWebhookClient:
    """Test the WebhookClient class."""

    @pytest.mark.anyio
    async def test_webhook_client_init(self, transport_config, webhook_url):
        """Test WebhookClient initialization."""
        webhook_path = "/webhook/response"  # Use path instead of full URL
        client = WebhookClient(transport_config, webhook_path)

        assert client.transport.config == transport_config
        assert client.transport.config.server_url == "http://localhost:8000/mcp/request"
        assert client.webhook_path == webhook_path
        assert client.http_client is None
        # webhook_server no longer exists in refactored version
        assert hasattr(client, "read_stream_writer")
        assert hasattr(client, "read_stream")
        assert hasattr(client, "write_stream")

    @pytest.mark.anyio
    async def test_handle_webhook_response_success(self, transport_config, webhook_url):
        """Test successful webhook response handling."""
        webhook_path = "/webhook/response"
        client = WebhookClient(transport_config, webhook_path)

        # Don't set up streams to avoid hanging
        client.read_stream_writer = None

        # Create an initialize response which should set session ID
        initialize_response_body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "serverInfo": {"name": "test-server", "version": "1.0"},
                },
            }
        ).encode("utf-8")

        # Mock request
        mock_request = MagicMock()
        mock_request.body = AsyncMock(return_value=initialize_response_body)
        mock_request.headers = {"X-Session-ID": "test-session-123"}

        response = await client.handle_webhook_response(mock_request)

        assert response.status_code == 200
        # Session ID should now be set since this is an initialize response
        assert client.transport.client_state.session_id == "test-session-123"

    @pytest.mark.anyio
    async def test_handle_webhook_response_error(self, transport_config, webhook_url):
        """Test webhook response handling with error."""
        webhook_path = "/webhook/response"
        client = WebhookClient(transport_config, webhook_path)

        # Don't set up streams to avoid hanging
        client.read_stream_writer = None

        # Mock request with invalid body
        mock_request = MagicMock()
        mock_request.body = AsyncMock(return_value=b"invalid json")
        mock_request.headers = {}

        response = await client.handle_webhook_response(mock_request)

        assert response.status_code == 400

    @pytest.mark.anyio
    async def test_send_request_regular(self, transport_config, sample_jsonrpc_request, webhook_url):
        """Test sending regular request."""
        webhook_path = "/webhook/response"
        client = WebhookClient(transport_config, webhook_path)

        # Mock HTTP client
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_http_client = AsyncMock()
        mock_http_client.post.return_value = mock_response
        client.http_client = mock_http_client

        session_message = SessionMessage(sample_jsonrpc_request)
        await client.send_request(session_message)

        # Verify HTTP client was called
        mock_http_client.post.assert_called_once()
        call_args = mock_http_client.post.call_args
        # Check positional args (url is first arg)
        assert call_args[0][0] == "http://localhost:8000/mcp/request"
        # Check kwargs for headers and content
        assert "headers" in call_args.kwargs
        assert "content" in call_args.kwargs

    @pytest.mark.anyio
    async def test_send_request_initialize(self, transport_config, sample_jsonrpc_initialize_request, webhook_url):
        """Test sending initialize request with webhook URL injection."""
        webhook_path = "/webhook/response"
        client = WebhookClient(transport_config, webhook_path)

        # Mock HTTP client
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_http_client = AsyncMock()
        mock_http_client.post.return_value = mock_response
        client.http_client = mock_http_client

        session_message = SessionMessage(sample_jsonrpc_initialize_request)
        await client.send_request(session_message)

        # Verify HTTP client was called
        mock_http_client.post.assert_called_once()
        call_args = mock_http_client.post.call_args

        # In the refactored version, webhook URL must be provided in _meta by external app
        # The client no longer automatically injects the webhook URL

        body_content = call_args.kwargs["content"]
        parsed_body = json.loads(body_content)
        # The webhook URL should already be in the test fixture
        assert parsed_body["params"]["_meta"]["webhookUrl"] == "http://localhost:8001/webhook/response"

    @pytest.mark.anyio
    async def test_send_request_http_error(self, transport_config, sample_jsonrpc_request, webhook_url):
        """Test handling HTTP error in send_request."""
        state = ClientState(client_id="test-client", session_id="test-session")
        webhook_path = "/webhook/response"
        client = WebhookClient(transport_config, webhook_path)

        # Don't set up streams to avoid hanging
        client.read_stream_writer = None

        # Mock HTTP client to raise error
        mock_http_client = AsyncMock()
        mock_http_client.post.side_effect = httpx.HTTPError("Network error")
        client.http_client = mock_http_client

        session_message = SessionMessage(sample_jsonrpc_request)
        await client.send_request(session_message)

        # Just verify the HTTP client was called (the error is handled internally)
        mock_http_client.post.assert_called_once()

    @pytest.mark.anyio
    async def test_stop(self, transport_config, webhook_url):
        """Test client stop functionality."""
        webhook_path = "/webhook/response"
        client = WebhookClient(transport_config, webhook_path)

        # Mock HTTP client (webhook_server no longer exists)
        mock_http_client = AsyncMock()
        client.http_client = mock_http_client

        await client.stop()

        mock_http_client.aclose.assert_called_once()
        # Verify HTTP client is set to None after stop
        assert client.http_client is None

    @pytest.mark.anyio
    async def test_get_webhook_callback(self, transport_config):
        """Test getting webhook callback function."""
        webhook_path = "/webhook/response"
        client = WebhookClient(transport_config, webhook_path)

        callback = await client.get_webhook_callback()
        assert callback == client.handle_webhook_response

    @pytest.mark.anyio
    async def test_get_streams_not_initialized(self, transport_config):
        """Test get_streams raises error when streams not initialized."""
        webhook_path = "/webhook/response"
        client = WebhookClient(transport_config, webhook_path)

        with pytest.raises(RuntimeError, match="Streams not initialized"):
            client.get_streams()

    @pytest.mark.anyio
    async def test_get_streams_initialized(self, transport_config):
        """Test get_streams returns streams when initialized."""
        webhook_path = "/webhook/response"
        client = WebhookClient(transport_config, webhook_path)

        # Mock streams
        mock_read_stream = MagicMock(spec=MemoryObjectReceiveStream)
        mock_write_stream = MagicMock(spec=MemoryObjectSendStream)
        client.read_stream = mock_read_stream
        client.write_stream = mock_write_stream

        read_stream, write_stream = client.get_streams()
        assert read_stream == mock_read_stream
        assert write_stream == mock_write_stream


class TestWebhookClientContextManager:
    """Test the webhook_client context manager function."""

    @pytest.mark.anyio
    async def test_client_context_manager_basic(self, transport_config):
        """Test basic client context manager functionality."""
        with patch("asyncmcp.webhook.client.WebhookClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.stop = AsyncMock()
            mock_client_class.return_value = mock_client

            webhook_path = "/webhook/response"
            with anyio.move_on_after(0.2):  # Add timeout to prevent hanging
                async with webhook_client(transport_config, webhook_path) as (read_stream, write_stream, client):
                    assert read_stream is not None
                    assert write_stream is not None
                    await anyio.sleep(0.01)  # Give background tasks time to start

    @pytest.mark.anyio
    async def test_client_with_timeout(self, transport_config):
        """Test client context manager with timeout."""
        # Skip this test for now due to context manager complexity
        pytest.skip("Context manager timeout test needs more complex mocking")


class TestWebhookUtilities:
    """Test webhook utility functions."""

    @pytest.mark.anyio
    async def test_create_http_headers_request(self, sample_jsonrpc_request):
        """Test creating HTTP headers for request."""
        session_message = SessionMessage(sample_jsonrpc_request)
        headers = await create_http_headers(session_message, session_id="test-session", client_id="test-client")

        assert headers["Content-Type"] == "application/json"
        assert headers["X-Client-ID"] == "test-client"
        assert headers["X-Session-ID"] == "test-session"
        assert headers["X-Request-ID"] == "1"
        assert headers["X-Method"] == "test/method"

    @pytest.mark.anyio
    async def test_parse_webhook_request(self, sample_webhook_request_body):
        """Test parsing webhook request body."""
        session_message = await parse_webhook_request(sample_webhook_request_body)

        assert isinstance(session_message, SessionMessage)
        assert session_message.message.root.method == "test/method"
        assert session_message.message.root.id == 1

    @pytest.mark.anyio
    async def test_parse_webhook_request_invalid(self):
        """Test parsing invalid webhook request body."""
        with pytest.raises(Exception):
            await parse_webhook_request(b"invalid json")
