"""
Shared fixtures and configuration for multi-transport tests.
"""

from unittest.mock import MagicMock

import pytest
from mcp.server.lowlevel import Server

from asyncmcp.multi_transport.registry import TransportRegistry, TransportType
from asyncmcp.multi_transport.routing import transport_info
from asyncmcp.multi_transport.server import MultiTransportServer


class MockTransportManager:
    """Reusable mock transport manager for tests."""

    def __init__(self, transport_type: str = "mock", is_running: bool = False):
        self.transport_type = transport_type
        self._is_running = is_running
        self.start_called = False
        self.stop_called = False
        self.start_count = 0
        self.stop_count = 0

    @property
    def is_running(self) -> bool:
        return self._is_running

    async def start(self) -> None:
        self.start_called = True
        self.start_count += 1
        self._is_running = True

    async def stop(self) -> None:
        self.stop_called = True
        self.stop_count += 1
        self._is_running = False

    def reset(self):
        """Reset mock state for reuse."""
        self.start_called = False
        self.stop_called = False
        self.start_count = 0
        self.stop_count = 0
        self._is_running = False


@pytest.fixture
def mock_mcp_server():
    """Create a mock MCP server for testing."""
    server = MagicMock(spec=Server)
    server._tool_handlers = {}
    return server


@pytest.fixture
def transport_registry(mock_mcp_server):
    """Create a TransportRegistry instance for testing."""
    return TransportRegistry(mock_mcp_server)


@pytest.fixture
def mock_transport_manager():
    """Create a mock transport manager."""
    return MockTransportManager()


@pytest.fixture
def mock_http_transport():
    """Create a mock HTTP transport manager."""
    return MockTransportManager("http")


@pytest.fixture
def mock_webhook_transport():
    """Create a mock webhook transport manager."""
    return MockTransportManager("webhook")


@pytest.fixture
def mock_sqs_transport():
    """Create a mock SQS transport manager."""
    return MockTransportManager("sqs")


@pytest.fixture
def multi_transport_server(mock_mcp_server):
    """Create a MultiTransportServer for testing."""
    return MultiTransportServer(mock_mcp_server, enable_routing_validation=False)


@pytest.fixture
def sample_decorated_tools():
    """Create sample tool functions with transport decorators."""

    @transport_info(TransportType.HTTP, description="Fast HTTP tool")
    def http_tool():
        return "http result"

    @transport_info(TransportType.WEBHOOK, description="Async webhook tool")
    def webhook_tool():
        return "webhook result"

    @transport_info(TransportType.SQS, description="Queue-based tool", priority="high")
    def sqs_tool():
        return "sqs result"

    def undecorated_tool():
        return "default result"

    return {
        "http_tool": http_tool,
        "webhook_tool": webhook_tool,
        "sqs_tool": sqs_tool,
        "undecorated_tool": undecorated_tool,
    }


@pytest.fixture
def configured_multi_server(mock_mcp_server, sample_decorated_tools):
    """Create a fully configured MultiTransportServer with tools and transports."""
    # Set up MCP server with tool handlers
    mock_mcp_server._tool_handlers = sample_decorated_tools

    # Create multi-transport server
    server = MultiTransportServer(mock_mcp_server, enable_routing_validation=False)

    # Add transports
    server.registry.register_transport(
        transport_type=TransportType.HTTP,
        transport_instance=MockTransportManager("http"),
        config={"host": "localhost", "port": 8000},
        tools=["http_tool"],
        is_default=True,
    )

    server.registry.register_transport(
        transport_type=TransportType.WEBHOOK,
        transport_instance=MockTransportManager("webhook"),
        config={"timeout": 30},
        tools=["webhook_tool"],
    )

    server.registry.register_transport(
        transport_type=TransportType.SQS,
        transport_instance=MockTransportManager("sqs"),
        config={"queue_url": "test-queue"},
        tools=["sqs_tool"],
    )

    # Discover tools to register metadata
    server._discover_tools()

    return server


@pytest.fixture
def active_transports_registry(transport_registry, mock_http_transport, mock_webhook_transport):
    """Create a registry with multiple active transports."""
    # Register transports
    transport_registry.register_transport(
        transport_type=TransportType.HTTP,
        transport_instance=mock_http_transport,
        config={"host": "localhost", "port": 8000},
        tools=["tool1", "tool2"],
        is_default=True,
    )

    transport_registry.register_transport(
        transport_type=TransportType.WEBHOOK,
        transport_instance=mock_webhook_transport,
        config={"timeout": 30},
        tools=["tool3"],
    )

    # Make transports active
    transport_registry.get_transport_info(TransportType.HTTP).is_active = True
    transport_registry.get_transport_info(TransportType.WEBHOOK).is_active = True

    return transport_registry


@pytest.fixture
def sample_tool_metadata():
    """Create sample tool metadata for testing."""
    return [
        {
            "tool_name": "fetch_data",
            "transport_type": TransportType.HTTP,
            "description": "Fast data fetcher",
            "metadata": {"timeout": 10, "cache": True},
        },
        {
            "tool_name": "process_async",
            "transport_type": TransportType.WEBHOOK,
            "description": "Long async processor",
            "metadata": {"timeout": 300, "priority": "high"},
        },
        {
            "tool_name": "queue_task",
            "transport_type": TransportType.SQS,
            "description": "Queue-based task",
            "metadata": {"retries": 3, "delay": 5},
        },
    ]


@pytest.fixture(autouse=True)
def reset_global_state():
    """Reset any global state before each test."""
    # This fixture runs automatically before each test
    # Currently there's no global state in the new system,
    # but this provides a safety net
    yield
    # Cleanup after test if needed


# Pytest configuration for async tests
@pytest.fixture(scope="session")
def anyio_backend():
    """Configure anyio backend for async tests."""
    return "asyncio"


# Helper functions for tests
def create_mock_tool_handler(name: str, transport_type: TransportType = None, **metadata):
    """Helper to create mock tool handlers with optional transport info."""

    def handler():
        return f"{name} result"

    handler.__name__ = name

    if transport_type:
        handler._transport_type = transport_type
        handler._transport_description = metadata.get("description")
        handler._transport_metadata = {k: v for k, v in metadata.items() if k != "description"}

    return handler


def assert_transport_active(registry: TransportRegistry, transport_type: TransportType):
    """Helper assertion to check if a transport is active."""
    transport_info = registry.get_transport_info(transport_type)
    assert transport_info is not None
    assert transport_info.is_active


def assert_transport_inactive(registry: TransportRegistry, transport_type: TransportType):
    """Helper assertion to check if a transport is inactive."""
    transport_info = registry.get_transport_info(transport_type)
    assert transport_info is not None
    assert not transport_info.is_active


def assert_tool_routed_to(
    registry: TransportRegistry, tool_name: str, expected_transport: TransportType, available_transports: list = None
):
    """Helper assertion to check tool routing."""
    if available_transports is None:
        available_transports = list(registry.get_active_transports().keys())

    decision = registry.make_routing_decision(tool_name, available_transports)
    assert decision == expected_transport
