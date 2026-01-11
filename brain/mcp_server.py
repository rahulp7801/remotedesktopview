#!/usr/bin/env python3
"""
MCP Server for Agent-S GUI Automation

Exposes Agent-S as MCP tools for Claude to orchestrate:
- execute_desktop_command: Natural language GUI automation
- capture_screen: Screenshot capture for verification
- get_active_applications: List running applications

This server uses stdio transport for local MCP communication.
"""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# Add project root to Python path
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import (
    Tool,
    TextContent,
    ImageContent,
    EmbeddedResource,
)
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.requests import Request as StarletteRequest
import uvicorn

# Import Agent-S integration
from brain.agent_manager import get_agent_instance, AgentExecutionError
from brain.tools.desktop_command import (
    execute_desktop_command,
    capture_screen_sync,
    get_active_apps,
)

# Configure logging
import logging
from loguru import logger

# Setup loguru to work with MCP
logger.remove()  # Remove default handler
logger.add(
    sys.stderr,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>",
    level="INFO"
)

# Create MCP server instance
app = Server("agent-s-mcp-server")

# Tool definitions
TOOLS = [
    Tool(
        name="execute_desktop_command",
        description="""Execute a natural language GUI command on the desktop using Agent-S vision-based automation.

Examples:
- "Open Google Chrome from the Dock"
- "Click the 'New Message' button in Mail"
- "Type 'hello@example.com' in the email field"
- "Navigate to System Settings and click Privacy & Security"

Agent-S uses hierarchical planning and vision detection to find UI elements robustly.
Works across different screen sizes, themes, and UI layouts.

Returns a JSON object with:
- status: "success", "failed", or "error"
- message: Human-readable result
- steps_executed: List of actions Agent-S performed
- screenshot_path: Optional path to verification screenshot""",
        inputSchema={
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Natural language description of the GUI action to perform"
                },
                "screenshot_before": {
                    "type": "boolean",
                    "description": "Capture screenshot before action (default: false)",
                    "default": False
                },
                "screenshot_after": {
                    "type": "boolean",
                    "description": "Capture screenshot after action for verification (default: true)",
                    "default": True
                }
            },
            "required": ["prompt"]
        }
    ),
    Tool(
        name="capture_screen",
        description="""Capture a screenshot of the current desktop state.

Useful for:
- Visual verification before/after actions
- Debugging UI element detection issues
- Providing visual context to Claude for planning

Returns the path to the saved screenshot.""",
        inputSchema={
            "type": "object",
            "properties": {
                "save_path": {
                    "type": "string",
                    "description": "Optional custom path to save screenshot (default: cache/screenshot_<timestamp>.png)"
                }
            }
        }
    ),
    Tool(
        name="get_active_applications",
        description="""List all currently running applications on the desktop.

Useful for:
- Checking if target app is already open before launching
- Understanding current desktop state
- Planning multi-app workflows

Returns a JSON list of application names.""",
        inputSchema={
            "type": "object",
            "properties": {}
        }
    )
]


@app.list_tools()
async def list_tools() -> list[Tool]:
    """Return available MCP tools."""
    logger.info("MCP client requested tool list")
    return TOOLS


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent | ImageContent]:
    """
    Handle MCP tool calls from Claude.

    Routes to appropriate Agent-S functions and returns results.
    """
    logger.info(f"Tool call received: {name}")
    logger.debug(f"Arguments: {json.dumps(arguments, indent=2)}")

    try:
        if name == "execute_desktop_command":
            result = await _handle_execute_desktop_command(arguments)

        elif name == "capture_screen":
            result = await _handle_capture_screen(arguments)

        elif name == "get_active_applications":
            result = await _handle_get_active_applications(arguments)

        else:
            error_msg = f"Unknown tool: {name}"
            logger.error(error_msg)
            result = {"error": error_msg}

        # Format result as TextContent
        result_text = json.dumps(result, indent=2, default=str)
        logger.info(f"Tool call completed: {name}")
        logger.debug(f"Result: {result_text}")

        return [TextContent(type="text", text=result_text)]

    except Exception as e:
        error_msg = f"Tool execution error: {type(e).__name__}: {str(e)}"
        logger.exception(f"Tool call failed: {name}")

        error_result = {
            "status": "error",
            "error": str(e),
            "error_type": type(e).__name__
        }

        return [TextContent(type="text", text=json.dumps(error_result, indent=2))]


async def _handle_execute_desktop_command(args: dict[str, Any]) -> dict[str, Any]:
    """Handle execute_desktop_command tool calls."""
    prompt = args.get("prompt")
    screenshot_before = args.get("screenshot_before", False)
    screenshot_after = args.get("screenshot_after", True)

    if not prompt:
        return {
            "status": "error",
            "error": "Missing required parameter: prompt"
        }

    logger.info(f"Executing desktop command: {prompt}")

    # Capture before screenshot if requested
    before_path = None
    if screenshot_before:
        logger.debug("Capturing before screenshot")
        before_path = f"cache/before_{datetime.now().timestamp()}.png"
        await asyncio.to_thread(capture_screen_sync, before_path)

    # Execute command via Agent-S
    try:
        result = await execute_desktop_command(
            prompt=prompt,
            screenshot_after=screenshot_after
        )

        if before_path:
            result["screenshot_before"] = before_path

        return result

    except AgentExecutionError as e:
        logger.error(f"Agent-S execution failed: {e}")
        return {
            "status": "failed",
            "error": str(e),
            "prompt": prompt
        }


async def _handle_capture_screen(args: dict[str, Any]) -> dict[str, Any]:
    """Handle capture_screen tool calls."""
    save_path = args.get("save_path")

    if not save_path:
        # Generate default path
        save_path = f"cache/screenshot_{datetime.now().timestamp()}.png"

    logger.info(f"Capturing screenshot to: {save_path}")

    try:
        # Ensure cache directory exists
        Path(save_path).parent.mkdir(parents=True, exist_ok=True)

        # Capture screenshot
        await asyncio.to_thread(capture_screen_sync, save_path)

        return {
            "status": "success",
            "screenshot_path": save_path,
            "timestamp": datetime.now().isoformat()
        }

    except Exception as e:
        logger.error(f"Screenshot capture failed: {e}")
        return {
            "status": "error",
            "error": str(e),
            "requested_path": save_path
        }


async def _handle_get_active_applications(args: dict[str, Any]) -> dict[str, Any]:
    """Handle get_active_applications tool calls."""
    logger.info("Getting list of active applications")

    try:
        apps = await asyncio.to_thread(get_active_apps)

        return {
            "status": "success",
            "applications": apps,
            "count": len(apps),
            "timestamp": datetime.now().isoformat()
        }

    except Exception as e:
        logger.error(f"Failed to get active applications: {e}")
        return {
            "status": "error",
            "error": str(e)
        }


async def handle_sse(request: StarletteRequest):
    """Handle SSE connection for MCP."""
    async with SseServerTransport("/messages") as transport:
        await app.run(
            transport.read_stream,
            transport.write_stream,
            app.create_initialization_options()
        )


async def handle_messages(request: StarletteRequest):
    """Handle incoming MCP messages."""
    # This endpoint receives the client messages
    # SSE transport handles the actual communication
    pass


def create_starlette_app():
    """Create Starlette app for MCP HTTP server."""
    routes = [
        Route("/sse", endpoint=handle_sse),
        Route("/messages", endpoint=handle_messages, methods=["POST"]),
    ]
    return Starlette(routes=routes)


def main():
    """Run the MCP server with HTTP/SSE transport."""
    logger.info("Starting Agent-S MCP Server (HTTP)")
    logger.info(f"Available tools: {[tool.name for tool in TOOLS]}")

    # Ensure cache directory exists
    Path("cache").mkdir(exist_ok=True)

    # Create Starlette app
    starlette_app = create_starlette_app()

    # Run HTTP server on port 8001 (different from main gateway on 8000)
    logger.info("MCP server listening on http://localhost:8001")
    uvicorn.run(
        starlette_app,
        host="127.0.0.1",
        port=8001,
        log_level="info"
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("MCP server stopped by user")
    except Exception as e:
        logger.exception("MCP server crashed")
        sys.exit(1)
