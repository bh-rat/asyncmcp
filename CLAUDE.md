# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AsyncMCP provides async transport layer implementations for MCP (Model Context Protocol), supporting AWS SQS, SNS+SQS, and webhook-based transports beyond the standard stdio and HTTP transports. The library enables MCP servers to handle requests asynchronously through queues and webhooks rather than requiring immediate responses.

## Development Commands

### Testing
```bash
# Run all tests
uv run pytest

# Run specific test module
uv run pytest tests/sqs/test_integration.py

# Run tests with specific markers
uv run pytest -m integration
uv run pytest -m unit
```

### Code Quality
```bash
# Type checking with pyright
uv run pyright

# Linting with ruff
uv run ruff check

# Format code with ruff
uv run ruff format
```

### Local Development Setup
```bash
# Install dependencies
uv sync

# Setup LocalStack for testing (required for SQS/SNS examples)
localstack start

# Setup LocalStack resources for examples
uv run examples/setup.py
```

### Example Usage
```bash
# SNS-SQS transport example
uv run examples/website_server.py  # Terminal 1
uv run examples/website_client.py  # Terminal 2

# SQS-only transport example  
uv run examples/website_server.py --transport sqs
uv run examples/website_client.py --transport sqs

# Webhook transport example
uv run examples/webhook_server.py --server-port 8000  # Terminal 1
uv run examples/webhook_client.py --server-port 8000 --webhook-port 8001  # Terminal 2
```

## Architecture

### Core Components

- **Common Layer** (`src/asyncmcp/common/`): Base protocols and server transport implementation
  - `protocols.py`: Defines `ServerTransportProtocol` interface 
  - `server.py`: Base `ServerTransport` class with stream management
  - `outgoing_event.py`: Event model for message forwarding
  - `client_state.py`: Client session state management

- **Transport Implementations**: Three transport mechanisms in separate modules
  - **SQS** (`src/asyncmcp/sqs/`): Queue-to-queue communication
  - **SNS+SQS** (`src/asyncmcp/sns_sqs/`): Topic-based pub/sub with SQS queues
  - **Webhook** (`src/asyncmcp/webhook/`): HTTP POST requests with webhook responses

### Session Management Pattern

Each transport type follows a consistent session manager pattern:
- `*Manager` classes handle multiple client sessions
- Individual transport instances handle single client sessions  
- Session managers route messages between central queues/topics and per-session transports
- Managers support dynamic session creation based on incoming requests

### Key Architectural Patterns

1. **Async Context Managers**: All transports use `async with` for resource management
2. **Memory Streams**: Internal communication uses anyio memory object streams
3. **Protocol Compliance**: All transports implement `ServerTransportProtocol`
4. **Session Isolation**: Each client gets isolated transport instance with unique session ID

### Transport-Specific Details

**SQS Transport**: 
- Server listens on request queue, sends responses to client-specific response queues
- Client provides response queue URL in initialize request
- Uses SQS message attributes for metadata

**SNS+SQS Transport**:
- Server listens on SQS queue, publishes responses to SNS topic
- Clients subscribe to topic with individual SQS queues
- Topic-based message routing with filtering

**Webhook Transport**:
- Client sends HTTP POST to server endpoints
- Server responds via HTTP POST to client webhook URLs
- Client provides webhook URL in initialize request `_meta` field

## Testing Strategy

Tests are organized by transport type with consistent structure:
- `conftest.py`: Transport-specific fixtures and configuration
- `shared_fixtures.py`: Common test fixtures and utilities
- `test_client_transport.py`: Client-side transport unit tests
- `test_server_transport.py`: Server-side transport unit tests  
- `test_integration.py`: End-to-end integration tests

LocalStack is required for SQS/SNS testing. Webhook tests use httpx mock clients.

## Configuration

Transport configurations are defined in `utils.py` files within each transport module:
- `SqsTransportConfig`: SQS queue URLs and message attributes
- `SnsSqsServerConfig`/`SnsSqsClientConfig`: Topic ARNs and queue URLs
- `WebhookTransportConfig`: HTTP client settings and timeouts

All configs support customizable message attributes and transport-specific parameters.