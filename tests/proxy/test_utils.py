"""Tests for proxy utilities and configuration."""

import pytest

from asyncmcp.proxy.utils import (
    ProxyConfig,
    generate_session_id,
    validate_auth_token,
)
from asyncmcp.sqs.utils import SqsClientConfig
from asyncmcp.sns_sqs.utils import SnsSqsClientConfig
from asyncmcp.webhook.utils import WebhookClientConfig


class TestProxyConfig:
    """Test ProxyConfig class."""
    
    def test_default_config(self):
        """Test default configuration values."""
        config = ProxyConfig(
            backend_config=SqsClientConfig(
                read_queue_url="https://sqs.example.com/queue",
                response_queue_url="https://sqs.example.com/response",
            )
        )
        
        assert config.host == "127.0.0.1"
        assert config.port == 8080
        assert config.server_path == "/mcp"
        assert config.backend_transport == "sqs"
        assert config.session_timeout == 300.0
        assert config.max_sessions == 100
        assert config.stateless is False
        
    def test_config_validation(self):
        """Test configuration validation."""
        # Missing backend_config
        with pytest.raises(ValueError, match="backend_config is required"):
            ProxyConfig()
            
        # Mismatched transport type and config
        with pytest.raises(ValueError, match="backend_config must be SqsClientConfig"):
            ProxyConfig(
                backend_transport="sqs",
                backend_config=WebhookClientConfig(server_url="http://example.com"),
            )
            
    def test_custom_config(self):
        """Test custom configuration values."""
        backend_config = SnsSqsClientConfig(
            sns_topic_arn="arn:aws:sns:us-east-1:123:topic",
            sqs_queue_url="https://sqs.example.com/queue",
        )
        
        config = ProxyConfig(
            host="0.0.0.0",
            port=9090,
            server_path="/proxy",
            backend_transport="sns_sqs",
            backend_config=backend_config,
            session_timeout=600.0,
            max_sessions=50,
            stateless=True,
            cors_origins=["http://localhost:3000"],
            auth_enabled=True,
            auth_token="secret-token",
        )
        
        assert config.host == "0.0.0.0"
        assert config.port == 9090
        assert config.server_path == "/proxy"
        assert config.backend_transport == "sns_sqs"
        assert config.backend_config == backend_config
        assert config.session_timeout == 600.0
        assert config.max_sessions == 50
        assert config.stateless is True
        assert config.cors_origins == ["http://localhost:3000"]
        assert config.auth_enabled is True
        assert config.auth_token == "secret-token"


class TestUtilityFunctions:
    """Test utility functions."""
    
    def test_generate_session_id(self):
        """Test session ID generation."""
        # Generate multiple IDs
        ids = [generate_session_id() for _ in range(10)]
        
        # All should be unique
        assert len(set(ids)) == 10
        
        # All should be valid UUIDs
        for session_id in ids:
            assert len(session_id) == 36  # UUID string length
            assert session_id.count("-") == 4  # UUID format
            
    def test_validate_auth_token(self):
        """Test auth token validation."""
        # No auth required
        assert validate_auth_token(None, None) is True
        assert validate_auth_token("any-token", None) is True
        
        # Auth required, no token provided
        assert validate_auth_token(None, "expected-token") is False
        assert validate_auth_token("", "expected-token") is False
        
        # Auth required, wrong token
        assert validate_auth_token("wrong-token", "expected-token") is False
        
        # Auth required, correct token
        assert validate_auth_token("expected-token", "expected-token") is True