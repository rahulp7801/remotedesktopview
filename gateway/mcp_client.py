"""
MCP Client for Gateway

Communicates with the Agent-S MCP server to execute desktop commands.

Uses stdio transport to communicate with the local MCP server process.
"""

import asyncio
import json
from typing import Any, Optional, Dict
from datetime import datetime
from pathlib import Path

from loguru import logger

from mcp import ClientSession
from mcp.client.sse import sse_client


class MCPClient:
    """
    MCP client for communicating with Agent-S server.

    Manages the lifecycle of the MCP connection and provides
    convenience methods for calling tools.
    """

    def __init__(self):
        """Initialize MCP client (not connected yet)."""
        self._session: Optional[ClientSession] = None
        self._read_stream = None
        self._write_stream = None
        self._connected = False
        self._server_process = None
        self._context_manager = None

    async def connect(self) -> None:
        """
        Connect to the MCP server via HTTP/SSE.

        Connects to MCP server running on http://localhost:8001
        """
        if self._connected:
            logger.debug("Already connected to MCP server")
            return

        logger.info("Connecting to MCP server via HTTP")

        try:
            # MCP server URL (running separately on port 8001)
            mcp_server_url = "http://localhost:8001/sse"

            # Connect via SSE client using async context manager
            self._context_manager = sse_client(mcp_server_url)
            self._read_stream, self._write_stream = await self._context_manager.__aenter__()

            # Create session
            self._session = ClientSession(self._read_stream, self._write_stream)

            # Initialize session
            await self._session.initialize()

            self._connected = True
            logger.info("Connected to MCP server successfully via HTTP")

            # Log available tools
            tools_result = await self._session.list_tools()
            tool_names = [tool.name for tool in tools_result.tools]
            logger.info(f"Available MCP tools: {tool_names}")

        except Exception as e:
            logger.exception("Failed to connect to MCP server via HTTP")
            self._connected = False
            raise RuntimeError(f"MCP HTTP connection failed: {e}") from e

    async def disconnect(self) -> None:
        """Disconnect from MCP server."""
        if not self._connected:
            return

        logger.info("Disconnecting from MCP server")

        try:
            # Exit the context manager if it exists
            if self._context_manager:
                await self._context_manager.__aexit__(None, None, None)
                self._context_manager = None

            if self._session:
                # No explicit close method in MCP SDK, just cleanup
                self._session = None

            self._read_stream = None
            self._write_stream = None
            self._connected = False

            logger.info("Disconnected from MCP server")

        except Exception as e:
            logger.error(f"Error during disconnect: {e}")

    def is_connected(self) -> bool:
        """Check if connected to MCP server."""
        return self._connected

    async def call_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        timeout_seconds: float = 30.0
    ) -> Dict[str, Any]:
        """
        Call an MCP tool with timeout.

        Args:
            tool_name: Name of the tool to call
            arguments: Tool arguments as dictionary
            timeout_seconds: Maximum time to wait for response

        Returns:
            Tool result as dictionary

        Raises:
            RuntimeError: If not connected or tool call fails
            asyncio.TimeoutError: If tool call exceeds timeout
        """
        if not self._connected:
            raise RuntimeError("Not connected to MCP server. Call connect() first.")

        logger.info(f"Calling MCP tool: {tool_name}")
        logger.debug(f"Arguments: {json.dumps(arguments, indent=2)}")

        start_time = datetime.now()

        try:
            # Call tool with timeout
            result = await asyncio.wait_for(
                self._session.call_tool(tool_name, arguments),
                timeout=timeout_seconds
            )

            execution_time = (datetime.now() - start_time).total_seconds()
            logger.info(f"Tool call completed in {execution_time:.2f}s")

            # Parse result from TextContent
            if result.content and len(result.content) > 0:
                content = result.content[0]

                if hasattr(content, "text"):
                    # Parse JSON response
                    result_data = json.loads(content.text)
                    logger.debug(f"Tool result: {json.dumps(result_data, indent=2)}")
                    return result_data
                else:
                    logger.warning(f"Unexpected content type: {type(content)}")
                    return {"error": "Unexpected content type from MCP server"}
            else:
                logger.warning("Empty result from MCP server")
                return {"error": "Empty result from MCP server"}

        except asyncio.TimeoutError:
            execution_time = (datetime.now() - start_time).total_seconds()
            logger.error(f"Tool call timed out after {execution_time:.2f}s (limit: {timeout_seconds}s)")
            raise

        except Exception as e:
            execution_time = (datetime.now() - start_time).total_seconds()
            logger.exception(f"Tool call failed after {execution_time:.2f}s")
            raise RuntimeError(f"MCP tool call failed: {e}") from e

    async def execute_desktop_command(
        self,
        prompt: str,
        screenshot_before: bool = False,
        screenshot_after: bool = True
    ) -> Dict[str, Any]:
        """
        Execute a desktop command via Agent-S.

        Convenience wrapper for the execute_desktop_command tool.

        Args:
            prompt: Natural language command
            screenshot_before: Capture screenshot before action
            screenshot_after: Capture screenshot after action

        Returns:
            Result dictionary with status, message, etc.
        """
        return await self.call_tool(
            "execute_desktop_command",
            {
                "prompt": prompt,
                "screenshot_before": screenshot_before,
                "screenshot_after": screenshot_after
            }
        )

    async def capture_screen(self, save_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Capture a screenshot.

        Convenience wrapper for the capture_screen tool.

        Args:
            save_path: Optional custom path for screenshot

        Returns:
            Result dictionary with screenshot_path
        """
        args = {}
        if save_path:
            args["save_path"] = save_path

        return await self.call_tool("capture_screen", args)

    async def get_active_applications(self) -> Dict[str, Any]:
        """
        Get list of running applications.

        Convenience wrapper for the get_active_applications tool.

        Returns:
            Result dictionary with applications list
        """
        return await self.call_tool("get_active_applications", {})


# Global singleton client
_mcp_client: Optional[MCPClient] = None


async def get_mcp_client() -> MCPClient:
    """
    Get the global MCP client instance.

    Creates and connects if not already done.

    Returns:
        Connected MCP client
    """
    global _mcp_client

    if _mcp_client is None:
        _mcp_client = MCPClient()
        await _mcp_client.connect()

    elif not _mcp_client.is_connected():
        # Reconnect if disconnected
        await _mcp_client.connect()

    return _mcp_client


async def initialize_mcp_client() -> None:
    """Initialize MCP client on server startup."""
    logger.info("Initializing MCP client")
    await get_mcp_client()


async def shutdown_mcp_client() -> None:
    """Shutdown MCP client on server shutdown."""
    global _mcp_client

    if _mcp_client and _mcp_client.is_connected():
        logger.info("Shutting down MCP client")
        await _mcp_client.disconnect()
