"""
Tool routing and webhook detection for Streamable HTTP + Webhook transport.

This module provides functionality to discover webhook tools, route messages
based on tool type, and manage tool-specific delivery methods.
"""

import logging
from typing import Any, Callable, Optional, Set

import mcp.types as types
from mcp.shared.message import SessionMessage

logger = logging.getLogger(__name__)


class ToolRouter:
    """Routes tool results between SSE and webhook delivery."""

    def __init__(self, webhook_tools: Set[str], response_type_lookup: Optional[Callable[[str], Optional[str]]] = None):
        """Initialize tool router with webhook tool set."""
        self.webhook_tools = webhook_tools
        self.response_type_lookup = response_type_lookup
        logger.debug(f"ToolRouter initialized with webhook tools: {webhook_tools}")

    def should_use_webhook(self, session_message: SessionMessage) -> bool:
        """Determine if message should be delivered via webhook."""
        message_root = session_message.message.root

        # For responses, look up the response type using the request ID
        if isinstance(message_root, (types.JSONRPCResponse, types.JSONRPCError)):
            if self.response_type_lookup:
                request_id = str(message_root.id)
                response_type = self.response_type_lookup(request_id)
                return response_type == "webhook"

        # Fallback: check if this is a tool result using metadata
        if hasattr(session_message, "metadata") and session_message.metadata:
            # Check if metadata indicates this is a tool result
            metadata = session_message.metadata
            if hasattr(metadata, "tool_name"):
                tool_name = metadata.tool_name
                return tool_name in self.webhook_tools

        return False

    async def route_message(
        self,
        session_message: SessionMessage,
        sse_handler: Callable[[SessionMessage], Any],
        webhook_handler: Callable[[SessionMessage], Any],
    ) -> None:
        """Route message to appropriate delivery method."""
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


def extract_tool_name_from_request(session_message: SessionMessage) -> Optional[str]:
    """Extract tool name from a tools/call request."""
    message_root = session_message.message.root

    if isinstance(message_root, types.JSONRPCRequest):
        if message_root.method == "tools/call":
            params = message_root.params
            if isinstance(params, dict) and "name" in params:
                return params["name"]

    return None
