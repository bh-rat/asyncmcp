# MCP Local CLI Testing

This directory contains an interactive CLI client example showing how asyncmcp can be used. 
The CLI allows to take actions and the server is an asyncmcp version of MCP's [fetch server example](https://github.com/modelcontextprotocol/servers/tree/main/src/fetch)

> [!CAUTION]
> This server can access local/internal IP addresses and may represent a security risk. Exercise caution when using this MCP server to ensure this does not expose any sensitive data.

## Prerequisites

- **LocalStack** - Running on `localhost:4566`

## Steps

### 1. Run [localstack](https://www.localstack.cloud/) - Only for sqs/sns_sqs

```bash
uv add localstack
```

```bash
localstack start
```

### 2. Setup LocalStack Resources - Only for sqs/sns_sqs
Sets up the infrastructure on localstack - topics, queues and subscriptions for requests and responses.

```bash
uv run setup.py cleanup # cleans up resources
uv run setup.py
```

### 3. Start the MCP Transport Server (Terminal 1)

```bash
# Using SNS-SQS transport (default)
uv run website_server.py

# Using SQS-only transport
uv run website_server.py --transport sqs

# Using Webhook transport
uv run webhook_server.py --server-port 8000

# Using StreamableHTTP + Webhook transport
uv run streamable_http_webhook_server.py --server-port 8000
```

The SQS/Webhook server will:
- Listen for messages on the server request queue or on http://localhost:8000 for webhook
- Create new sessions when `initialize` requests arrive
- Send responses to client-specific queues provided in the initialize request or client-specified webhook url.


### 4. Start the CLI (Terminal 2) 

```bash
# Using SNS-SQS transport (default)
uv run website_client.py

# Using SQS-only transport with dynamic queues
uv run website_client.py --transport sqs

# Using Webhook transport
uv run webhook_client.py --server-port 8000 --webhook-port 8001

# Using StreamableHTTP + Webhook transport
uv run streamable_http_webhook_client.py --server-url http://localhost:8000/mcp --webhook-url http://localhost:8001/webhook
```

The SQS/Webhook client will:
- Send its response queue URL/response topic/webhook URL in the `initialize` request parameters 
- Listen for responses on its own response queue or the webhook endpoint
- Allow the server to route responses correctly

### 5. Try the workflow

In the interactive client (Terminal 2):
```
Quick Interactive MCP Client
Commands: init, tools, call <tool_name> <params...>, quit
Example: call fetch url=https://google.com
🔗 Connected to MCP transport
>
init
📤 Sending initialize request...
✅ Initialized with server: mcp-website-fetcher
📤 Sent initialized notification
>
tools
📤 Sending tools/list request...
✅ Found 1 tools:
   • fetch: Fetches a website and returns its content
>
call fetch url=https://google.com
📤 Sending tools/call request...
✅ Tool result:
   📄 <!doctype html><html itemscope="" ...
```

The whole MCP communication happened through queues and topics.

The initialize request includes the client's response queue URL:
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": "2024-11-05",
    "capabilities": {},
    "clientInfo": {"name": "test-client", "version": "1.0"},
    "response_queue_url": "http://localhost:4566/000000000000/mcp-consumer"
  }
}
```


## Webhook Transport

### Webhook Flow

1. **Initialization**: Client sends `initialize` request with webhook URL in `_meta` field
2. **Session Creation**: Server creates session and stores webhook URL
3. **Request/Response**: Client sends HTTP requests, server responds via webhooks
4. **Session Management**: Server tracks session state (init_pending → initialized)

### Usage

```bash
# Terminal 1: Start webhook server
uv run webhook_server.py --server-port 8000

# Terminal 2: Start webhook client
uv run webhook_client.py --server-port 8000 --webhook-port 8001
```

### Example Session

```
🔗 Connected to MCP transport
> init
📤 Sending initialize request...
✅ Initialized with server: mcp-website-fetcher
📤 Sent initialized notification
> tools
📤 Sending tools/list request...
✅ Found 1 tools:
   • fetch: Fetches a website and returns its content
> call fetch url=https://google.com
📤 Sending tools/call request...
✅ Tool result:
   📄 <!doctype html><html itemscope="" ...
```

The MCP communication happens through HTTP requests and webhook responses.

## StreamableHTTP + Webhook Transport

### StreamableHTTP + Webhook Flow

1. **Initialization**: Client sends `initialize` request with webhook URL in `_meta` field
2. **Session Creation**: Server creates session and stores webhook URL for selective tool routing
3. **Tool Routing**: 
   - Standard tools use StreamableHTTP with SSE streaming (immediate response)
   - Tools explicitly configured in `webhook_tools` parameter use webhook delivery (async response)
4. **Dual Response Handling**: Single session supports both immediate SSE responses and async webhook callbacks

### Key Features

- **Selective Transport**: Tools explicitly routed based on `webhook_tools` configuration parameter
- **Single Session**: Both SSE and webhook responses share the same session ID
- **StreamableHTTP Base**: Built on MCP's StreamableHTTP transport with webhook extensions
- **Async Tool Support**: Long-running operations delivered via webhook without blocking SSE stream

### Usage

```bash
# Terminal 1: Start StreamableHTTP + Webhook server
uv run streamable_http_webhook_server.py --server-port 8000

# Terminal 2: Start StreamableHTTP + Webhook client
uv run streamable_http_webhook_client.py --server-url http://localhost:8000/mcp --webhook-url http://localhost:8001/webhook
```

### Example Session

```
🔗 StreamableHTTP + Webhook MCP Client
🚀 Interactive client with SSE and Webhook support
💡 Tools are automatically routed to SSE or Webhook delivery!

[StreamableHTTP+Webhook] > init
🔄 Initializing client session...
✅ Client session initialized
📋 Server: mcp-streamable-http-webhook-server

[StreamableHTTP+Webhook] > tools
📋 Listing available tools...
✅ Found 3 tools:
   • fetch_sync (📡 SSE): Fetch website content synchronously via StreamableHTTP SSE
   • analyze_async (🌐 Webhook): Perform deep website analysis asynchronously via webhook transport
   • server_info (📡 SSE): Get StreamableHTTP + Webhook server information and routing details

[StreamableHTTP+Webhook] > call fetch_sync url=https://google.com
🔄 Calling fetch_sync (📡 SSE)...
📡 Waiting for SSE response...
📄 [SSE] Response:
[SSE] Quick fetch from https://google.com:

<!doctype html><html itemscope="" ...

[StreamableHTTP+Webhook] > call analyze_async url=https://example.com  
🔄 Calling analyze_async (🌐 Webhook)...
⏳ Webhook response will arrive via webhook...

📥 [Webhook Response Received]
📄 Response:
[WEBHOOK] Deep Analysis Results for https://example.com
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 Processing: Completed via Webhook (Async)
⏱️  Processing Time: ~5 seconds
...
```

The MCP communication combines StreamableHTTP SSE streaming for immediate responses with webhook POST delivery for async operations.