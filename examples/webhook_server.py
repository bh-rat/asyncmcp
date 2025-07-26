#!/usr/bin/env python3
"""
Sample MCP Server using webhook transport
"""

import anyio
import click
import mcp.types as types
import uvicorn
from mcp.server.lowlevel import Server
from mcp.shared._httpx_utils import create_mcp_http_client
from shared import print_colored

from asyncmcp.webhook.manager import WebhookSessionManager
from asyncmcp.webhook.utils import WebhookServerConfig


async def fetch_website(
    url: str,
) -> list[types.ContentBlock]:
    print_colored(f"🌐 Fetching {url}", "blue")
    headers = {"User-Agent": "MCP Test Server (github.com/bh-rat/asyncmcp)"}
    async with create_mcp_http_client(headers=headers) as client:
        response = await client.get(url)
        response.raise_for_status()
        print_colored(f"✅ Successfully fetched {len(response.text)} characters", "green")
        return [types.TextContent(type="text", text=response.text)]


@click.command()
@click.option(
    "--server-port",
    type=int,
    default=8000,
    help="Port for HTTP server",
)
@click.option(
    "--webhook-port",
    type=int,
    default=8001,
    help="Port for webhook responses (not used by server, but kept for consistency)",
)
@click.option(
    "--stateless",
    is_flag=True,
    default=False,
    help="Run in stateless mode",
)
def main(server_port, webhook_port, stateless) -> int:
    print_colored("🚀 Starting MCP Website Fetcher Server with Webhook Transport", "cyan")
    app = Server("mcp-website-fetcher")

    @app.call_tool()
    async def fetch_tool(name: str, arguments: dict) -> list[types.ContentBlock]:
        if name != "fetch":
            raise ValueError(f"Unknown tool: {name}")
        if "url" not in arguments:
            raise ValueError("Missing required argument 'url'")
        return await fetch_website(arguments["url"])

    @app.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="fetch",
                title="Website Fetcher",
                description="Fetches a website and returns its content",
                inputSchema={
                    "type": "object",
                    "required": ["url"],
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "URL to fetch",
                        }
                    },
                },
            )
        ]

    async def arun():
        # Configure webhook transport
        print_colored("🔧 Configuring webhook transport", "yellow")

        config = WebhookServerConfig()

        # Create session manager
        session_manager = WebhookSessionManager(
            app,
            config,
            server_path="/mcp/request",
            stateless=stateless,
        )

        print_colored("📡 Starting webhook session manager", "green")
        print_colored(f"🔗 Server listening on http://localhost:{server_port}/mcp/request", "blue")

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
