# StreamableHTTP + Webhook Transport Examples

This directory contains examples demonstrating the StreamableHTTP + Webhook transport, which combines MCP's StreamableHTTP functionality with selective webhook-based tool result delivery.

## Overview

The StreamableHTTP + Webhook transport provides:

- **StreamableHTTP Compatibility**: Full MCP StreamableHTTP support (SSE streaming, JSON responses, session management)
- **Selective Webhook Routing**: Tools marked with `@webhook_tool` deliver results via HTTP POST
- **Single Session**: Both HTTP streaming and webhook delivery share the same MCP session
- **AsyncMCP Architecture**: Uses AsyncMCP's base classes and patterns for consistency

## Architecture

```
┌─────────────────┐    HTTP POST     ┌─────────────────┐
│                 │ ────────────────►│                 │
│     Client      │                  │     Server      │
│                 │◄──── SSE ────────│                 │
└─────────────────┘                  └─────────────────┘
         │                                     │
         │ Webhook POST                        │
         │ (async tool results)                │
         └◄────────────────────────────────────┘
```

## Files

### `streamable_http_webhook_server.py`
Example MCP server with StreamableHTTP + Webhook transport featuring:

- **fetch_sync**: Standard tool using SSE delivery (immediate response)
- **analyze_async**: Tool marked with `@webhook_tool` using webhook delivery (async response)  
- **server_info**: Standard tool using default SSE delivery

### `streamable_http_webhook_client.py`
Example MCP client that:

- Connects via HTTP to the server
- Receives standard tool results via SSE stream
- Runs a webhook server to receive webhook tool results
- Demonstrates single session management for both transport methods

## Usage

### 1. Start the Server

```bash
# Basic server (SSE mode)
uv run examples/streamable_http_webhook_server.py

# JSON response mode
uv run examples/streamable_http_webhook_server.py --json-response

# Stateless mode
uv run examples/streamable_http_webhook_server.py --stateless

# Custom port
uv run examples/streamable_http_webhook_server.py --server-port 8080
```

### 2. Start the Client

```bash
# Basic client
uv run examples/streamable_http_webhook_client.py

# Custom URLs
uv run examples/streamable_http_webhook_client.py \
  --server-url http://localhost:8080/mcp \
  --webhook-url http://localhost:8002/webhook
```

## Tool Routing Demo

When you run the examples:

1. **fetch_sync** - Returns immediately via SSE stream
2. **analyze_async** - Takes ~5 seconds, returns via webhook POST
3. **server_info** - Returns immediately via SSE stream (default)

## Key Features Demonstrated

### Server-Side

```python
from asyncmcp.streamable_http_webhook import webhook_tool

# Standard tool (SSE delivery)
async def fetch_website_sync(url: str):
    return await fast_fetch(url)

# Webhook tool (HTTP POST delivery)
@webhook_tool(description="Long-running analysis")
async def analyze_website_async(url: str):
    await asyncio.sleep(5)  # Long operation
    return await deep_analysis(url)
```

### Client-Side

```python
from asyncmcp.streamable_http_webhook import streamable_http_webhook_client

async with streamable_http_webhook_client(config) as (read, write, client):
    # Get webhook handler for your web framework
    webhook_handler = await client.get_webhook_callback()
    
    # Mount in your web app
    app.add_route("/webhook", webhook_handler, methods=["POST"])
    
    # Use MCP client normally
    async with ClientSession(read, write) as session:
        result = await session.call_tool("analyze_async", {"url": "..."})
        # Result will arrive at webhook_handler
```

## Configuration Options

### Server Configuration

```python
config = StreamableHTTPWebhookConfig(
    json_response=False,          # Use SSE streaming (vs JSON responses)
    timeout_seconds=30.0,         # HTTP timeout
    webhook_timeout=30.0,         # Webhook delivery timeout
    webhook_max_retries=1,        # Webhook retry attempts
    security_settings=None,       # MCP security settings
)
```

### Client Configuration

```python
config = StreamableHTTPWebhookClientConfig(
    server_url="http://localhost:8000/mcp",
    webhook_url="http://localhost:8001/webhook",
    client_id="my-client",
    timeout_seconds=30.0,
    max_retries=1,
)
```

## Integration with Web Frameworks

The client provides a webhook handler that can be integrated with any ASGI-compatible web framework:

### FastAPI
```python
from fastapi import FastAPI
app = FastAPI()

webhook_handler = await client.get_webhook_callback()
app.add_api_route("/webhook", webhook_handler, methods=["POST"])
```

### Starlette
```python
from starlette.applications import Starlette
from starlette.routing import Route

webhook_handler = await client.get_webhook_callback() 
routes = [Route("/webhook", webhook_handler, methods=["POST"])]
app = Starlette(routes=routes)
```

## Comparison with Other Transports

| Feature | StreamableHTTP | Webhook | StreamableHTTP + Webhook |
|---------|----------------|---------|--------------------------|
| Response Method | SSE Stream | HTTP POST | Both (selective) |
| Session Management | ✅ | ✅ | ✅ |
| Immediate Results | ✅ | ❌ | ✅ (for standard tools) |
| Async Tool Results | ❌ | ✅ | ✅ (for webhook tools) |
| MCP Compatibility | ✅ | ❌ | ✅ |
| Single Session | ✅ | ✅ | ✅ |

## Troubleshooting

### Common Issues

1. **Webhook not receiving messages**: Ensure webhook URL in client config matches your web server
2. **Session ID mismatch**: Both HTTP and webhook should use the same session ID
3. **Tools not routing to webhook**: Ensure tools are properly marked with `@webhook_tool`

### Debug Mode

Enable debug logging:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

### Testing Webhook Delivery

Use a tool like `ngrok` to expose your webhook endpoint for testing:

```bash
ngrok http 8001
# Use the ngrok URL as your webhook_url
```

## Performance Considerations

- **Standard Tools**: Use SSE for immediate responses and real-time updates
- **Long-Running Tools**: Use webhooks for operations > 30 seconds
- **Mixed Workloads**: Combine both based on tool characteristics
- **Scaling**: Each session maintains separate HTTP and webhook routing