"""
Tests for webhook utility functions.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock
import json

import httpx
import mcp.types as types
from mcp.shared.message import SessionMessage

from asyncmcp.webhook.utils import (
    WebhookServerConfig,
    WebhookClientConfig,
    SessionInfo,
    create_http_headers,
    parse_webhook_request,
    send_webhook_response,
    extract_webhook_url_from_meta,
    generate_session_id,
)

from tests.webhook.shared_fixtures import sample_jsonrpc_request, sample_jsonrpc_initialize_request, \
    sample_jsonrpc_response, sample_jsonrpc_notification, sample_webhook_request_body


class TestWebhookServerConfig:
    """Test WebhookServerConfig class."""

    def test_default_config(self):
        """Test default configuration values."""
        config = WebhookServerConfig()

        assert config.timeout_seconds == 30.0
        assert config.max_retries == 0
        assert config.transport_timeout_seconds is None

    def test_custom_config(self):
        """Test custom configuration values."""
        config = WebhookServerConfig(
            timeout_seconds=60.0,
            max_retries=3,
            transport_timeout_seconds=120.0,
        )

        assert config.timeout_seconds == 60.0
        assert config.max_retries == 3
        assert config.transport_timeout_seconds == 120.0


class TestWebhookClientConfig:
    """Test WebhookClientConfig class."""

    def test_default_config(self):
        """Test default configuration values."""
        config = WebhookClientConfig()

        assert config.server_url == "http://localhost:8000/mcp/request"
        assert config.timeout_seconds == 30.0
        assert config.max_retries == 0
        assert config.client_id is not None  # Auto-generated
        assert config.transport_timeout_seconds is None

    def test_custom_config(self):
        """Test custom configuration values."""
        config = WebhookClientConfig(
            server_url="http://localhost:9000/mcp/request",
            timeout_seconds=60.0,
            max_retries=3,
            client_id="custom-client",
            transport_timeout_seconds=120.0,
        )

        assert config.server_url == "http://localhost:9000/mcp/request"
        assert config.timeout_seconds == 60.0
        assert config.max_retries == 3
        assert config.client_id == "custom-client"
        assert config.transport_timeout_seconds == 120.0


class TestSessionInfo:
    """Test SessionInfo class."""

    def test_session_info_creation(self):
        """Test creating a SessionInfo instance."""
        session_info = SessionInfo(
            session_id="test-session-123",
            client_id="test-client",
            webhook_url="http://localhost:8001/webhook/response",
            state="initialized",
        )

        assert session_info.session_id == "test-session-123"
        assert session_info.client_id == "test-client"
        assert session_info.webhook_url == "http://localhost:8001/webhook/response"
        assert session_info.state == "initialized"


class TestUtilityFunctions:
    """Test utility functions."""

    @pytest.mark.anyio
    async def test_create_http_headers_with_request(self, sample_jsonrpc_request):
        """Test creating HTTP headers for a request."""
        session_message = SessionMessage(sample_jsonrpc_request)

        headers = await create_http_headers(session_message, session_id="test-session", client_id="test-client")

        assert headers["Content-Type"] == "application/json"
        assert headers["User-Agent"] == "asyncmcp-webhook/1.0"
        assert headers["X-Client-ID"] == "test-client"
        assert headers["X-Session-ID"] == "test-session"
        assert headers["X-Request-ID"] == "1"
        assert headers["X-Method"] == "test/method"

    @pytest.mark.anyio
    async def test_create_http_headers_with_notification(self, sample_jsonrpc_notification):
        """Test creating HTTP headers for a notification."""
        session_message = SessionMessage(sample_jsonrpc_notification)

        headers = await create_http_headers(session_message, session_id="test-session", client_id="test-client")

        assert headers["Content-Type"] == "application/json"
        assert headers["User-Agent"] == "asyncmcp-webhook/1.0"
        assert headers["X-Client-ID"] == "test-client"
        assert headers["X-Session-ID"] == "test-session"
        assert headers["X-Method"] == "test/notification"
        # Notifications don't have request ID
        assert "X-Request-ID" not in headers

    @pytest.mark.anyio
    async def test_parse_webhook_request_valid(self, sample_webhook_request_body):
        """Test parsing a valid webhook request."""
        session_message = await parse_webhook_request(sample_webhook_request_body)

        assert isinstance(session_message, SessionMessage)
        assert session_message.message.root.method == "test/method"
        assert session_message.message.root.id == 1
        assert session_message.message.root.params == {"key": "value"}

    @pytest.mark.anyio
    async def test_parse_webhook_request_invalid(self):
        """Test parsing an invalid webhook request."""
        with pytest.raises(Exception):
            await parse_webhook_request(b"invalid json content")

    @pytest.mark.anyio
    async def test_send_webhook_response_success(self, sample_jsonrpc_response):
        """Test sending a successful webhook response."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_response

        session_message = SessionMessage(sample_jsonrpc_response)

        await send_webhook_response(
            mock_client, "http://localhost:8001/webhook", session_message, "test-session", "test-client"
        )

        mock_client.post.assert_called_once()
        mock_response.raise_for_status.assert_called_once()

    @pytest.mark.anyio
    async def test_send_webhook_response_failure(self, sample_jsonrpc_response):
        """Test sending a webhook response that fails."""
        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.HTTPError("Connection failed")

        session_message = SessionMessage(sample_jsonrpc_response)

        with pytest.raises(httpx.HTTPError):
            await send_webhook_response(
                mock_client, "http://localhost:8001/webhook", session_message, "test-session", "test-client"
            )

    def test_extract_webhook_url_from_meta_valid(self, sample_jsonrpc_initialize_request):
        """Test extracting webhook URL from valid _meta field."""
        webhook_url = extract_webhook_url_from_meta(sample_jsonrpc_initialize_request)

        assert webhook_url == "http://localhost:8001/webhook/response"

    def test_extract_webhook_url_from_meta_missing(self, sample_jsonrpc_request):
        """Test extracting webhook URL when _meta field is missing."""
        webhook_url = extract_webhook_url_from_meta(sample_jsonrpc_request)

        assert webhook_url is None

    def test_extract_webhook_url_from_meta_notification(self, sample_jsonrpc_notification):
        """Test extracting webhook URL from notification (should return None)."""
        webhook_url = extract_webhook_url_from_meta(sample_jsonrpc_notification)

        assert webhook_url is None

    def test_generate_session_id(self):
        """Test generating a session ID."""
        session_id = generate_session_id()

        assert isinstance(session_id, str)
        assert len(session_id) > 0
        # Should be a UUID format
        assert len(session_id.split("-")) == 5
