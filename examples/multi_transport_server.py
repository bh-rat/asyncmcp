#!/usr/bin/env python3
"""
Multi-Transport MCP Server supporting both StreamableHTTP and Webhook transports.

This server demonstrates:
- StreamableHTTP transport for fast synchronous operations
- Webhook transport for long-running asynchronous operations
- Dynamic tool routing based on transport type
- Complete multi-transport architecture
"""

import anyio
import click
import mcp.types as types
import uvicorn
from mcp.server.lowlevel import Server
from mcp.shared._httpx_utils import create_mcp_http_client
from shared import print_colored
from starlette.applications import Starlette
from starlette.routing import Mount

from asyncmcp.multi_transport import MultiTransportServer, TransportType
from asyncmcp.webhook.manager import WebhookSessionManager
from asyncmcp.webhook.utils import WebhookServerConfig


async def fetch_sync(url: str) -> list[types.ContentBlock]:
    """
    Fast synchronous website fetcher - uses StreamableHTTP transport.
    Returns immediately via HTTP response.
    """
    print_colored(f"🌐 [HTTP-Sync] Fetching {url}", "blue")
    headers = {"User-Agent": "MCP Multi-Transport Server"}

    async with create_mcp_http_client(headers=headers) as client:
        response = await client.get(url)
        response.raise_for_status()
        content = response.text[:1000]  # Truncate for demo

    print_colored(f"✅ [HTTP-Sync] Fetched {len(content)} characters", "green")
    return [types.TextContent(type="text", text=f"[SYNC] Content from {url}:\n\n{content}...")]


async def analyze_async(url: str) -> list[types.ContentBlock]:
    """
    Asynchronous website analyzer - uses Webhook transport.
    Returns via webhook callback after processing.
    """
    print_colored(f"🔍 [Webhook-Async] Starting analysis of {url}", "yellow")

    # Simulate long-running async work
    await anyio.sleep(3)

    headers = {"User-Agent": "MCP Multi-Transport Server"}
    async with create_mcp_http_client(headers=headers) as client:
        response = await client.get(url)
        response.raise_for_status()
        content = response.text

    # More async processing
    await anyio.sleep(2)

    # Analysis
    word_count = len(content.split())
    char_count = len(content)
    line_count = len(content.split("\n"))

    analysis = f"""[ASYNC] Deep Analysis Results for {url}
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

    print_colored(f"✅ [Webhook-Async] Analysis completed for {url}", "green")
    return [types.TextContent(type="text", text=analysis)]


async def server_info() -> list[types.ContentBlock]:
    """
    Server information tool - uses default transport.
    """
    print_colored("📋 [Default] Getting server information", "cyan")

    info = """🚀 Multi-Transport MCP Server Information
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏗️  Architecture: AsyncMCP Multi-Transport System
📡 Active Transports: StreamableHTTP + Webhook

🛠️  Available Tools:
   • fetch_sync → StreamableHTTP Transport
     ↳ Fast synchronous website fetching
     ↳ Returns immediately via HTTP response
   
   • analyze_async → Webhook Transport
     ↳ Deep asynchronous website analysis
     ↳ Returns via webhook callback after processing
   
   • server_info → Default Transport
     ↳ Server status and routing information

🎯 Transport Routing:
   ✅ @transport_info(TransportType.HTTP) → StreamableHTTP
   ✅ @transport_info(TransportType.WEBHOOK) → Webhook
   ✅ No decorator → Default transport (StreamableHTTP)

💡 Client Usage:
   • tools/list → Returns all available tools
   • tools/call fetch_sync → Immediate HTTP response
   • tools/call analyze_async → Webhook async response

🔄 Status: Multi-transport routing active
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""

    print_colored("✅ [Default] Server info retrieved", "green")
    return [types.TextContent(type="text", text=info)]


@click.command()
@click.option("--http-port", type=int, default=8000, help="Port for StreamableHTTP transport")
@click.option("--webhook-port", type=int, default=8001, help="Port for Webhook transport")
@click.option("--stateless", is_flag=True, default=False, help="Run in stateless mode")
def main(http_port, webhook_port, stateless) -> int:
    print_colored("🚀 Multi-Transport MCP Server", "cyan")
    print_colored(f"   StreamableHTTP: http://localhost:{http_port}/mcp", "blue")
    print_colored(f"   Webhook: http://localhost:{webhook_port}/mcp/request", "yellow")

    # Create MCP server
    app = Server("mcp-multi-transport-server")

    # Register tool handlers with explicit transport routing
    @app.call_tool()
    async def handle_tools(name: str, arguments: dict) -> list[types.ContentBlock]:
        # Tool handler called
        if name == "fetch_sync":
            if "url" not in arguments:
                raise ValueError("Missing required argument 'url'")
            result = await fetch_sync(arguments["url"])
            # Tool handler returning result
            return result
        elif name == "analyze_async":
            if "url" not in arguments:
                raise ValueError("Missing required argument 'url'")
            # Note: This same handler runs on both HTTP and webhook servers
            # The webhook response delivery is handled by the transport layer
            result = await analyze_async(arguments["url"])
            # Tool handler returning result
            return result
        elif name == "server_info":
            result = await server_info()
            # Tool handler returning result
            return result
        else:
            raise ValueError(f"Unknown tool: {name}")

    # Apply transport routing info to specific tools
    # Note: This is for documentation/routing purposes
    fetch_sync.__transport_info__ = {
        "transport_type": TransportType.HTTP,
        "description": "Fast synchronous fetching via StreamableHTTP",
    }

    analyze_async.__transport_info__ = {
        "transport_type": TransportType.WEBHOOK,
        "description": "Long-running analysis via webhook transport",
    }

    @app.list_tools()
    async def list_tools() -> list[types.Tool]:
        # Base tool definitions
        base_tools = [
            types.Tool(
                name="fetch_sync",
                title="Synchronous Website Fetcher",
                description="Fetch website content synchronously via StreamableHTTP transport",
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
                description="Get multi-transport server information and routing details",
                inputSchema={"type": "object", "properties": {}},
            ),
        ]

        # Add transport metadata based on __transport_info__
        enhanced_tools = []
        for tool in base_tools:
            # Get the function by name
            tool_func = globals().get(tool.name)
            if tool_func and hasattr(tool_func, "__transport_info__"):
                transport_info = tool_func.__transport_info__
                transport_type = transport_info.get("transport_type")
                if transport_type:
                    # Add transport metadata to inputSchema
                    if tool.inputSchema:
                        tool.inputSchema["_meta"] = {
                            "transport": transport_type.value,
                            "description": transport_info.get("description", ""),
                        }
            enhanced_tools.append(tool)

        return enhanced_tools

    async def arun():
        print_colored("🔧 Configuring multi-transport server", "yellow")

        # Create multi-transport server
        multi_server = MultiTransportServer(mcp_server=app, max_concurrent_sessions=100, enable_routing_validation=True)

        # Add StreamableHTTP transport
        multi_server.add_http_transport(
            host="localhost",
            port=http_port,
            path="/mcp",
            stateless=stateless,
            json_response=False,  # Use SSE for streaming
            tools=["fetch_sync"],
            is_default=True,
        )

        # Add Webhook transport
        webhook_config = WebhookServerConfig(timeout_seconds=30.0, max_retries=1)
        webhook_manager = WebhookSessionManager(
            app=app, config=webhook_config, server_path="/mcp/request", stateless=stateless
        )

        multi_server.add_webhook_transport(
            webhook_manager=webhook_manager, tools=["analyze_async"], external_management=True
        )

        # Get webhook manager from transport registry for external HTTP server
        webhook_transport_info = multi_server.registry.get_transport_info(TransportType.WEBHOOK)
        webhook_manager = webhook_transport_info.config["manager"] if webhook_transport_info else None

        if not webhook_manager:
            print_colored("❌ Failed to get webhook manager from transport registry", "red")
            return 1

        # Validate configuration
        print_colored("🔍 Validating configuration", "yellow")
        validation = multi_server.validate_configuration()

        if not validation["valid"]:
            print_colored("❌ Configuration validation failed:", "red")
            for error in validation["errors"]:
                print_colored(f"   • {error}", "red")
            return 1

        print_colored("✅ Configuration validated", "green")

        # Show routing
        print_colored("🗺️  Tool routing:", "cyan")
        routing_info = multi_server.get_tool_routing_info()
        for tool_name, info in routing_info.items():
            transport = info.get("assigned_transport", "unknown")
            explicit = "explicit" if info.get("has_explicit_transport") else "default"
            print_colored(f"   • {tool_name} → {transport} ({explicit})", "blue")

        print_colored("🎯 Starting multi-transport server", "green")

        # Create external HTTP server for webhook transport
        print_colored("🌐 Setting up external HTTP server for webhook integration", "yellow")

        # Start webhook manager first
        async with webhook_manager.run():
            # Create combined ASGI app
            combined_app = Starlette()

            # Mount webhook transport endpoints (webhook manager handles /mcp/request internally)
            webhook_asgi_app = webhook_manager.asgi_app()
            combined_app.routes.append(Mount("/", webhook_asgi_app))

            # Create uvicorn server for webhook transport
            config = uvicorn.Config(
                app=combined_app,
                host="localhost",
                port=webhook_port,
                log_level="warning",  # Reduce log noise
            )
            webhook_server = uvicorn.Server(config)

            # Use task group to run both servers in parallel
            async with anyio.create_task_group() as tg:
                # Start webhook HTTP server in background
                async def run_webhook_server():
                    await webhook_server.serve()

                tg.start_soon(run_webhook_server)

                # Start multi-transport server in background
                async def run_multi_transport_server():
                    async with multi_server.run():
                        # Show status
                        status = multi_server.get_transport_status()
                        print_colored(f"📊 Server running with {len(status['transports'])} transports:", "green")
                        for transport_name, transport_info in status["transports"].items():
                            active = "active" if transport_info["active"] else "inactive"
                            tools_count = len(transport_info["tools"])
                            print_colored(f"   • {transport_name}: {active} ({tools_count} tools)", "blue")

                        print_colored("🔄 Multi-transport server ready!", "green")
                        print_colored("   • Use tools/list to see available tools", "gray")
                        print_colored("   • Use tools/call fetch_sync for sync HTTP response", "gray")
                        print_colored("   • Use tools/call analyze_async for async webhook response", "gray")
                        print_colored("   • Press Ctrl+C to stop", "gray")

                        await anyio.sleep_forever()

                tg.start_soon(run_multi_transport_server)

    try:
        anyio.run(arun)
        return 0
    except KeyboardInterrupt:
        print_colored("\n👋 Server stopped", "yellow")
        return 0
    except Exception as e:
        print_colored(f"❌ Server error: {e}", "red")
        return 1


if __name__ == "__main__":
    main()
