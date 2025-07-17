"""
Tests for webhook transport utilities.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
import httpx
import orjson

import mcp.types as types
from mcp.shared.message import SessionMessage

from asyncmcp.webhook.utils import (
    WebhookTransportConfig,
    SessionInfo,
    create_http_headers,
    parse_webhook_request,
    send_webhook_response,
    extract_webhook_url_from_meta,
    generate_session_id,
)


class TestWebhookTransportConfig:
    """Test WebhookTransportConfig class."""
    
    def test_default_config(self):
        """Test default configuration values."""
        config = WebhookTransportConfig()
        assert config.server_host == "0.0.0.0"
        assert config.server_port == 8000
        assert config.webhook_host == "0.0.0.0"
        assert config.webhook_port == 8001
        assert config.timeout_seconds == 30.0
        assert config.max_retries == 0
        assert config.client_id is not None  # Should be auto-generated
    
    def test_custom_config(self):
        """Test custom configuration values."""
        config = WebhookTransportConfig(
            server_host="localhost",
            server_port=9000,
            webhook_host="127.0.0.1",
            webhook_port=9001,
            timeout_seconds=60.0,
            client_id="custom-client",
        )
        assert config.server_host == "localhost"
        assert config.server_port == 9000
        assert config.webhook_host == "127.0.0.1"
        assert config.webhook_port == 9001
        assert config.timeout_seconds == 60.0
        assert config.client_id == "custom-client"


class TestSessionInfo:
    """Test SessionInfo class."""
    
    def test_session_info_creation(self):
        """Test SessionInfo creation."""
        session_info = SessionInfo(
            session_id="test-session",
            client_id="test-client",
            webhook_url="http://localhost:8001/webhook",
            state="initialized"
        )
        assert session_info.session_id == "test-session"
        assert session_info.client_id == "test-client"
        assert session_info.webhook_url == "http://localhost:8001/webhook"
        assert session_info.state == "initialized"


class TestUtilityFunctions:
    """Test utility functions."""
    
    @pytest.mark.anyio
    async def test_create_http_headers_with_request(self, sample_jsonrpc_request):
        """Test creating HTTP headers for a request."""
        message = types.JSONRPCMessage(root=sample_jsonrpc_request)
        session_message = SessionMessage(message)
        
        headers = await create_http_headers(
            session_message,
            session_id="test-session",
            client_id="test-client"
        )
        
        assert headers["Content-Type"] == "application/json"
        assert headers["X-Client-ID"] == "test-client"
        assert headers["X-Session-ID"] == "test-session"
        assert headers["X-Request-ID"] == "1"
        assert headers["X-Method"] == "test/method"
    
    @pytest.mark.anyio
    async def test_create_http_headers_with_notification(self, sample_jsonrpc_notification):
        """Test creating HTTP headers for a notification."""
        message = types.JSONRPCMessage(root=sample_jsonrpc_notification)
        session_message = SessionMessage(message)
        
        headers = await create_http_headers(
            session_message,
            session_id="test-session",
            client_id="test-client"
        )
        
        assert headers["Content-Type"] == "application/json"
        assert headers["X-Client-ID"] == "test-client"
        assert headers["X-Session-ID"] == "test-session"
        assert headers["X-Method"] == "test/notification"
        assert "X-Request-ID" not in headers
    
    @pytest.mark.anyio
    async def test_parse_webhook_request_valid(self, sample_jsonrpc_request):
        """Test parsing a valid webhook request."""
        message = types.JSONRPCMessage(root=sample_jsonrpc_request)
        json_body = message.model_dump_json(by_alias=True, exclude_none=True)
        
        session_message = await parse_webhook_request(json_body.encode('utf-8'))
        
        assert isinstance(session_message, SessionMessage)
        assert session_message.message.root.id == 1
        assert session_message.message.root.method == "test/method"
    
    @pytest.mark.anyio
    async def test_parse_webhook_request_invalid(self):
        """Test parsing an invalid webhook request."""
        invalid_json = b'{"invalid": json}'
        
        with pytest.raises(orjson.JSONDecodeError):
            await parse_webhook_request(invalid_json)
    
    @pytest.mark.anyio
    async def test_send_webhook_response_success(self, sample_session_message):
        """Test sending a successful webhook response."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_client.post.return_value = mock_response
        
        await send_webhook_response(
            mock_client,
            "http://localhost:8001/webhook",
            sample_session_message,
            "test-session",
            "test-client"
        )
        
        mock_client.post.assert_called_once()
        mock_response.raise_for_status.assert_called_once()
    
    @pytest.mark.anyio
    async def test_send_webhook_response_failure(self, sample_session_message):
        """Test sending a webhook response that fails."""
        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.HTTPError("Connection failed")
        
        with pytest.raises(httpx.HTTPError):
            await send_webhook_response(
                mock_client,
                "http://localhost:8001/webhook",
                sample_session_message,
                "test-session",
                "test-client"
            )
    
    def test_extract_webhook_url_from_meta_valid(self, sample_initialize_request):
        """Test extracting webhook URL from _meta field."""
        message = types.JSONRPCMessage(root=sample_initialize_request)
        
        webhook_url = extract_webhook_url_from_meta(message)
        
        assert webhook_url == "http://localhost:8001/webhook/response"
    
    def test_extract_webhook_url_from_meta_missing(self, sample_jsonrpc_request):
        """Test extracting webhook URL when _meta field is missing."""
        message = types.JSONRPCMessage(root=sample_jsonrpc_request)
        
        webhook_url = extract_webhook_url_from_meta(message)
        
        assert webhook_url is None
    
    def test_extract_webhook_url_from_meta_notification(self, sample_jsonrpc_notification):
        """Test extracting webhook URL from notification (should return None)."""
        message = types.JSONRPCMessage(root=sample_jsonrpc_notification)
        
        webhook_url = extract_webhook_url_from_meta(message)
        
        assert webhook_url is None
    
    def test_generate_session_id(self):
        """Test session ID generation."""
        session_id1 = generate_session_id()
        session_id2 = generate_session_id()
        
        assert isinstance(session_id1, str)
        assert isinstance(session_id2, str)
        assert session_id1 != session_id2  # Should be unique
        assert len(session_id1) > 0
        assert len(session_id2) > 0 