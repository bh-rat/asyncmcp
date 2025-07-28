# asyncmcp - Async transport layers for MCP 


[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
![Project Status: Alpha](https://img.shields.io/badge/status-alpha-orange)





---

## Overview


A regular MCP Server but working over queues :

https://github.com/user-attachments/assets/4b775ff8-02ae-4730-a822-3e1cedf9d744


Another MCP Server that sends async responses via Webhooks : 

https://github.com/user-attachments/assets/22f15a96-13bf-4038-8e80-938d9ee490c9



Quoting from the [official description](https://modelcontextprotocol.io/introduction) :<br/> 
> MCP is an open protocol that standardizes how applications provide context to LLMs.

But a lot of this context is not always readily available and takes time for the applications to process - think batch processing APIs, webhooks or queues. In these cases with the current transport layers, the MCP server would have to expose a light-weight polling wrapper in the MCP layer to allow waiting and polling for the tasks to be done. Although SSE does provide async functionalities but it comes with caveats. <br/>

asyncmcp explores supporting more of the async transport layer implementations for MCP clients and servers, beyond the officially supported stdio and Streamable Http transports. 

The whole idea of an **MCP server with async transport layer** is that it doesn't have to respond immediately to any requests. It can choose to direct them to internal queues for processing and the client doesn't have to stick around for the response.

## Supported Transport Types

AsyncMCP provides three different transport implementations for different use cases:

### 1. SNS+SQS Transport

**Best for**: High-throughput pub/sub architectures with message fanout capabilities

- **Server**: Listens to SQS queue for MCP requests, publishes responses to SNS topic
- **Client**: Publishes requests to SNS topic, listens to dedicated SQS queue for responses
- **Features**: Topic-based routing, message filtering, automatic scaling
- **Use Case**: Multiple clients, broadcast scenarios, cloud-native architectures

### 2. SQS-Only Transport

**Best for**: Simple point-to-point messaging with guaranteed delivery

- **Server**: Listens to request queue for MCP requests, sends responses to client-specific response queues
- **Client**: Sends requests to server queue, listens to dedicated response queue
- **Features**: Simple queue-to-queue communication, dynamic queue creation
- **Use Case**: Direct client-server communication, cost-effective messaging

### 3. Webhook Transport

**Best for**: HTTP-based integration with existing web infrastructure

- **Server**: Receives HTTP POST requests from clients, sends responses via HTTP POST to client webhook URLs
- **Client**: Sends HTTP POST requests to server, receives responses via HTTP webhook
- **Features**: HTTP-based, firewall-friendly, web-native integration
- **Use Case**: Web applications, microservices, HTTP-based architectures

### 4. StreamableHTTP + Webhook Transport

**Best for**: Hybrid scenarios requiring both immediate and asynchronous responses

- **Server**: Uses MCP's StreamableHTTP with SSE for standard tools, webhook POST for async tools explicitly configured via `webhook_tools` parameter
- **Client**: Receives immediate responses via SSE stream, async responses via webhook URL
- **Features**: Selective transport routing with explicit tool configuration, single session management, built on MCP StreamableHTTP base
- **Use Case**: Applications with mixed synchronous/asynchronous operations, real-time + batch processing

## Installation and Usage

```bash
# Using uv (recommended)
uv add asyncmcp
```

```bash
# Using pip  
pip install asyncmcp
```

## Basic Usage Examples

Note: We don't support FastMCP yet. The examples in this repo use the [basic way of creating MCP servers and clients](https://modelcontextprotocol.io/docs/concepts/architecture#implementation-example).

### SNS+SQS Transport

#### Server Setup
```python
import boto3
from asyncmcp.sns_sqs.server import sns_sqs_server
from asyncmcp import SnsSqsServerConfig

# Configure transport
config = SnsSqsServerConfig(
    sqs_queue_url="https://sqs.region.amazonaws.com/account/service-queue"
)

# Create AWS clients
sqs_client = boto3.client('sqs')
sns_client = boto3.client('sns')

async def main():
    async with sns_sqs_server(config, sqs_client, sns_client) as (read_stream, write_stream):
        # Your MCP server logic here
        pass
```

#### Client Setup
```python
import boto3
from asyncmcp.sns_sqs.client import sns_sqs_client
from asyncmcp import SnsSqsClientConfig

# Configure transport
config = SnsSqsClientConfig(
    sqs_queue_url="https://sqs.region.amazonaws.com/account/client-queue",
    sns_topic_arn="arn:aws:sns:region:account:mcp-responses"
)

# Create AWS clients
sqs_client = boto3.client('sqs')
sns_client = boto3.client('sns')

async def main():
    async with sns_sqs_client(config, sqs_client, sns_client) as (read_stream, write_stream):
        # Your MCP client logic here
        pass
```

### SQS-Only Transport

#### Server Setup
```python
import boto3
from asyncmcp.sqs.server import sqs_server
from asyncmcp import SqsServerConfig

# Configure transport
config = SqsServerConfig(
    read_queue_url="https://sqs.region.amazonaws.com/account/server-requests"
)

# Create AWS client
sqs_client = boto3.client('sqs')

async def main():
    async with sqs_server(config, sqs_client) as (read_stream, write_stream):
        # Your MCP server logic here
        pass
```

#### Client Setup
```python
import boto3
from asyncmcp.sqs.client import sqs_client
from asyncmcp import SqsClientConfig

# Configure transport
config = SqsClientConfig(
    read_queue_url="https://sqs.region.amazonaws.com/account/server-requests",
    response_queue_url="https://sqs.region.amazonaws.com/account/client-responses"
)

# Create AWS client
sqs_boto_client = boto3.client('sqs')

async def main():
    async with sqs_client(config, sqs_boto_client) as (read_stream, write_stream):
        # Your MCP client logic here
        pass
```

### Webhook Transport

#### Server Setup
```python
import anyio
from asyncmcp.webhook.manager import WebhookSessionManager
from asyncmcp import WebhookServerConfig
from mcp.server.lowlevel import Server

# Configure transport
config = WebhookServerConfig(
    timeout_seconds=30.0,
    max_retries=0
)

# Create MCP server
app = Server("my-webhook-server")

async def main():
    session_manager = WebhookSessionManager(app, config)
    async with session_manager.run():
        # Server runs as ASGI application - mount with your web framework
        # Example with uvicorn: uvicorn app:session_manager.asgi_app --host 0.0.0.0 --port 8000
        await anyio.sleep_forever()
```

#### Client Setup
```python
from asyncmcp import webhook_client, WebhookClientConfig

# Configure transport
config = WebhookClientConfig(
    server_url="http://localhost:8000/mcp/request",
    timeout_seconds=30.0,
    max_retries=0
)

async def main():
    async with webhook_client(config) as (read_stream, write_stream, client):
        # Your MCP client logic here
        # Make sure to start the client's webhook server to receive responses
        # See examples/webhook_client.py for complete implementation
        pass
```

#### Production Deployment
For production webhook servers, you'll need to:

1. **Deploy with a proper ASGI server**:
```python
# app.py
from asyncmcp.webhook.manager import WebhookSessionManager
from asyncmcp import WebhookServerConfig
from mcp.server.lowlevel import Server

config = WebhookServerConfig(timeout_seconds=30.0)
app = Server("production-server")
session_manager = WebhookSessionManager(app, config)

# Export ASGI application
asgi_app = session_manager.asgi_app()
```

```bash
# Deploy with uvicorn, gunicorn, or similar
uvicorn app:asgi_app --host 0.0.0.0 --port 8000 --workers 4
```

2. **Configure reverse proxy** (nginx, ALB, etc.) to route `/mcp/request` to your server

3. **Set up SSL/TLS** for secure webhook communication

4. **Configure proper timeouts** based on your MCP server response times

5. **Monitor webhook delivery** and implement retry logic in clients if needed

### StreamableHTTP + Webhook Transport

#### Server Setup
```python
import asyncio
import anyio
from asyncmcp.streamable_http_webhook.manager import StreamableHTTPWebhookSessionManager
from asyncmcp import StreamableHTTPWebhookConfig, webhook_tool
from mcp.server.lowlevel import Server

# Configure transport
config = StreamableHTTPWebhookConfig(
    json_response=False,  # Use SSE streaming
    timeout_seconds=30.0,
    webhook_timeout=30.0,
    webhook_max_retries=1
)

# Create MCP server
app = Server("streamable-http-webhook-server")

# Register tools with selective webhook routing
@app.call_tool()
async def handle_tools(name: str, arguments: dict):
    if name == "quick_tool":
        # Standard tool - uses SSE streaming
        return [{"type": "text", "text": "Quick response via SSE"}]
    elif name == "async_tool":
        # Webhook tool - uses async webhook delivery
        await asyncio.sleep(5)  # Simulate long processing
        return [{"type": "text", "text": "Async response via webhook"}]

# Mark async tools with decorator (optional - used for documentation)
@webhook_tool(description="Long-running async tool", tool_name="async_tool")
async def async_tool_handler(arg: str):
    # This will be delivered via webhook
    return [{"type": "text", "text": f"Processed {arg} asynchronously"}]

async def main():
    # Explicitly specify which tools should use webhook delivery
    webhook_tools = {"async_tool"}  # Tools that will be delivered via webhook
    
    session_manager = StreamableHTTPWebhookSessionManager(
        app, 
        config, 
        server_path="/mcp", 
        stateless=False,
        webhook_tools=webhook_tools  # Explicit webhook tool registration
    )
    async with session_manager.run():
        # Deploy with uvicorn or similar ASGI server
        await anyio.sleep_forever()
```

#### Client Setup
```python
from asyncmcp import streamable_http_webhook_client, StreamableHTTPWebhookClientConfig

# Configure transport
config = StreamableHTTPWebhookClientConfig(
    server_url="http://localhost:8000/mcp",
    webhook_url="http://localhost:8001/webhook",
    client_id="my-client",
    timeout_seconds=30.0,
    max_retries=1
)

async def main():
    async with streamable_http_webhook_client(config) as (read_stream, write_stream, client):
        # Get webhook callback for handling async responses
        webhook_callback = await client.get_webhook_callback()
        
        # Set up webhook server to receive async responses
        # Standard tools get immediate SSE responses
        # Webhook-decorated tools get async webhook responses
        pass
```

## Working Examples

Complete working examples are available in the `/examples/` directory:

### SNS+SQS Examples
```bash
# Terminal 1: Start SNS+SQS server
uv run examples/website_server.py

# Terminal 2: Start SNS+SQS client
uv run examples/website_client.py
```

### SQS-Only Examples
```bash
# Terminal 1: Start SQS server  
uv run examples/website_server.py --transport sqs

# Terminal 2: Start SQS client
uv run examples/website_client.py --transport sqs
```

### Webhook Examples
```bash
# Terminal 1: Start webhook server
uv run examples/webhook_server.py --server-port 8000

# Terminal 2: Start webhook client
uv run examples/webhook_client.py --server-port 8000 --webhook-port 8001
```

### StreamableHTTP + Webhook Examples
```bash
# Terminal 1: Start StreamableHTTP + Webhook server
uv run examples/streamable_http_webhook_server.py --server-port 8000

# Terminal 2: Start StreamableHTTP + Webhook client  
uv run examples/streamable_http_webhook_client.py --server-url http://localhost:8000/mcp --webhook-url http://localhost:8001/webhook
```

**Setup Requirements**: For AWS-based transports (SNS+SQS, SQS), you need LocalStack running locally. See `/examples/README.md` for detailed setup instructions.

## Limitations

### General
- **Response Handling**: Async nature means responses may not be immediate
- **Session Context**: Storage mechanism handled by server application, not transport

### AWS-Based Transports (SNS+SQS, SQS)
- **Message Size**: SQS message size limits apply (256KB standard, 2MB extended)
- **Ordering**: Standard SQS doesn't guarantee message ordering (use FIFO queues if needed)
- **AWS Dependencies**: Requires AWS credentials and proper IAM permissions
- **Cost**: AWS charges apply for message processing

### Webhook Transport
- **HTTP Dependencies**: Requires stable HTTP connectivity between client and server
- **Firewall Requirements**: Both client and server need accessible HTTP endpoints
- **No Built-in Persistence**: Messages not stored if endpoints are unreachable
- **Session Limits**: Server enforces maximum concurrent session limits (default: 1000)

## Testing

### Unit Tests

```bash
uv run pytest
```



## Contributing

We welcome contributions and discussions about async MCP architectures!

### Development Setup

```bash
git clone https://github.com/bh-rat/asyncmcp.git
cd asyncmcp
uv sync
pre-commit install
```

Code formatting and linting is enforced via pre-commit hooks using ruff.
The hooks automatically format code and block commits with unfixable linting issues in `src/`.

---

## License

Apache License 2.0 - see [LICENSE](LICENSE) file for details.

## Links

- **MCP Specification**: [https://spec.modelcontextprotocol.io](https://spec.modelcontextprotocol.io)
