"""Tests for proxy client."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from asyncmcp.proxy.client import ProxyClient, create_backend_client
from asyncmcp.sqs.utils import SqsClientConfig
from asyncmcp.sns_sqs.utils import SnsSqsClientConfig
from asyncmcp.webhook.utils import WebhookClientConfig
from asyncmcp.streamable_http_webhook.utils import StreamableHTTPWebhookClientConfig


class TestProxyClient:
    """Test ProxyClient class."""
    
    @pytest.fixture
    def mock_sqs_client(self):
        """Create a mock SQS client."""
        return MagicMock()
        
    @pytest.fixture
    def mock_sns_client(self):
        """Create a mock SNS client."""
        return MagicMock()
        
    def test_init(self):
        """Test ProxyClient initialization."""
        config = SqsClientConfig(
            read_queue_url="https://sqs.example.com/queue",
            response_queue_url="https://sqs.example.com/response",
        )
        
        client = ProxyClient(
            transport_type="sqs",
            config=config,
            low_level_clients={"sqs_client": MagicMock()},
            session_id="test-session",
        )
        
        assert client.transport_type == "sqs"
        assert client.config == config
        assert "sqs_client" in client.low_level_clients
        assert client.session_id == "test-session"
        
    @pytest.mark.anyio
    async def test_connect_sqs(self, mock_sqs_client):
        """Test connecting to SQS backend."""
        config = SqsClientConfig(
            read_queue_url="https://sqs.example.com/queue",
            response_queue_url="https://sqs.example.com/response",
        )
        
        client = ProxyClient(
            transport_type="sqs",
            config=config,
            low_level_clients={"sqs_client": mock_sqs_client},
        )
        
        # Mock the sqs_client context manager
        mock_streams = (AsyncMock(), AsyncMock())
        with patch("asyncmcp.proxy.client.sqs_client") as mock_sqs:
            mock_sqs.return_value.__aenter__.return_value = mock_streams
            
            async with client.connect() as (read_stream, write_stream):
                assert read_stream == mock_streams[0]
                assert write_stream == mock_streams[1]
                
            mock_sqs.assert_called_once_with(
                config=config,
                sqs_client=mock_sqs_client,
            )
            
    @pytest.mark.anyio
    async def test_connect_sns_sqs(self, mock_sqs_client, mock_sns_client):
        """Test connecting to SNS+SQS backend."""
        config = SnsSqsClientConfig(
            sns_topic_arn="arn:aws:sns:us-east-1:123:topic",
            sqs_queue_url="https://sqs.example.com/queue",
        )
        
        client = ProxyClient(
            transport_type="sns_sqs",
            config=config,
            low_level_clients={
                "sqs_client": mock_sqs_client,
                "sns_client": mock_sns_client,
            },
        )
        
        # Mock the sns_sqs_client context manager
        mock_streams = (AsyncMock(), AsyncMock())
        with patch("asyncmcp.proxy.client.sns_sqs_client") as mock_sns_sqs:
            mock_sns_sqs.return_value.__aenter__.return_value = mock_streams
            
            async with client.connect() as (read_stream, write_stream):
                assert read_stream == mock_streams[0]
                assert write_stream == mock_streams[1]
                
            mock_sns_sqs.assert_called_once_with(
                config=config,
                sqs_client=mock_sqs_client,
                sns_client=mock_sns_client,
                client_topic_arn=config.sns_topic_arn,
            )
            
    @pytest.mark.anyio
    async def test_connect_webhook(self):
        """Test connecting to Webhook backend."""
        config = WebhookClientConfig(server_url="http://example.com/mcp")
        
        client = ProxyClient(
            transport_type="webhook",
            config=config,
        )
        
        # Mock the webhook_client context manager
        mock_streams = (AsyncMock(), AsyncMock(), AsyncMock())  # 3-tuple
        with patch("asyncmcp.proxy.client.webhook_client") as mock_webhook:
            mock_webhook.return_value.__aenter__.return_value = mock_streams
            
            async with client.connect() as (read_stream, write_stream):
                assert read_stream == mock_streams[0]
                assert write_stream == mock_streams[1]
                
            mock_webhook.assert_called_once_with(config=config)
            
    @pytest.mark.anyio
    async def test_connect_unsupported_transport(self):
        """Test connecting with unsupported transport type."""
        client = ProxyClient(
            transport_type="unsupported",  # type: ignore
            config=SqsClientConfig(
                read_queue_url="https://sqs.example.com/queue",
                response_queue_url="https://sqs.example.com/response",
            ),
        )
        
        with pytest.raises(ValueError, match="Unsupported transport type"):
            async with client.connect():
                pass
                
    @pytest.mark.anyio
    async def test_connect_missing_client(self):
        """Test connecting without required low-level client."""
        config = SqsClientConfig(
            read_queue_url="https://sqs.example.com/queue",
            response_queue_url="https://sqs.example.com/response",
        )
        
        client = ProxyClient(
            transport_type="sqs",
            config=config,
            low_level_clients={},  # Missing sqs_client
        )
        
        with pytest.raises(ValueError, match="SQS transport requires 'sqs_client'"):
            async with client.connect():
                pass
                
    @pytest.mark.anyio
    async def test_connect_wrong_config_type(self, mock_sqs_client):
        """Test connecting with wrong config type."""
        config = WebhookClientConfig(server_url="http://example.com")  # Wrong type for SQS
        
        client = ProxyClient(
            transport_type="sqs",
            config=config,
            low_level_clients={"sqs_client": mock_sqs_client},
        )
        
        with pytest.raises(TypeError, match="Expected SqsClientConfig"):
            async with client.connect():
                pass


class TestCreateBackendClient:
    """Test create_backend_client function."""
    
    def test_create_backend_client(self):
        """Test creating a backend client."""
        config = SqsClientConfig(
            read_queue_url="https://sqs.example.com/queue",
            response_queue_url="https://sqs.example.com/response",
        )
        
        client = create_backend_client(
            transport_type="sqs",
            config=config,
            low_level_clients={"sqs_client": MagicMock()},
            session_id="test-session",
        )
        
        assert isinstance(client, ProxyClient)
        assert client.transport_type == "sqs"
        assert client.config == config
        assert client.session_id == "test-session"