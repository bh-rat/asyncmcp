#!/usr/bin/env python3
"""MCP client for testing the proxy server using the MCP SDK.

This example shows how to properly connect to the proxy server
using the MCP SDK's sse_client.
"""

import asyncio
import sys
from typing import Optional

import click
from mcp.client.session import ClientSession
from mcp.client.sse import sse_client


async def run_client(proxy_url: str, auth_token: Optional[str] = None):
    """Connect to proxy and run interactive session."""
    headers = {}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    print(f"🔌 Connecting to proxy at {proxy_url}...")

    # Use MCP SDK's sse_client
    async with sse_client(proxy_url, headers=headers) as (read, write):
        print("✅ SSE connection established")

        # Create MCP session
        async with ClientSession(read, write) as session:
            print("✅ Client session created")

            # Initialize the session
            print("\n📋 Initializing MCP session...")
            result = await session.initialize()

            print(f"✅ Session initialized!")
            print(f"   Server: {result.serverInfo.name} v{result.serverInfo.version}")
            print(f"   Protocol: {result.protocolVersion}")

            # List tools
            print("\n🔧 Listing available tools...")
            tools_result = await session.list_tools()

            if not tools_result.tools:
                print("❌ No tools available")
                return

            print(f"✅ Found {len(tools_result.tools)} tool(s):")
            for tool in tools_result.tools:
                print(f"   • {tool.name}: {tool.description or 'No description'}")

            # Interactive loop
            print("\n" + "=" * 60)
            print("Interactive Mode - Commands:")
            print("  call <tool_name> <arg1>=<value1> <arg2>=<value2> ...")
            print("  list - List tools again")
            print("  quit - Exit")
            print("=" * 60)

            while True:
                try:
                    command = input("\n> ").strip()

                    if command.lower() in ["quit", "exit"]:
                        print("👋 Goodbye!")
                        break

                    elif command.lower() == "list":
                        tools_result = await session.list_tools()
                        print(f"Available tools:")
                        for tool in tools_result.tools:
                            print(f"   • {tool.name}: {tool.description or 'No description'}")

                    elif command.lower().startswith("call "):
                        # Parse command
                        parts = command[5:].split()
                        if not parts:
                            print("❌ Usage: call <tool_name> <arg>=<value> ...")
                            continue

                        tool_name = parts[0]
                        arguments = {}

                        # Parse arguments
                        for part in parts[1:]:
                            if "=" in part:
                                key, value = part.split("=", 1)
                                arguments[key] = value

                        # Call tool
                        print(f"\n🚀 Calling tool '{tool_name}'...")
                        if arguments:
                            print(f"   Arguments: {arguments}")

                        try:
                            result = await session.call_tool(tool_name, arguments)

                            print(f"✅ Tool executed successfully!")
                            for i, content in enumerate(result.content):
                                if hasattr(content, "text"):
                                    text = content.text
                                    if len(text) > 200:
                                        print(f"\n📄 Result {i + 1}: {text[:200]}...")
                                        print(f"   (Total: {len(text)} characters)")
                                    else:
                                        print(f"\n📄 Result {i + 1}: {text}")
                                else:
                                    print(f"\n📦 Result {i + 1}: {content}")

                        except Exception as e:
                            print(f"❌ Error: {e}")

                    else:
                        print(f"❌ Unknown command: {command}")
                        print("   Type 'help' for available commands")

                except (KeyboardInterrupt, EOFError):
                    print("\n👋 Goodbye!")
                    break
                except Exception as e:
                    print(f"❌ Error: {e}")


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
    """MCP client for testing proxy servers."""
    try:
        asyncio.run(run_client(url, auth_token))
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")
        sys.exit(0)
    except Exception as e:
        print(f"❌ Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
