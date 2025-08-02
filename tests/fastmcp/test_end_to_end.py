"""End-to-end tests for FastMCP integration with asyncmcp transports."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp import FastMCP
from mcp.types import TextContent

from asyncmcp.fastmcp import AsyncMCPTransport, create_fastmcp_client, run_fastmcp_server
from asyncmcp.sqs.utils import SqsClientConfig, SqsServerConfig


@pytest.fixture
def mock_boto3_clients():
    """Create mock boto3 clients for testing."""
    sqs_client = MagicMock()
    sns_client = MagicMock()

    # Mock SQS responses
    sqs_client.receive_message = MagicMock(return_value={"Messages": []})
    sqs_client.send_message = MagicMock(return_value={"MessageId": "test-message-id"})
    sqs_client.delete_message = MagicMock()

    # Mock SNS responses
    sns_client.publish = MagicMock(return_value={"MessageId": "test-message-id"})

    return {"sqs_client": sqs_client, "sns_client": sns_client}


@pytest.fixture
def example_fastmcp_server():
    """Create an example FastMCP server."""
    mcp = FastMCP("example-server", version="1.0.0")

    @mcp.tool()
    def calculate(operation: str, a: float, b: float) -> float:
        """Perform a calculation."""
        if operation == "add":
            return a + b
        elif operation == "multiply":
            return a * b
        elif operation == "subtract":
            return a - b
        elif operation == "divide":
            if b == 0:
                raise ValueError("Cannot divide by zero")
            return a / b
        else:
            raise ValueError(f"Unknown operation: {operation}")

    @mcp.resource("data://calculations/history.json")
    def get_calculation_history() -> str:
        """Get calculation history."""
        return '{"calculations": [{"op": "add", "a": 1, "b": 2, "result": 3}]}'

    @mcp.prompt()
    def calculation_prompt(operation: str = "add") -> str:
        """Generate a calculation prompt."""
        return f"Please perform the following calculation: {operation}"

    return mcp


class TestFastMCPEndToEnd:
    """End-to-end tests for FastMCP with asyncmcp transports."""

    @pytest.mark.anyio
    async def test_transport_creation_and_connection(self, mock_boto3_clients):
        """Test creating and connecting through AsyncMCPTransport."""
        config = SqsClientConfig(
            read_queue_url="https://sqs.us-east-1.amazonaws.com/123/read",
            response_queue_url="https://sqs.us-east-1.amazonaws.com/123/response",
        )

        transport = AsyncMCPTransport(
            transport_type="sqs",
            config=config,
            low_level_clients=mock_boto3_clients,
        )

        # Mock the underlying SQS client to simulate a connection
        with patch("asyncmcp.sqs.client.sqs_client") as mock_sqs_client:
            mock_read_stream = AsyncMock()
            mock_write_stream = AsyncMock()

            # Make the context manager return the streams
            mock_sqs_client.return_value.__aenter__.return_value = (mock_read_stream, mock_write_stream)

            # Test that we can create a session through the transport
            async with transport.connect_session() as session:
                assert session is not None
                # The session was created successfully

    @pytest.mark.anyio
    async def test_client_creation_with_transport(self, mock_boto3_clients):
        """Test creating a FastMCP client with asyncmcp transport."""
        config = SqsClientConfig(
            read_queue_url="https://sqs.us-east-1.amazonaws.com/123/read",
            response_queue_url="https://sqs.us-east-1.amazonaws.com/123/response",
        )

        client = create_fastmcp_client(
            transport_type="sqs",
            config=config,
            low_level_clients=mock_boto3_clients,
        )

        # Verify the client was created with the correct transport
        assert client.transport is not None
        assert isinstance(client.transport, AsyncMCPTransport)
        assert client.transport.transport_type == "sqs"

    @pytest.mark.anyio
    async def test_server_lifecycle_management(self, example_fastmcp_server, mock_boto3_clients):
        """Test that the server lifecycle is properly managed."""
        server_config = SqsServerConfig(read_queue_url="https://sqs.us-east-1.amazonaws.com/123/requests")

        client_config = SqsClientConfig(
            read_queue_url="https://sqs.us-east-1.amazonaws.com/123/requests",
            response_queue_url="https://sqs.us-east-1.amazonaws.com/123/responses",
        )

        # Create a task to run the server
        server_started = asyncio.Event()
        server_task = None

        async def run_server():
            server_started.set()
            await run_fastmcp_server(
                mcp=example_fastmcp_server,
                transport_type="sqs",
                server_config=server_config,
                client_config=client_config,
                low_level_clients=mock_boto3_clients,
            )

        try:
            # Start the server in a background task
            server_task = asyncio.create_task(run_server())

            # Wait for server to start
            await asyncio.wait_for(server_started.wait(), timeout=1.0)

            # Server should be running
            assert not server_task.done()

        finally:
            # Clean up the server task
            if server_task and not server_task.done():
                server_task.cancel()
                try:
                    await server_task
                except asyncio.CancelledError:
                    pass

    @pytest.mark.anyio
    async def test_multiple_transport_types(self):
        """Test that different transport types can be created."""
        # Test SQS transport
        sqs_config = SqsClientConfig(
            read_queue_url="https://sqs.us-east-1.amazonaws.com/123/read",
            response_queue_url="https://sqs.us-east-1.amazonaws.com/123/response",
        )
        sqs_transport = AsyncMCPTransport(
            transport_type="sqs",
            config=sqs_config,
            low_level_clients={"sqs_client": MagicMock()},
        )
        assert sqs_transport.transport_type == "sqs"

        # Test Webhook transport
        from asyncmcp.webhook.utils import WebhookClientConfig

        webhook_config = WebhookClientConfig(server_url="http://localhost:8000")
        webhook_transport = AsyncMCPTransport(
            transport_type="webhook",
            config=webhook_config,
            low_level_clients={},
        )
        assert webhook_transport.transport_type == "webhook"

        # Test unsupported transport
        with pytest.raises(ValueError, match="Unsupported transport type"):
            AsyncMCPTransport(
                transport_type="unsupported",  # type: ignore
                config=sqs_config,
                low_level_clients={},
            )
