"""
Routing utilities for multi-transport AsyncMCP.

This module provides decorators for associating tools with specific transport types.
The decorators work with MultiTransportServer instances to maintain server-scoped
tool-transport associations.
"""

import logging
from typing import Any, Callable, Optional

from .registry import TransportType

logger = logging.getLogger(__name__)


def transport_info(transport_type: TransportType, description: Optional[str] = None, **metadata: Any) -> Callable:
    """
    Decorator to associate a tool with a specific transport type.

    This decorator must be used in conjunction with a MultiTransportServer instance.
    The tool metadata will be registered with the server's registry when the
    decorator is applied.

    Args:
        transport_type: The transport type this tool should use
        description: Optional description of why this transport is used
        **metadata: Additional metadata for the tool-transport association

    Example:
        @transport_info(TransportType.WEBHOOK, description="Requires webhook for async responses")
        @app.call_tool()
        async def long_running_tool(name: str, arguments: dict):
            # Tool implementation
            pass

    Note:
        The server instance must have its registry accessible for this decorator to work.
        This is automatically handled when using MultiTransportServer.
    """

    def decorator(func: Callable) -> Callable:
        # Extract tool name from the function
        tool_name = getattr(func, "__name__", "unknown")

        # Store metadata as function attribute for later registration
        # The MultiTransportServer will scan for these attributes during tool discovery
        func._transport_type = transport_type
        func._transport_description = description
        func._transport_metadata = metadata

        logger.debug(f"Marked tool '{tool_name}' for transport {transport_type}")

        return func

    return decorator


def register_tool_with_server(server: "MultiTransportServer", func: Callable) -> None:
    """
    Register a decorated tool's transport metadata with a server's registry.

    This function is called internally by MultiTransportServer during tool discovery.

    Args:
        server: The MultiTransportServer instance
        func: The decorated function
    """
    if not hasattr(func, "_transport_type"):
        return

    tool_name = getattr(func, "__name__", "unknown")
    transport_type = func._transport_type
    description = getattr(func, "_transport_description", None)
    metadata = getattr(func, "_transport_metadata", {})

    server.registry.register_tool_metadata(
        tool_name=tool_name, transport_type=transport_type, description=description, **metadata
    )

    logger.debug(f"Registered tool '{tool_name}' with server registry: {transport_type}")
