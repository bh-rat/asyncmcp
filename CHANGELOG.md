# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2025-07-09

### Added
- Initial release of AsyncMCP
- `sns_sqs` transport layer for MCP clients and servers
- Examples which uses mcp Server with `sns_sqs` transport

### Features
- **Transport Configuration**: `SQSTransportConfig`
- **Type Safety**: Full type hint support with `py.typed` marker
- **AWS Integration**: Native boto3 integration for SQS and SNS services

### Documentation
- Comprehensive README with usage examples
- LocalStack based examples and setup
- Testing documentation and examples

### Testing
- Unit tests for client and server transports
- LocalStack-based testing environment

[Unreleased]: https://github.com/bh-rat/asyncmcp/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/bh-rat/asyncmcp/releases/tag/v0.1.0
