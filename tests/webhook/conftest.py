"""
Configuration and shared fixtures for webhook transport tests.
"""

# Import all shared fixtures so they're available to all test files
from tests.webhook.shared_fixtures import (
    webhook_transport_config,
    mock_httpx_client,
    mock_uvicorn_server,
    sample_jsonrpc_request,
    sample_jsonrpc_response,
    sample_jsonrpc_notification,
    sample_initialize_request,
    sample_initialized_notification,
    sample_session_message,
    sample_session_message_with_metadata,
    sample_http_request_body,
    sample_http_headers,
)

# Make fixtures available for import
__all__ = [
    "webhook_transport_config",
    "mock_httpx_client",
    "mock_uvicorn_server",
    "sample_jsonrpc_request",
    "sample_jsonrpc_response",
    "sample_jsonrpc_notification",
    "sample_initialize_request",
    "sample_initialized_notification",
    "sample_session_message",
    "sample_session_message_with_metadata",
    "sample_http_request_body",
    "sample_http_headers",
]

import pytest
import anyio
from unittest.mock import MagicMock, AsyncMock, patch
from typing import Dict, Any

# anyio backend configuration moved to top-level conftest.py


@pytest.fixture
def mock_starlette_request():
    """Mock Starlette request for testing."""
    mock_request = MagicMock()
    mock_request.body = AsyncMock(return_value=b'{"jsonrpc": "2.0", "id": 1, "method": "test", "params": {}}')
    mock_request.headers = {
        "Content-Type": "application/json",
        "X-Client-ID": "test-client",
        "X-Request-ID": "1",
        "X-Method": "test/method",
        "X-Session-ID": "test-session-123",
    }
    return mock_request


@pytest.fixture
def mock_starlette_response():
    """Mock Starlette response for testing."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    return mock_response


@pytest.fixture
def client_server_config(webhook_transport_config):
    """Create client and server configurations for integration tests."""
    # Client config - webhook server listens on port 8001
    client_config = WebhookTransportConfig(
        server_host="localhost",
        server_port=8000,
        webhook_host="localhost",
        webhook_port=8001,
        client_id="test-client",
        timeout_seconds=10.0,
    )
    
    # Server config - HTTP server listens on port 8000
    server_config = WebhookTransportConfig(
        server_host="localhost",
        server_port=8000,
        webhook_host="localhost",
        webhook_port=8001,
        timeout_seconds=10.0,
    )
    
    return {"client": client_config, "server": server_config}


@pytest.fixture
def mock_session_info():
    """Mock session info for testing."""
    from asyncmcp.webhook.utils import SessionInfo
    return SessionInfo(
        session_id="test-session-123",
        client_id="test-client",
        webhook_url="http://localhost:8001/webhook/response",
        state="initialized"
    ) 