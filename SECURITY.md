# Security Policy

## Supported Versions

We currently support security updates for the following versions:

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

## Reporting a Vulnerability

If you discover a security vulnerability in AsyncMCP, please report it responsibly:

### How to Report

1. **Email**: Send details to bharatgeleda@gmail.com
2. **Subject**: Use the subject line "AsyncMCP Security Vulnerability"
3. **Include**:
   - Description of the vulnerability
   - Steps to reproduce the issue
   - Potential impact assessment
   - Any suggested fixes (if you have them)

### Response Timeline

- **Initial Response**: Within 1-3 days
- **Investigation**: 1-7 days depending on complexity
- **Resolution**: As soon as possible, typically within 30 days

### What to Expect

- Acknowledgment of your report
- Regular updates on our investigation progress
- Credit in our security advisory (if you wish)
- Notification when the issue is resolved

### Security Considerations

AsyncMCP handles AWS message queues and works with boto3. Key security areas:

- **AWS Credentials**: Always use IAM roles and least-privilege access
- **Message Content**: Avoid sending sensitive data in messages
- **Queue Permissions**: Properly configure SQS and SNS permissions
- **LocalStack**: Only use for development, never in production

### Responsible Disclosure

We ask that you:

- Give us reasonable time to investigate and fix the issue
- Do not publicly disclose the vulnerability until we've had a chance to address it
- Do not access, modify, or delete data that doesn't belong to you

Thank you for helping keep AsyncMCP secure! 