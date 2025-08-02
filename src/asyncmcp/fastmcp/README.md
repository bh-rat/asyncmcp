# FastMCP Integration for AsyncMCP

This module provides seamless integration between [FastMCP](https://github.com/jlowin/fastmcp) servers and AsyncMCP's custom transport layers (SQS, SNS+SQS, Webhook, etc.).

## Overview

FastMCP is a Pythonic framework for building MCP servers with a simple, decorator-based API. AsyncMCP extends MCP with asynchronous transport layers for queue-based and webhook-based communication. This integration allows you to:

- Write MCP servers using FastMCP's simple API
- Deploy them over AsyncMCP's custom transports (SQS, SNS+SQS, Webhooks)
- Use FastMCP clients to communicate with servers over async transports

## Installation

```bash
# Install asyncmcp with FastMCP support
pip install asyncmcp[fastmcp]
```

## Quick Start

### Creating a FastMCP Server

```python
from fastmcp import FastMCP

# Create a FastMCP server
mcp = FastMCP("my-server")

@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b

@mcp.resource("file://data.txt")
def get_data() -> str:
    """Get some data."""
    return "Hello from FastMCP!"
```

### Running with AsyncMCP Transports

#### SQS Transport

```python
import boto3
from asyncmcp.fastmcp import run_fastmcp_server
from asyncmcp.sqs.utils import SqsServerConfig, SqsClientConfig

# Configure transport
sqs_client = boto3.client("sqs", region_name="us-east-1")

server_config = SqsServerConfig(
    request_queue_url="https://sqs.us-east-1.amazonaws.com/123/requests"
)

client_config = SqsClientConfig(
    read_queue_url="https://sqs.us-east-1.amazonaws.com/123/requests",
    response_queue_url="https://sqs.us-east-1.amazonaws.com/123/responses"
)

# Run the server
await run_fastmcp_server(
    mcp,
    transport_type="sqs",
    server_config=server_config,
    client_config=client_config,
    low_level_clients={"sqs_client": sqs_client}
)
```

#### SNS+SQS Transport

```python
sns_client = boto3.client("sns", region_name="us-east-1")

server_config = SnsSqsServerConfig(
    queue_url="https://sqs.us-east-1.amazonaws.com/123/server-queue",
    topic_arn="arn:aws:sns:us-east-1:123:mcp-topic"
)

client_config = SnsSqsClientConfig(
    topic_arn="arn:aws:sns:us-east-1:123:mcp-topic",
    queue_url=""  # Will be created dynamically
)

await run_fastmcp_server(
    mcp,
    transport_type="sns_sqs",
    server_config=server_config,
    client_config=client_config,
    low_level_clients={
        "sqs_client": sqs_client,
        "sns_client": sns_client
    }
)
```

### Creating a FastMCP Client

```python
from asyncmcp.fastmcp import create_fastmcp_client

# Create a client
client = create_fastmcp_client(
    transport_type="sqs",
    config=client_config,
    low_level_clients={"sqs_client": sqs_client}
)

# Use the client
async with client:
    # List and call tools
    tools = await client.list_tools()
    result = await client.call_tool("add", {"a": 5, "b": 3})
    
    # Read resources
    content = await client.read_resource("file://data.txt")
```

## How It Works

The integration leverages FastMCP's `as_proxy` method to create a proxy server that communicates through AsyncMCP transports:

1. **AsyncMCPTransport**: A custom transport adapter that implements FastMCP's `ClientTransport` protocol
2. **Server Wrapper**: Uses `FastMCP.as_proxy()` to create a proxy that routes requests through AsyncMCP
3. **Client Factory**: Creates FastMCP clients configured with AsyncMCP transports

## Architecture

```
FastMCP Server
     |
     v
FastMCP Proxy (via as_proxy)
     |
     v
AsyncMCPTransport (ClientTransport)
     |
     v
AsyncMCP Client (sqs_client, etc.)
     |
     v
Queue/Topic/Webhook
```

## Supported Transports

- **SQS**: AWS Simple Queue Service for reliable message queuing
- **SNS+SQS**: AWS SNS topics with SQS subscriptions for pub/sub patterns
- **Webhook**: HTTP webhooks for direct server-to-server communication
- **Streamable HTTP + Webhook**: Combines streaming HTTP requests with webhook responses

## Examples

See the [examples/fastmcp_example.py](../../../examples/fastmcp_example.py) for a complete working example.

## Testing

Run the integration tests:

```bash
pytest tests/fastmcp/
```

## Limitations

- The integration creates a proxy layer, which may add some overhead compared to native AsyncMCP servers
- FastMCP's lifespan and context features work through the proxy but may have different timing characteristics
- Some advanced FastMCP features may require additional configuration when used with async transports