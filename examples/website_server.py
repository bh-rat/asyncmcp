#!/usr/bin/env python3

import anyio
import click
import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.shared._httpx_utils import create_mcp_http_client
from shared import (
    TRANSPORT_SNS_SQS,
    TRANSPORT_SQS,
    create_server_transport_config,
    print_colored,
)

from asyncmcp.sns_sqs.manager import SnsSqsSessionManager
from asyncmcp.sqs.manager import SqsSessionManager


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
    "--transport",
    type=click.Choice([TRANSPORT_SNS_SQS, TRANSPORT_SQS], case_sensitive=False),
    default=TRANSPORT_SNS_SQS,
    help="Transport layer to use",
)
def main(transport) -> int:
    print_colored("🚀 Starting MCP Website Fetcher Server", "cyan")
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
        # Configure transport based on command line argument
        print_colored(f"🔧 Configuring {transport} transport", "yellow")

        if transport == TRANSPORT_SNS_SQS:
            server_configuration, sqs_client, sns_client = create_server_transport_config(transport_type=transport)
            session_manager = SnsSqsSessionManager(
                app=app, config=server_configuration, sqs_client=sqs_client, sns_client=sns_client
            )
            print_colored("📡 SQS server ready and listening for requests", "green")
            print_colored("📝 Send an 'initialize' request to start a session", "cyan")

            async with session_manager.run():
                try:
                    while True:
                        await anyio.sleep(10)
                        # Optionally show session stats
                        stats = session_manager.get_all_sessions()
                        if stats:
                            print(f"🔗 Active sessions: {len(stats)}")
                            for session_id, stat in stats.items():
                                terminated = stat.get("terminated", False)
                                status = "terminated" if terminated else "active"
                                print(f"  Session {session_id[:8]}...: {status}")
                except KeyboardInterrupt:
                    print_colored("🛑 Shutting down server...", "yellow")
        else:
            server_configuration, sqs_client, sns_client = create_server_transport_config(transport_type=transport)
            session_manager = SqsSessionManager(app=app, config=server_configuration, sqs_client=sqs_client)

            async with session_manager.run():
                print_colored("📡 SQS server ready and listening for requests", "green")
                print_colored("📝 Send an 'initialize' request to start a session", "cyan")

                # Keep the server running
                try:
                    while True:
                        await anyio.sleep(10)
                        # Optionally show session stats
                        stats = session_manager.get_all_sessions()
                        if stats:
                            print(f"🔗 Active sessions: {len(stats)}")
                            for session_id, stat in stats.items():
                                terminated = stat.get("terminated", False)
                                status = "terminated" if terminated else "active"
                                print(f"  Session {session_id[:8]}...: {status}")
                except KeyboardInterrupt:
                    print_colored("🛑 Shutting down server...", "yellow")

    anyio.run(arun)

    return 0


if __name__ == "__main__":
    main()
