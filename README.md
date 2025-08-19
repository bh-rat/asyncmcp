# asyncmcp - Async transport layers for MCP 


[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
![Project Status: Alpha](https://img.shields.io/badge/status-alpha-orange)
[![Documentation](https://img.shields.io/badge/docs-mintlify-blue)](https://asyncmcp.mintlify.app)

---

## 📚 Documentation

**[View Full Documentation](https://asyncmcp.mintlify.app)** - Comprehensive guides, API reference, and examples

- [Quickstart Guide](https://asyncmcp.mintlify.app/quickstart) - Get running in 5 minutes
- [Installation](https://asyncmcp.mintlify.app/installation) - Setup instructions
- [Examples](https://asyncmcp.mintlify.app/examples/basic-examples) - Working code examples
- [API Reference](https://asyncmcp.mintlify.app/concepts/overview) - Detailed API documentation

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

## Available Transports

- **[SQS Transport](https://asyncmcp.mintlify.app/transports/sqs)** - AWS Simple Queue Service for reliable queue-based messaging
- **[SNS+SQS Transport](https://asyncmcp.mintlify.app/transports/sns-sqs)** - Pub/sub messaging with topic-based routing and fanout
- **[Webhook Transport](https://asyncmcp.mintlify.app/transports/webhook)** - HTTP-based async messaging for web applications
- **[StreamableHTTP + Webhook](https://asyncmcp.mintlify.app/transports/streamable-http-webhook)** - Hybrid transport with SSE for sync and webhooks for async operations
- **[Proxy Server](https://asyncmcp.mintlify.app/proxy)** - Bridge standard MCP clients to async transports

## Installation

```bash
# Using uv (recommended)
uv add asyncmcp

# Using pip
pip install asyncmcp
```

For detailed setup instructions and requirements, see the [Installation Guide](https://asyncmcp.mintlify.app/installation).

## Quick Start

Check out the [Quickstart Guide](https://asyncmcp.mintlify.app/quickstart) to get running in 5 minutes.

## Examples

Complete working examples are available in the [`/examples`](https://github.com/bh-rat/asyncmcp/tree/main/examples) directory and documented in the [Examples Guide](https://asyncmcp.mintlify.app/examples/basic-examples).

## Contributing

We welcome contributions! Please see our [Contributing Guide](https://github.com/bh-rat/asyncmcp/blob/main/CONTRIBUTING.md) for details.

```bash
git clone https://github.com/bh-rat/asyncmcp.git
cd asyncmcp
uv sync
```

## License

Apache License 2.0 - see [LICENSE](LICENSE) file for details.

## Links

- **Documentation**: [asyncmcp.mintlify.app](https://asyncmcp.mintlify.app)
- **GitHub**: [github.com/bh-rat/asyncmcp](https://github.com/bh-rat/asyncmcp)
- **MCP Specification**: [spec.modelcontextprotocol.io](https://spec.modelcontextprotocol.io)