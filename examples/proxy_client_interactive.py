#!/usr/bin/env python3
"""Interactive MCP client for testing the proxy server.

This example provides an interactive client that connects to the proxy server
using the standard MCP SSE transport. It demonstrates:
1. Establishing an SSE connection with the proxy
2. Initializing the MCP session
3. Listing available tools
4. Calling tools interactively
5. Handling responses and errors

Usage:
    # Connect to local proxy server
    python proxy_client.py

    # Connect to proxy on different host/port
    python proxy_client.py --url http://localhost:9090/mcp

    # Connect with authentication
    python proxy_client.py --auth-token "secret-token"
"""

import asyncio
import json
import sys
from typing import Optional

import click
from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from mcp.types import Tool


class InteractiveProxyClient:
    """Interactive client for testing MCP proxy servers."""

    def __init__(self, proxy_url: str, auth_token: Optional[str] = None):
        self.proxy_url = proxy_url
        self.auth_token = auth_token
        self.session: Optional[ClientSession] = None
        self.tools: list[Tool] = []

    async def connect(self):
        """Connect to the proxy server and initialize session."""
        print(f"🔌 Connecting to proxy at {self.proxy_url}...")

        headers = {}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

        try:
            # Create SSE connection using context manager
            self.sse_context = sse_client(self.proxy_url, headers=headers)
            self.read, self.write = await self.sse_context.__aenter__()

            print("✅ Connected to proxy server (SSE established)")

            # Create MCP session
            self.session = ClientSession(self.read, self.write)
            await self.session.__aenter__()

            print("✅ MCP session created")

            # Initialize session
            print("\n📋 Initializing MCP session...")
            result = await self.session.initialize()

            print(f"✅ Session initialized!")
            print(f"   Server: {result.serverInfo.name} v{result.serverInfo.version}")
            print(f"   Protocol: {result.protocolVersion}")
            if result.capabilities:
                print(f"   Capabilities: {list(result.capabilities.model_dump(exclude_none=True).keys())}")

        except Exception as e:
            print(f"❌ Connection failed: {type(e).__name__}: {e}")
            # If it's an ExceptionGroup, show the underlying errors
            if hasattr(e, "__exceptions__"):
                for i, sub_e in enumerate(e.__exceptions__):
                    print(f"   Sub-error {i + 1}: {type(sub_e).__name__}: {sub_e}")
            raise

    async def list_tools(self):
        """List available tools from the server."""
        print("\n🔧 Fetching available tools...")
        result = await self.session.list_tools()
        self.tools = result.tools

        if not self.tools:
            print("❌ No tools available")
            return

        print(f"✅ Found {len(self.tools)} tool(s):")
        for i, tool in enumerate(self.tools):
            print(f"\n{i + 1}. {tool.name}")
            if tool.description:
                print(f"   Description: {tool.description}")
            if tool.inputSchema:
                schema = tool.inputSchema
                if hasattr(schema, "properties"):
                    props = schema.properties or {}
                    if props:
                        print("   Parameters:")
                        for name, prop in props.items():
                            required = name in (schema.required or [])
                            req_str = " (required)" if required else " (optional)"
                            desc = prop.get("description", "No description")
                            print(f"     - {name}: {desc}{req_str}")

    async def call_tool(self, tool_name: str, arguments: dict):
        """Call a tool with the given arguments."""
        print(f"\n🚀 Calling tool '{tool_name}'...")
        print(f"   Arguments: {json.dumps(arguments, indent=2)}")

        try:
            result = await self.session.call_tool(tool_name, arguments)

            print(f"✅ Tool executed successfully!")
            print(f"   Result has {len(result.content)} content block(s)")

            for i, content in enumerate(result.content):
                print(f"\n   Content {i + 1}:")
                if hasattr(content, "text"):
                    # TextContent
                    text = content.text
                    if len(text) > 200:
                        print(f"     Text: {text[:200]}...")
                        print(f"     (Total length: {len(text)} characters)")
                    else:
                        print(f"     Text: {text}")
                elif hasattr(content, "data"):
                    # ImageContent or other binary content
                    print(f"     Type: {content.type}")
                    print(f"     Data size: {len(content.data)} bytes")
                else:
                    print(f"     Type: {content.type}")
                    print(f"     Content: {content}")

        except Exception as e:
            print(f"❌ Error calling tool: {type(e).__name__}: {e}")

    async def interactive_loop(self):
        """Run the interactive command loop."""
        print("\n" + "=" * 60)
        print("Interactive MCP Proxy Client")
        print("=" * 60)
        print("\nCommands:")
        print("  list    - List available tools")
        print("  call    - Call a tool")
        print("  help    - Show this help")
        print("  quit    - Exit the client")
        print()

        while True:
            try:
                command = input("\n> ").strip().lower()

                if command == "quit" or command == "exit":
                    print("👋 Goodbye!")
                    break

                elif command == "help":
                    print("\nCommands:")
                    print("  list    - List available tools")
                    print("  call    - Call a tool")
                    print("  help    - Show this help")
                    print("  quit    - Exit the client")

                elif command == "list":
                    await self.list_tools()

                elif command == "call":
                    if not self.tools:
                        print("❌ No tools available. Run 'list' first.")
                        continue

                    # Show tools
                    print("\nAvailable tools:")
                    for i, tool in enumerate(self.tools):
                        print(f"  {i + 1}. {tool.name}")

                    # Get tool selection
                    try:
                        choice = input("\nSelect tool (number or name): ").strip()

                        # Try to parse as number
                        tool = None
                        try:
                            idx = int(choice) - 1
                            if 0 <= idx < len(self.tools):
                                tool = self.tools[idx]
                        except ValueError:
                            # Try to match by name
                            for t in self.tools:
                                if t.name.lower() == choice.lower():
                                    tool = t
                                    break

                        if not tool:
                            print("❌ Invalid tool selection")
                            continue

                        # Get arguments
                        print(f"\nCalling tool: {tool.name}")
                        if tool.inputSchema and hasattr(tool.inputSchema, "properties"):
                            props = tool.inputSchema.properties or {}
                            required = tool.inputSchema.required or []

                            arguments = {}
                            for name, prop in props.items():
                                is_required = name in required
                                desc = prop.get("description", "No description")
                                prompt = f"{name} ({desc})"
                                if is_required:
                                    prompt += " [required]"
                                else:
                                    prompt += " [optional]"
                                prompt += ": "

                                value = input(prompt).strip()
                                if value or is_required:
                                    # Try to parse as JSON
                                    try:
                                        arguments[name] = json.loads(value)
                                    except:
                                        # Use as string
                                        arguments[name] = value
                        else:
                            # No schema, get raw JSON
                            args_str = input("Arguments (JSON): ").strip()
                            if args_str:
                                arguments = json.loads(args_str)
                            else:
                                arguments = {}

                        # Call the tool
                        await self.call_tool(tool.name, arguments)

                    except (KeyboardInterrupt, EOFError):
                        print("\n❌ Cancelled")
                        continue
                    except Exception as e:
                        print(f"❌ Error: {e}")
                        continue

                else:
                    print(f"❌ Unknown command: {command}")
                    print("   Type 'help' for available commands")

            except (KeyboardInterrupt, EOFError):
                print("\n👋 Goodbye!")
                break
            except Exception as e:
                print(f"❌ Error: {e}")

    async def disconnect(self):
        """Disconnect from the proxy server."""
        if self.session:
            await self.session.__aexit__(None, None, None)
        if hasattr(self, "sse_context"):
            await self.sse_context.__aexit__(None, None, None)

    async def run(self):
        """Run the interactive client."""
        try:
            await self.connect()
            await self.interactive_loop()
        finally:
            await self.disconnect()


@click.command()
@click.option(
    "--url",
    default="http://localhost:8080/mcp",
    help="Proxy server URL",
)
@click.option(
    "--auth-token",
    help="Authentication token for the proxy",
)
def main(url: str, auth_token: Optional[str]):
    """Interactive MCP client for testing proxy servers."""
    client = InteractiveProxyClient(url, auth_token)

    try:
        asyncio.run(client.run())
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")
        sys.exit(0)
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
