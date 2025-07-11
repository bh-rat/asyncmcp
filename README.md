# asyncmcp - Async transport layers for MCP 


[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python Version](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)


---

## Overview


A regular MCP Server but working over queues :

https://github.com/user-attachments/assets/4b775ff8-02ae-4730-a822-3e1cedf9d744


Quoting from the [official description](https://modelcontextprotocol.io/introduction) :<br/> 
> MCP is an open protocol that standardizes how applications provide context to LLMs.

But a lot of this context is not always readily available and takes time for the applications to process - think batch processing APIs, webhooks or queues. In these cases with the current transport layers, the MCP server would have to expose a light-weight polling wrapper in the MCP layer to allow waiting and polling for the tasks to be done. Although SSE does provide async functionalities but it comes with caveats. <br/>

asyncmcp explores supporting more of the async transport layer implementations for MCP clients and servers, beyond the officially supported stdio and Streamable Http transports. 

The whole idea of an **MCP server with async transport layer** is that it doesn't have to respond immediately to any requests. It can choose to direct them to internal queues for processing and the client doesn't have to stick around for the response.

## Current capabilities

### Transport layer : sns-sqs

- Server : Transport layer that listens to a queue for MCP requests and writes the responses to a topic
- Client : Transport layer that writes requests to a topic and listens to a queue for responses

## Installation and Usage

```bash
# Using uv (recommended)
uv add asyncmcp
```

```bash
# Using pip  
pip install asyncmcp
```

### Basic server setup

Note : we don't support FastMCP yet. The examples in this repo uses the [basic way of creating MCP servers and client](https://modelcontextprotocol.io/docs/concepts/architecture#implementation-example) 
<br/>
Here's a basic example of implementing an MCP server which supports sns-sqs as the transport layer:

```python
import boto3
from asyncmcp.sns_sqs.server import sns_sqs_server
from asyncmcp import SnsSqsTransportConfig

# Configure transport
config = SnsSqsTransportConfig(
    sqs_queue_url="https://sqs.region.amazonaws.com/account/service-queue",
    sns_topic_arn="arn:aws:sns:region:account:mcp-responses"
)  # more configurable params available.

# Create AWS clients
sqs_client = boto3.client('sqs')
sns_client = boto3.client('sns')

async def main():
    async with sns_sqs_server(config, sqs_client, sns_client) as (read_stream, write_stream):
        # Your MCP server logic here
        pass
```

### Basic client setup

Here's a basic example of implementing an MCP client which supports sns-sqs as the transport layer:

```python
import boto3
from asyncmcp.sns_sqs.client import sns_sqs_client
from asyncmcp import SnsSqsTransportConfig

# Configure transport
config = SnsSqsTransportConfig(
    sqs_queue_url="https://sqs.region.amazonaws.com/account/client-queue",
    sns_topic_arn="arn:aws:sns:region:account:mcp-requests"
)  # more configurable params available.

# Create AWS clients
sqs_client = boto3.client('sqs')
sns_client = boto3.client('sns')

async def main():
    async with sns_sqs_client(config, sqs_client, sns_client) as (read_stream, write_stream):
        # Your MCP client logic here
        pass
```

You can check full examples at `/examples/website_server.py` and `/examples/website_client.py`.
<br/>
Read more at `/examples/README.md`

## Limitations

- **Message Size**: For SQS - message size limits are applicable (256KB standard, 2MB extended)
- **Response Handling**: Async nature means responses may not be immediate
- **Session Context**: Storage mechanism handled by server application, not transport
- **Ordering**: Standard SQS doesn't guarantee message ordering

## Testing

### Unit Tests

```bash
uv run pytest
```



## Contributing

We welcome contributions and discussions about async MCP architectures!

### Development Setup

```bash
git clone https://github.com/bharatgeleda/asyncmcp.git
cd asyncmcp
uv sync
```

---

## License

Apache License 2.0 - see [LICENSE](LICENSE) file for details.

## Links

- **MCP Specification**: [https://spec.modelcontextprotocol.io](https://spec.modelcontextprotocol.io)
