"""Configuration and shared fixtures for webhook transport tests."""

from tests.webhook.shared_fixtures import (
    mock_http_client,
    sample_jsonrpc_request,
    sample_jsonrpc_initialize_request,
    sample_jsonrpc_response,
    sample_jsonrpc_notification,
    sample_webhook_request_body,
    sample_initialize_webhook_request,
    client_transport_config,
    server_transport_config,
    client_server_config,
    mock_mcp_server,
)

__all__ = [
    "mock_http_client",
    "sample_jsonrpc_request",
    "sample_jsonrpc_initialize_request",
    "sample_jsonrpc_response",
    "sample_jsonrpc_notification",
    "sample_webhook_request_body",
    "sample_initialize_webhook_request",
    "client_transport_config",
    "server_transport_config",
    "client_server_config",
    "mock_mcp_server",
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
def mock_session_info():
    """Mock session info for testing."""
    from asyncmcp.webhook.utils import SessionInfo

    return SessionInfo(
        session_id="test-session-123",
        client_id="test-client",
        webhook_url="http://localhost:8001/webhook/response",
        state="initialized",
    )
