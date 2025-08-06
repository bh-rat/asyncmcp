#!/usr/bin/env python3
"""Example demonstrating FastMCP integration with asyncmcp transports.

This example shows how to:
1. Create a FastMCP server with tools, resources, and prompts
2. Run it over asyncmcp's SQS transport
3. Connect to it with a FastMCP client over the same transport
"""

import asyncio
import logging
from datetime import datetime
from typing import Dict, Any

import boto3
import click
from fastmcp import FastMCP
from mcp.types import TextContent, Tool

from asyncmcp.fastmcp import create_fastmcp_client, run_fastmcp_server
from asyncmcp.sqs.utils import SqsServerConfig, SqsClientConfig

try:
    from examples.shared import (
        TRANSPORT_SQS,
        TRANSPORT_SNS_SQS,
        create_server_transport_config,
        create_client_transport_config,
        print_colored,
    )
except ImportError:
    # Fallback for running from different locations
    from shared import (
        TRANSPORT_SQS,
        TRANSPORT_SNS_SQS,
        create_server_transport_config,
        create_client_transport_config,
        print_colored,
    )

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_example_fastmcp_server() -> FastMCP:
    """Create an example FastMCP server with various capabilities."""
    mcp = FastMCP(
        name="fastmcp-example",
        version="1.0.0",
        instructions="An example FastMCP server demonstrating asyncmcp integration",
    )

    # Add a simple tool
    @mcp.tool()
    def add(a: int, b: int) -> int:
        """Add two numbers together."""
        return a + b

    # Add a more complex tool
    @mcp.tool()
    async def fetch_data(url: str) -> Dict[str, Any]:
        """Fetch data from a URL (simulated)."""
        # Simulate async operation
        await asyncio.sleep(0.1)
        return {
            "url": url,
            "status": "success",
            "data": f"Sample data from {url}",
            "timestamp": datetime.now().isoformat(),
        }

    # Add a resource
    @mcp.resource("file://example/readme.txt")
    def get_readme() -> str:
        """Get the example README content."""
        return """FastMCP AsyncMCP Integration Example
=====================================

This example demonstrates how FastMCP servers can communicate
over asyncmcp's custom transports like SQS, SNS+SQS, and Webhooks.

Features:
- Tools: add, fetch_data
- Resources: This README
- Prompts: greeting, code_review
"""

    # Add a prompt
    @mcp.prompt()
    def greeting(name: str = "User") -> str:
        """Generate a greeting message."""
        return f"Hello {name}! Welcome to the FastMCP asyncmcp integration example."

    @mcp.prompt()
    def code_review(code: str, language: str = "python") -> str:
        """Generate a code review prompt."""
        return f"""Please review the following {language} code:

```{language}
{code}
```

Consider:
1. Code quality and best practices
2. Potential bugs or issues
3. Performance improvements
4. Readability and maintainability
"""

    return mcp


async def run_server(transport: str):
    """Run the FastMCP server with the specified transport."""
    print_colored("🚀 Starting FastMCP Example Server", "cyan")

    # Create the FastMCP server
    mcp = create_example_fastmcp_server()

    # Configure transport
    if transport == TRANSPORT_SQS:
        server_config, sqs_client = create_server_transport_config(transport_type=transport)[:2]

        # For SQS, we need client config that points to the same queues
        client_config = SqsClientConfig(
            read_queue_url=server_config.read_queue_url,
            response_queue_url=server_config.read_queue_url,  # Server will determine actual response queue
        )

        print_colored(f"📡 Running FastMCP server '{mcp.name}' on SQS transport", "green")
        await run_fastmcp_server(
            mcp=mcp,
            transport_type="sqs",
            server_config=server_config,
            client_config=client_config,
            low_level_clients={"sqs_client": sqs_client},
        )

    elif transport == TRANSPORT_SNS_SQS:
        server_config, sqs_client, sns_client = create_server_transport_config(transport_type=transport)

        # For SNS+SQS, client config needs the topic ARN
        from asyncmcp.sns_sqs.utils import SnsSqsClientConfig

        # Get the response topic ARN from the shared resources
        from shared import RESOURCES

        client_config = SnsSqsClientConfig(
            sns_topic_arn=RESOURCES["server_response_topic"],
            sqs_queue_url="",  # Will be created by client
        )

        print_colored(f"📡 Running FastMCP server '{mcp.name}' on SNS+SQS transport", "green")
        await run_fastmcp_server(
            mcp=mcp,
            transport_type="sns_sqs",
            server_config=server_config,
            client_config=client_config,
            low_level_clients={"sqs_client": sqs_client, "sns_client": sns_client},
        )


async def run_client(transport: str):
    """Run a FastMCP client to interact with the server."""
    print_colored("🔧 Starting FastMCP Example Client", "cyan")

    # Configure transport
    if transport == TRANSPORT_SQS:
        config, sqs_client = create_client_transport_config(transport_type=transport)[:2]
        low_level_clients = {"sqs_client": sqs_client}
        transport_type = "sqs"

    elif transport == TRANSPORT_SNS_SQS:
        config, sqs_client, sns_client = create_client_transport_config(transport_type=transport)
        low_level_clients = {"sqs_client": sqs_client, "sns_client": sns_client}
        transport_type = "sns_sqs"

    else:
        raise ValueError(f"Unsupported transport: {transport}")

    # Create FastMCP client
    client = create_fastmcp_client(
        transport_type=transport_type,
        config=config,
        low_level_clients=low_level_clients,
    )

    async with client:
        print_colored("✅ Client connected!", "green")

        # List available tools
        print_colored("\n📋 Available Tools:", "yellow")
        tools = await client.list_tools()
        for tool in tools:
            print_colored(f"  - {tool.name}: {tool.description}", "white")

        # Call the add tool
        print_colored("\n🔧 Calling 'add' tool with a=5, b=3:", "yellow")
        result = await client.call_tool("add", {"a": 5, "b": 3})
        print_colored(f"  Result: {result.content[0].text}", "green")

        # Call the fetch_data tool
        print_colored("\n🔧 Calling 'fetch_data' tool:", "yellow")
        result = await client.call_tool("fetch_data", {"url": "https://example.com/api/data"})
        print_colored(f"  Result: {result.content[0].text}", "green")

        # List and read resources
        print_colored("\n📁 Available Resources:", "yellow")
        resources = await client.list_resources()
        for resource in resources:
            print_colored(f"  - {resource.uri}: {resource.name}", "white")

        if resources:
            print_colored(f"\n📖 Reading resource '{resources[0].uri}':", "yellow")
            content = await client.read_resource(str(resources[0].uri))
            # FastMCP client returns a list of content blocks
            if content and len(content) > 0:
                if hasattr(content[0], "text"):
                    print_colored(content[0].text, "white")
                else:
                    print_colored(str(content[0]), "white")

        # List and get prompts
        print_colored("\n💬 Available Prompts:", "yellow")
        prompts = await client.list_prompts()
        for prompt in prompts:
            print_colored(f"  - {prompt.name}: {prompt.description}", "white")

        if prompts:
            print_colored(f"\n💬 Getting 'greeting' prompt:", "yellow")
            prompt_result = await client.get_prompt("greeting", {"name": "AsyncMCP User"})
            for msg in prompt_result.messages:
                print_colored(f"  {msg.role}: {msg.content.text}", "green")


@click.group()
def cli():
    """FastMCP AsyncMCP Integration Example."""
    pass


@cli.command()
@click.option(
    "--transport",
    type=click.Choice([TRANSPORT_SQS, TRANSPORT_SNS_SQS], case_sensitive=False),
    default=TRANSPORT_SQS,
    help="Transport layer to use",
)
def server(transport):
    """Run the FastMCP server."""
    asyncio.run(run_server(transport))


@cli.command()
@click.option(
    "--transport",
    type=click.Choice([TRANSPORT_SQS, TRANSPORT_SNS_SQS], case_sensitive=False),
    default=TRANSPORT_SQS,
    help="Transport layer to use",
)
def client(transport):
    """Run the FastMCP client."""
    asyncio.run(run_client(transport))


if __name__ == "__main__":
    cli()
