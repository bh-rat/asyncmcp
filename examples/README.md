# MCP Local CLI Testing

This directory an interactive CLI client example showing how asyncmcp can be used.

## Prerequisites

- **LocalStack** - Running on `localhost:4566`

## Steps

### 1. Run [localstack](https://www.localstack.cloud/)

```bash
pip install localstack
```

```bash
localstack start
```

### 2. Setup LocalStack Resources
Sets up the infrastructure on localstack - topics, queues and subscriptions for requests and responses.

```bash
uv run setup.py cleanup # cleans up resources
uv run setup.py
```

### 3. Start the MCP Transport Server (Terminal 1)

```bash
uv run website_server.py
```

### 4. Start the CLI (Terminal 2) 

```bash
uv run website_client.py
```

### 5. Try the workflow

In the interactive client (Terminal 2):
```
Quick Interactive MCP Client
Commands: init, tools, call <tool_name> <params...>, quit
Example: call fetch url=https://github.com/bh-rat/asyncmcp
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
call fetch url=https://github.com/bh-rat/asyncmcp
📤 Sending tools/call request...
✅ Tool result:
   📄 <!doctype html><html itemscope="" ...
```

The whole MCP communication happened through queues and topics. 