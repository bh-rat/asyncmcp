import asyncio

import click
import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.shared._httpx_utils import create_mcp_http_client

from asyncmcp.sns_sqs.server import sns_sqs_server
from shared import create_server_transport_config


async def fetch_website(
    url: str,
) -> list[types.ContentBlock]:
    headers = {"User-Agent": "MCP Test Server (github.com/bh-rat/asyncmcp)"}
    async with create_mcp_http_client(headers=headers) as client:
        response = await client.get(url)
        response.raise_for_status()
        return [types.TextContent(type="text", text=response.text)]


@click.command()
def main() -> int:
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
        # Changes for running asyncmcp servers :
        server_configuration = create_server_transport_config()
        async with sns_sqs_server(server_configuration) as (read_stream, write_stream):
            await app.run(read_stream, write_stream, app.create_initialization_options())

    asyncio.run(arun())

    return 0


if __name__ == "__main__":
    main()
