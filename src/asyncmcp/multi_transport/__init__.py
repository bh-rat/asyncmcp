"""
Multi-transport support for AsyncMCP.

This module provides dynamic transport registration and routing capabilities,
allowing a single MCP server to support multiple transport types simultaneously.
"""

from .adapters import HttpTransportAdapter
from .registry import ToolTransportInfo, TransportRegistry, TransportType
from .routing import transport_info
from .server import MultiTransportServer

__all__ = [
    "TransportRegistry",
    "TransportType",
    "ToolTransportInfo",
    "MultiTransportServer",
    "transport_info",
    "HttpTransportAdapter",
]
