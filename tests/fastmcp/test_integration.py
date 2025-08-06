"""Integration tests for FastMCP with asyncmcp transports."""

import pytest
from unittest.mock import MagicMock
from fastmcp import FastMCP
from mcp.types import TextContent, TextResourceContents

from asyncmcp.fastmcp import AsyncMCPTransport, create_fastmcp_client
from asyncmcp.sqs.utils import SqsClientConfig


@pytest.fixture
def sqs_client_config():
    """Create a test SQS client configuration."""
    return SqsClientConfig(
        read_queue_url="https://sqs.us-east-1.amazonaws.com/123456789012/test-read",
        response_queue_url="https://sqs.us-east-1.amazonaws.com/123456789012/test-response",
        client_id="test-client",
    )


@pytest.fixture
def mock_sqs_client():
    """Create a mock SQS client."""
    return MagicMock()


@pytest.fixture
def simple_fastmcp_server():
    """Create a simple FastMCP server for testing."""
    mcp = FastMCP("test-server")

    @mcp.tool()
    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    @mcp.tool()
    async def multiply(x: float, y: float) -> float:
        """Multiply two numbers."""
        return x * y

    @mcp.resource("test://example/data.txt")
    def get_data() -> str:
        """Get test data."""
        return "This is test data from FastMCP!"

    @mcp.prompt()
    def test_prompt(message: str = "Hello") -> str:
        """Generate a test prompt."""
        return f"Test prompt: {message}"

    return mcp


class TestAsyncMCPTransport:
    """Test the AsyncMCPTransport class."""

    def test_transport_creation(self, sqs_client_config, mock_sqs_client):
        """Test creating an AsyncMCPTransport instance."""
        transport = AsyncMCPTransport(
            transport_type="sqs",
            config=sqs_client_config,
            low_level_clients={"sqs_client": mock_sqs_client},
        )

        assert transport.transport_type == "sqs"
        assert transport.config == sqs_client_config
        assert "sqs_client" in transport.low_level_clients
        assert str(transport) == "<AsyncMCPTransport(type='sqs')>"

    def test_unsupported_transport_type(self, sqs_client_config):
        """Test that unsupported transport types raise an error."""
        with pytest.raises(ValueError, match="Unsupported transport type: invalid"):
            AsyncMCPTransport(
                transport_type="invalid",
                config=sqs_client_config,
                low_level_clients={},
            )

    @pytest.mark.anyio
    async def test_transport_session_connection(self, sqs_client_config, mock_sqs_client):
        """Test connecting a session through the transport."""
        transport = AsyncMCPTransport(
            transport_type="sqs",
            config=sqs_client_config,
            low_level_clients={"sqs_client": mock_sqs_client},
        )

        # This would normally connect to the actual transport
        # For testing, we'd need to mock the underlying asyncmcp client
        # The actual integration test would be in the full integration suite


class TestFastMCPClient:
    """Test FastMCP client creation and usage."""

    def test_create_client(self, sqs_client_config, mock_sqs_client):
        """Test creating a FastMCP client with asyncmcp transport."""
        client = create_fastmcp_client(
            transport_type="sqs",
            config=sqs_client_config,
            low_level_clients={"sqs_client": mock_sqs_client},
        )

        # Verify client is created with correct transport
        assert client.transport is not None
        assert isinstance(client.transport, AsyncMCPTransport)


@pytest.mark.anyio
class TestFastMCPIntegration:
    """Full integration tests with FastMCP servers and clients."""

    async def test_in_memory_fastmcp_communication(self, simple_fastmcp_server):
        """Test FastMCP server and client communication using in-memory transport."""
        # Create a client directly connected to the server (in-memory)
        from fastmcp import Client

        client = Client(transport=simple_fastmcp_server)

        async with client:
            # Test tool listing
            tools = await client.list_tools()
            assert len(tools) == 2
            tool_names = [t.name for t in tools]
            assert "add" in tool_names
            assert "multiply" in tool_names

            # Test tool calling
            result = await client.call_tool("add", {"a": 5, "b": 3})
            assert len(result.content) == 1
            assert isinstance(result.content[0], TextContent)
            assert result.content[0].text == "8"

            # Test async tool
            result = await client.call_tool("multiply", {"x": 4.5, "y": 2.0})
            assert result.content[0].text == "9.0"

            # Test resource listing
            resources = await client.list_resources()
            assert len(resources) == 1
            assert str(resources[0].uri) == "test://example/data.txt"

            # Test resource reading
            content = await client.read_resource("test://example/data.txt")
            # FastMCP client returns a list of resource contents
            assert len(content) == 1
            assert isinstance(content[0], TextResourceContents)
            assert content[0].text == "This is test data from FastMCP!"

            # Test prompt listing
            prompts = await client.list_prompts()
            assert len(prompts) == 1
            assert prompts[0].name == "test_prompt"

            # Test prompt getting
            prompt_result = await client.get_prompt("test_prompt", {"message": "Testing"})
            assert len(prompt_result.messages) == 1
            assert prompt_result.messages[0].content.text == "Test prompt: Testing"
