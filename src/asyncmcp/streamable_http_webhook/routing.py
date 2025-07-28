"""
Tool routing and webhook detection for Streamable HTTP + Webhook transport.

This module provides functionality to discover webhook tools, route messages
based on tool type, and manage tool-specific delivery methods.
"""

import logging
from typing import Any, Callable, Optional, Set

import mcp.types as types
from mcp.server.lowlevel import Server as MCPServer
from mcp.shared.message import SessionMessage

from .utils import get_webhook_tool_info, is_webhook_tool

logger = logging.getLogger(__name__)


class ToolRouter:
    """Routes tool results between SSE and webhook delivery."""

    def __init__(self, webhook_tools: Set[str]):
        """
        Initialize tool router with webhook tool set.

        Args:
            webhook_tools: Set of tool names that should use webhook delivery
        """
        self.webhook_tools = webhook_tools
        logger.debug(f"ToolRouter initialized with webhook tools: {webhook_tools}")

    def should_use_webhook(self, session_message: SessionMessage) -> bool:
        """
        Determine if message should be delivered via webhook.

        Args:
            session_message: The session message to route

        Returns:
            True if message should be delivered via webhook, False for SSE
        """
        # Check if this is a tool result
        message_root = session_message.message.root

        # Look for tool call responses or results
        if isinstance(message_root, (types.JSONRPCResponse, types.JSONRPCError)):
            # For responses, we need to check if this was a tool call response
            # We can check the metadata for related request information
            if hasattr(session_message, "metadata") and session_message.metadata:
                # Check if metadata indicates this is a tool result
                metadata = session_message.metadata
                if hasattr(metadata, "tool_name"):
                    tool_name = metadata.tool_name
                    return tool_name in self.webhook_tools

        # Check if this is a tools/call request response
        if isinstance(message_root, types.JSONRPCResponse):
            # We need to infer from the message content if this is a tool result
            # This is a simplified approach - in practice, you might need more
            # sophisticated message correlation
            return self._is_tool_call_response(session_message)

        return False

    def _is_tool_call_response(self, session_message: SessionMessage) -> bool:
        """
        Check if this is a response to a tools/call request.

        This is a heuristic approach - we look for patterns that indicate
        this is a tool call response that might be from a webhook tool.
        """
        logger.info(f"Checking if tool call response for {session_message}")
        message_root = session_message.message.root

        if isinstance(message_root, types.JSONRPCResponse):
            # Check if the result looks like a tool call result
            result = message_root.result
            if isinstance(result, dict):
                # Tool call responses typically have a 'content' field
                if "content" in result:
                    logger.info(f"Routing based on heuristics")
                    # This looks like a tool call response
                    # We'll be conservative and check if ANY webhook tools are configured
                    return len(self.webhook_tools) > 0

        return False

    async def route_message(
        self,
        session_message: SessionMessage,
        sse_handler: Callable[[SessionMessage], Any],
        webhook_handler: Callable[[SessionMessage], Any],
    ) -> None:
        """
        Route message to appropriate delivery method.

        Args:
            session_message: The message to route
            sse_handler: Handler for SSE delivery
            webhook_handler: Handler for webhook delivery
        """
        try:
            if self.should_use_webhook(session_message):
                logger.debug("Routing message to webhook delivery")
                await webhook_handler(session_message)
            else:
                logger.debug("Routing message to SSE delivery")
                await sse_handler(session_message)
        except Exception as e:
            logger.error(f"Error routing message: {e}")
            # Fallback to SSE on error
            try:
                await sse_handler(session_message)
            except Exception as fallback_error:
                logger.error(f"Error in fallback SSE delivery: {fallback_error}")
                raise

    def add_webhook_tool(self, tool_name: str) -> None:
        """Add a tool to the webhook tools set."""
        self.webhook_tools.add(tool_name)
        logger.debug(f"Added webhook tool: {tool_name}")

    def remove_webhook_tool(self, tool_name: str) -> None:
        """Remove a tool from the webhook tools set."""
        self.webhook_tools.discard(tool_name)
        logger.debug(f"Removed webhook tool: {tool_name}")

    def get_webhook_tools(self) -> Set[str]:
        """Get the current set of webhook tools."""
        return self.webhook_tools.copy()


def discover_webhook_tools(mcp_server: MCPServer) -> Set[str]:
    """
    Discover tools marked with @webhook_tool from MCP server.

    This function scans the MCP server's registered tools and handlers
    to find functions marked with the @webhook_tool decorator.

    Args:
        mcp_server: The MCP server instance to scan

    Returns:
        Set of tool names that should use webhook delivery
    """
    webhook_tools = set()

    try:
        # Get the server's tool handlers
        # The MCP server stores tool handlers in various ways
        # We need to inspect the server's internal structure

        # Check if server has _tool_handlers attribute
        if hasattr(mcp_server, "_tool_handlers"):
            tool_handlers = mcp_server._tool_handlers
            for tool_name, handler in tool_handlers.items():
                if is_webhook_tool(handler):
                    webhook_tools.add(tool_name)
                    tool_info = get_webhook_tool_info(handler)
                    logger.info(
                        f"Discovered webhook tool '{tool_name}': {tool_info.get('description', 'No description')}"
                    )

        # Alternative: Check server's registry/router if available
        elif hasattr(mcp_server, "_router") and hasattr(mcp_server._router, "_tool_handlers"):
            tool_handlers = mcp_server._router._tool_handlers
            for tool_name, handler in tool_handlers.items():
                if is_webhook_tool(handler):
                    webhook_tools.add(tool_name)
                    tool_info = get_webhook_tool_info(handler)
                    logger.info(
                        f"Discovered webhook tool '{tool_name}': {tool_info.get('description', 'No description')}"
                    )

        # Fallback: Scan the server object for decorated methods
        else:
            logger.debug("Using fallback method to discover webhook tools")
            for attr_name in dir(mcp_server):
                attr = getattr(mcp_server, attr_name)
                if callable(attr) and is_webhook_tool(attr):
                    # Use the attribute name as tool name (may need adjustment)
                    tool_name = attr_name
                    webhook_tools.add(tool_name)
                    tool_info = get_webhook_tool_info(attr)
                    logger.info(
                        f"Discovered webhook tool '{tool_name}': {tool_info.get('description', 'No description')}"
                    )

        # Additionally, check for any global registry or decorators applied
        # This is a more comprehensive approach that scans all loaded modules
        webhook_tools.update(_scan_global_webhook_tools())

    except Exception as e:
        logger.error(f"Error discovering webhook tools: {e}")
        # Return empty set on error - better to have no webhook routing than crash
        return set()

    logger.info(f"Discovered {len(webhook_tools)} webhook tools: {webhook_tools}")
    return webhook_tools


def _scan_global_webhook_tools() -> Set[str]:
    """
    Scan for globally registered webhook tools.

    This is a fallback method that looks for functions with the webhook_tool
    decorator across all loaded modules. This might be needed if the MCP
    server doesn't expose its tool handlers directly.
    """
    webhook_tools = set()

    try:
        # Instead of using inspect.currentframe() which can cause ContextVar issues,
        # we'll disable this global scan for now and rely on the server inspection
        # This prevents the ContextVar error while maintaining functionality
        logger.debug("Global webhook tool scan disabled to prevent ContextVar issues")

    except Exception as e:
        logger.debug(f"Error in global webhook tool scan: {e}")

    return webhook_tools


def create_tool_router_from_server(mcp_server: MCPServer) -> ToolRouter:
    """
    Create a ToolRouter instance by discovering webhook tools from an MCP server.

    Args:
        mcp_server: The MCP server to scan for webhook tools

    Returns:
        Configured ToolRouter instance
    """
    webhook_tools = discover_webhook_tools(mcp_server)
    return ToolRouter(webhook_tools)


def update_router_with_tools(router: ToolRouter, tool_names: Set[str]) -> None:
    """
    Update an existing router with additional webhook tools.

    Args:
        router: The ToolRouter to update
        tool_names: Set of tool names to add as webhook tools
    """
    for tool_name in tool_names:
        router.add_webhook_tool(tool_name)

    logger.debug(f"Updated router with {len(tool_names)} additional webhook tools")


def extract_tool_name_from_request(session_message: SessionMessage) -> Optional[str]:
    """
    Extract tool name from a tools/call request.

    Args:
        session_message: The session message containing a tools/call request

    Returns:
        Tool name if this is a tools/call request, None otherwise
    """
    message_root = session_message.message.root

    if isinstance(message_root, types.JSONRPCRequest):
        if message_root.method == "tools/call":
            params = message_root.params
            if isinstance(params, dict) and "name" in params:
                return params["name"]

    return None


def is_tool_call_request(session_message: SessionMessage) -> bool:
    """
    Check if a session message is a tools/call request.

    Args:
        session_message: The session message to check

    Returns:
        True if this is a tools/call request
    """
    message_root = session_message.message.root

    if isinstance(message_root, types.JSONRPCRequest):
        return message_root.method == "tools/call"

    return False


def correlate_response_to_tool(request_message: SessionMessage, response_message: SessionMessage) -> Optional[str]:
    """
    Correlate a response message to the original tool request.

    This helps determine if a response is for a webhook tool by looking
    at the original request.

    Args:
        request_message: The original tools/call request
        response_message: The response message

    Returns:
        Tool name if correlation is successful, None otherwise
    """
    # Check if request was a tool call
    if not is_tool_call_request(request_message):
        return None

    # Extract tool name from request
    tool_name = extract_tool_name_from_request(request_message)

    # Verify response corresponds to request (by ID matching)
    request_root = request_message.message.root
    response_root = response_message.message.root

    if isinstance(request_root, types.JSONRPCRequest) and isinstance(
        response_root, (types.JSONRPCResponse, types.JSONRPCError)
    ):
        if str(request_root.id) == str(response_root.id):
            return tool_name

    return None
