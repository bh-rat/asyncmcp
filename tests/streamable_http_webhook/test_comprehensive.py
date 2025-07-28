"""
Comprehensive tests for StreamableHTTP + Webhook transport.

This test module focuses on testing the core functionality of the StreamableHTTP + Webhook
transport implementation, including configuration, tool routing, and basic operations.
"""

from asyncmcp.streamable_http_webhook.routing import ToolRouter
from asyncmcp.streamable_http_webhook.utils import (
    StreamableHTTPWebhookClientConfig,
    StreamableHTTPWebhookConfig,
    get_webhook_tool_info,
    is_webhook_tool,
    validate_session_id,
    webhook_tool,
)


class TestStreamableHTTPWebhookConfig:
    """Test StreamableHTTP + Webhook configuration classes."""

    def test_server_config_defaults(self):
        """Test server config with default values."""
        config = StreamableHTTPWebhookConfig()
        assert config.json_response is False
        assert config.timeout_seconds == 30.0
        assert config.webhook_timeout == 30.0
        assert config.webhook_max_retries == 1

    def test_server_config_custom_values(self):
        """Test server config with custom values."""
        config = StreamableHTTPWebhookConfig(
            json_response=True, timeout_seconds=60.0, webhook_timeout=45.0, webhook_max_retries=3
        )
        assert config.json_response is True
        assert config.timeout_seconds == 60.0
        assert config.webhook_timeout == 45.0
        assert config.webhook_max_retries == 3

    def test_client_config_initialization(self):
        """Test client config initialization."""
        config = StreamableHTTPWebhookClientConfig(
            server_url="http://localhost:8000/mcp",
            webhook_url="http://localhost:8001/webhook",
            timeout_seconds=30.0,
            max_retries=1,
        )
        assert config.server_url == "http://localhost:8000/mcp"
        assert config.webhook_url == "http://localhost:8001/webhook"
        assert config.timeout_seconds == 30.0
        assert config.max_retries == 1
        assert config.client_id is not None  # Auto-generated UUID

    def test_client_config_defaults(self):
        """Test client config with default values."""
        config = StreamableHTTPWebhookClientConfig()
        assert config.server_url == "http://localhost:8000/mcp"
        assert config.webhook_url == "http://localhost:8001/webhook"
        assert config.timeout_seconds == 30.0
        assert config.max_retries == 1


class TestWebhookToolDecorator:
    """Test webhook tool decorator functionality."""

    def test_webhook_tool_decorator_basic(self):
        """Test basic webhook_tool decorator usage."""

        @webhook_tool(description="Test webhook tool", tool_name="test_tool")
        async def test_function(arg: str):
            return f"Result: {arg}"

        assert is_webhook_tool(test_function) is True
        assert test_function._webhook_tool_name == "test_tool"
        assert test_function._webhook_description == "Test webhook tool"

    def test_webhook_tool_decorator_without_tool_name(self):
        """Test webhook_tool decorator using function name."""

        @webhook_tool(description="Function name tool")
        async def my_webhook_function():
            return "result"

        assert is_webhook_tool(my_webhook_function) is True
        assert my_webhook_function._webhook_tool_name == "my_webhook_function"

    def test_webhook_tool_decorator_preserves_function(self):
        """Test that decorator preserves original function."""

        @webhook_tool(description="Test tool", tool_name="preserve_test")
        async def original_function(x: int, y: int):
            """Original docstring."""
            return x + y

        # Function should still be callable and work correctly
        assert callable(original_function)
        assert is_webhook_tool(original_function) is True

    def test_get_webhook_tool_info(self):
        """Test extracting webhook tool information."""

        @webhook_tool(description="Info test", tool_name="info_tool", category="test")
        async def info_function():
            pass

        info = get_webhook_tool_info(info_function)
        assert info["description"] == "Info test"
        assert info["metadata"]["category"] == "test"

    def test_non_webhook_tool(self):
        """Test that regular functions are not webhook tools."""

        async def regular_function():
            return "not a webhook tool"

        assert is_webhook_tool(regular_function) is False
        assert get_webhook_tool_info(regular_function) == {}


class TestToolRouter:
    """Test ToolRouter functionality."""

    def test_router_initialization(self):
        """Test ToolRouter initialization."""
        webhook_tools = {"tool1", "tool2"}
        router = ToolRouter(webhook_tools)
        assert router.webhook_tools == webhook_tools

    def test_add_webhook_tool(self):
        """Test adding webhook tools."""
        router = ToolRouter(set())
        router.add_webhook_tool("new_tool")
        assert "new_tool" in router.webhook_tools

    def test_remove_webhook_tool(self):
        """Test removing webhook tools."""
        router = ToolRouter({"tool1", "tool2"})
        router.remove_webhook_tool("tool1")
        assert "tool1" not in router.webhook_tools
        assert "tool2" in router.webhook_tools

    def test_get_webhook_tools(self):
        """Test getting webhook tools copy."""
        original_tools = {"tool1", "tool2"}
        router = ToolRouter(original_tools)
        copied_tools = router.get_webhook_tools()

        # Should be equal but different objects
        assert copied_tools == original_tools
        assert copied_tools is not router.webhook_tools

    def test_router_with_empty_tools(self):
        """Test router with empty webhook tools set."""
        router = ToolRouter(set())
        assert len(router.webhook_tools) == 0
        assert router.get_webhook_tools() == set()


class TestUtilityFunctions:
    """Test utility functions."""

    def test_validate_session_id_valid(self):
        """Test session ID validation with valid IDs."""
        assert validate_session_id("valid-session-123") is True
        assert validate_session_id("another_valid_id") is True
        assert validate_session_id("simple123") is True

    def test_validate_session_id_invalid(self):
        """Test session ID validation with invalid IDs."""
        assert validate_session_id(None) is False
        assert validate_session_id("") is False
        assert validate_session_id("invalid id with spaces") is False
        assert validate_session_id("id\nwith\nnewlines") is False

    def test_validate_session_id_edge_cases(self):
        """Test session ID validation edge cases."""
        # Test minimum valid character
        assert validate_session_id("!") is True  # ASCII 33
        # Test maximum valid character
        assert validate_session_id("~") is True  # ASCII 126
        # Test just outside valid range
        assert validate_session_id(" ") is False  # ASCII 32 (space)
        assert validate_session_id("\x7f") is False  # ASCII 127 (DEL)


class TestTransportIntegration:
    """Test high-level transport integration."""

    def test_streamable_http_webhook_client_config_integration(self):
        """Test that client config works with expected transport parameters."""
        config = StreamableHTTPWebhookClientConfig(
            server_url="https://api.example.com/mcp",
            webhook_url="https://webhook.example.com/callback",
            timeout_seconds=45.0,
            max_retries=2,
        )

        # Verify all expected attributes are present and correct
        assert config.server_url == "https://api.example.com/mcp"
        assert config.webhook_url == "https://webhook.example.com/callback"
        assert config.timeout_seconds == 45.0
        assert config.max_retries == 2
        assert isinstance(config.client_id, str)
        assert len(config.client_id) > 0

    def test_webhook_tool_discovery_pattern(self):
        """Test the pattern used for webhook tool discovery."""

        @webhook_tool(description="Async analysis tool", tool_name="analyze_data")
        async def analyze_data_webhook(data: str):
            """This tool should be discoverable by introspection."""
            return f"Analyzed: {data}"

        @webhook_tool(description="Batch processing tool")
        async def batch_process():
            """This tool uses function name as tool name."""
            return "Batch processed"

        # Verify tools are properly decorated
        assert is_webhook_tool(analyze_data_webhook)
        assert is_webhook_tool(batch_process)

        # Verify tool names
        assert analyze_data_webhook._webhook_tool_name == "analyze_data"
        assert batch_process._webhook_tool_name == "batch_process"

        # Verify descriptions
        assert analyze_data_webhook._webhook_description == "Async analysis tool"
        assert batch_process._webhook_description == "Batch processing tool"

    def test_dual_transport_concept(self):
        """Test the concept of dual transport routing."""
        # This test demonstrates the core concept: some tools use webhooks, others don't
        webhook_tools = {"analyze_async", "batch_process", "long_running_task"}
        standard_tools = {"fetch_sync", "quick_calc", "server_info"}

        router = ToolRouter(webhook_tools)

        # Webhook tools should be identified correctly
        for tool in webhook_tools:
            router.add_webhook_tool(tool)

        # Verify all webhook tools are in the router
        for tool in webhook_tools:
            assert tool in router.get_webhook_tools()

        # Verify standard tools are not in webhook tools
        for tool in standard_tools:
            assert tool not in router.get_webhook_tools()

        # Verify we can add and remove tools dynamically
        router.add_webhook_tool("new_async_tool")
        assert "new_async_tool" in router.get_webhook_tools()

        router.remove_webhook_tool("new_async_tool")
        assert "new_async_tool" not in router.get_webhook_tools()
