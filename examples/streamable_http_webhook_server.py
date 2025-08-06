#!/usr/bin/env python3
"""
Example MCP Server using StreamableHTTP + Webhook transport.

This server demonstrates:
- StreamableHTTP for standard tool responses (immediate via SSE)
- Webhook delivery for tools marked with @webhook_tool decorator
- Full MCP protocol compatibility with AsyncMCP architecture
- Single session management for both transport methods
"""

import asyncio
import logging

import anyio
import click
import mcp.types as types
import uvicorn
from mcp.server.lowlevel import Server
from mcp.shared._httpx_utils import create_mcp_http_client
from shared import print_colored

from asyncmcp.streamable_http_webhook import (
    StreamableHTTPWebhookConfig,
    StreamableHTTPWebhookSessionManager,
    webhook_tool,
)

logger = logging.getLogger(__name__)


async def fetch_website_sync(url: str) -> list[types.ContentBlock]:
    """
    Fast synchronous website fetcher - uses StreamableHTTP (SSE).
    Returns immediately via HTTP response.
    """
    print_colored(f"🌐 [HTTP-SSE] Fetching {url}", "blue")
    headers = {"User-Agent": "MCP StreamableHTTP+Webhook Server"}

    async with create_mcp_http_client(headers=headers) as client:
        response = await client.get(url)
        response.raise_for_status()
        content = response.text[:1000]  # Truncate for demo

    print_colored(f"✅ [HTTP-SSE] Fetched {len(content)} characters", "green")
    return [types.TextContent(type="text", text=f"[SSE] Quick fetch from {url}:\n\n{content}...")]


@webhook_tool(description="Long-running analysis via webhook", tool_name="analyze_async")
async def analyze_website_async(url: str) -> list[types.ContentBlock]:
    """
    Asynchronous website analyzer - uses Webhook delivery.
    Returns via webhook callback after processing.
    """
    print_colored(f"🔍 [Webhook] Starting analysis of {url}", "yellow")

    # Simulate long-running async work
    await asyncio.sleep(3)

    headers = {"User-Agent": "MCP StreamableHTTP+Webhook Server"}
    async with create_mcp_http_client(headers=headers) as client:
        response = await client.get(url)
        response.raise_for_status()
        content = response.text

    # More async processing
    await asyncio.sleep(2)

    # Analysis
    word_count = len(content.split())
    char_count = len(content)
    line_count = len(content.split("\n"))

    analysis = f"""[WEBHOOK] Deep Analysis Results for {url}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 Processing: Completed via Webhook (Async)
⏱️  Processing Time: ~5 seconds

📈 Content Statistics:
   • Total Characters: {char_count:,}
   • Word Count: {word_count:,} 
   • Line Count: {line_count:,}
   • Average Words per Line: {word_count / line_count if line_count > 0 else 0:.1f}

🔍 Content Preview:
{content[:500].strip()}{"..." if len(content) > 500 else ""}

✅ Analysis completed and returned via webhook transport
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""

    print_colored(f"✅ [Webhook] Analysis completed for {url}", "green")
    return [types.TextContent(type="text", text=analysis)]


async def server_info() -> list[types.ContentBlock]:
    """
    Server information tool - uses default transport (SSE).
    """
    print_colored("📋 [SSE-Default] Getting server information", "cyan")

    info = """🚀 StreamableHTTP + Webhook MCP Server Information
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏗️  Architecture: AsyncMCP StreamableHTTP + Webhook Transport
📡 Transport Methods: HTTP SSE + Webhook POST

🛠️  Available Tools:
   • fetch_sync → StreamableHTTP (SSE)
     ↳ Fast synchronous website fetching
     ↳ Returns immediately via Server-Sent Events
   
   • analyze_async → Webhook Transport  
     ↳ Deep asynchronous website analysis
     ↳ Returns via HTTP POST to client webhook URL
   
   • server_info → Default Transport (SSE)
     ↳ Server status and routing information

🎯 Tool Routing:
   ✅ @webhook_tool decorator → Webhook POST delivery
   ✅ Standard tools → StreamableHTTP SSE delivery
   ✅ Single session for both transport methods

💡 Client Usage:
   • Standard tools: Results via SSE stream
   • Webhook tools: Results via webhook endpoint
   • Session management: Same session ID for both

🔄 Status: StreamableHTTP + Webhook routing active
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""

    print_colored("✅ [SSE-Default] Server info retrieved", "green")
    return [types.TextContent(type="text", text=info)]


@click.command()
@click.option(
    "--server-port",
    type=int,
    default=8000,
    help="Port for HTTP server",
)
@click.option(
    "--stateless",
    is_flag=True,
    default=False,
    help="Run in stateless mode",
)
@click.option(
    "--json-response",
    is_flag=True,
    default=False,
    help="Use JSON responses instead of SSE streaming",
)
def main(server_port, stateless, json_response) -> int:
    print_colored("🚀 Starting MCP Server with StreamableHTTP + Webhook Transport", "cyan")
    print_colored(f"   Endpoint: http://localhost:{server_port}/mcp", "blue")
    print_colored(f"   Mode: {'Stateless' if stateless else 'Stateful'}", "blue")
    print_colored(f"   Response: {'JSON' if json_response else 'SSE Streaming'}", "blue")

    # Create MCP server
    app = Server("mcp-streamable-http-webhook-server")

    # Register tool handlers with explicit webhook routing
    @app.call_tool()
    async def handle_tools(name: str, arguments: dict) -> list[types.ContentBlock]:
        if name == "fetch_sync":
            if "url" not in arguments:
                raise ValueError("Missing required argument 'url'")
            return await fetch_website_sync(arguments["url"])
        elif name == "analyze_async":
            if "url" not in arguments:
                raise ValueError("Missing required argument 'url'")
            return await analyze_website_async(arguments["url"])
        elif name == "server_info":
            return await server_info()
        else:
            raise ValueError(f"Unknown tool: {name}")

    @app.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="fetch_sync",
                title="Synchronous Website Fetcher",
                description="Fetch website content synchronously via StreamableHTTP SSE",
                inputSchema={
                    "type": "object",
                    "required": ["url"],
                    "properties": {"url": {"type": "string", "description": "URL to fetch"}},
                },
            ),
            types.Tool(
                name="analyze_async",
                title="Asynchronous Website Analyzer",
                description="Perform deep website analysis asynchronously via webhook transport",
                inputSchema={
                    "type": "object",
                    "required": ["url"],
                    "properties": {"url": {"type": "string", "description": "URL to analyze"}},
                },
            ),
            types.Tool(
                name="server_info",
                title="Server Information",
                description="Get StreamableHTTP + Webhook server information and routing details",
                inputSchema={"type": "object", "properties": {}},
            ),
        ]

    async def arun():
        # Configure StreamableHTTP + Webhook transport
        print_colored("🔧 Configuring StreamableHTTP + Webhook transport", "yellow")

        config = StreamableHTTPWebhookConfig(
            json_response=json_response,
            timeout_seconds=30.0,
            webhook_timeout=30.0,
            webhook_max_retries=1,
        )

        # Create session manager with explicit webhook tools specification
        webhook_tools = {"analyze_async"}  # Explicitly specify which tools use webhooks
        session_manager = StreamableHTTPWebhookSessionManager(
            app,
            config,
            server_path="/mcp",
            stateless=stateless,
            webhook_tools=webhook_tools,
        )

        print_colored("📡 Starting StreamableHTTP + Webhook session manager", "green")
        print_colored(f"🔗 Server listening on http://localhost:{server_port}/mcp", "blue")
        print_colored(f"📄 Webhook tools configured: {webhook_tools}", "blue")

        # Enable debug logging to see what's happening
        logging.basicConfig(level=logging.DEBUG)
        logger.setLevel(logging.DEBUG)

        async with session_manager.run():
            # Start uvicorn server with the ASGI app
            asgi_app = session_manager.asgi_app()
            config = uvicorn.Config(
                app=asgi_app,
                host="localhost",
                port=server_port,
                log_level="info",
            )
            server = uvicorn.Server(config)
            await server.serve()

    anyio.run(arun)
    return 0


if __name__ == "__main__":
    main()
