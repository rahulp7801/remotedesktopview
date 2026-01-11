"""
Simplified MCP Client using HTTP

Makes HTTP requests to the MCP server instead of using MCP SDK transport.
This is simpler and more reliable.
"""

import asyncio
from typing import Any, Optional, Dict

import httpx
from loguru import logger


class SimpleMCPClient:
    """
    Simplified MCP client that uses HTTP requests.

    Communicates with MCP server via REST API instead of MCP protocol.
    """

    def __init__(self, base_url: str = "http://localhost:8001"):
        """Initialize client."""
        self._base_url = base_url
        self._client: Optional[httpx.AsyncClient] = None
        self._connected = False

    async def connect(self) -> None:
        """Connect to MCP server."""
        if self._connected:
            logger.debug("Already connected to MCP server")
            return

        logger.info(f"Connecting to MCP server at {self._base_url}")

        try:
            # Create HTTP client
            self._client = httpx.AsyncClient(base_url=self._base_url, timeout=30.0)

            # Test connection with health check
            response = await self._client.get("/health")
            response.raise_for_status()

            health_data = response.json()
            logger.info(f"Connected to MCP server successfully")
            logger.info(f"Available tools: {health_data.get('tools', [])}")

            self._connected = True

        except Exception as e:
            logger.exception(f"Failed to connect to MCP server: {e}")
            self._connected = False
            raise RuntimeError(f"MCP connection failed: {e}") from e

    async def disconnect(self) -> None:
        """Disconnect from MCP server."""
        if not self._connected:
            return

        logger.info("Disconnecting from MCP server")

        try:
            if self._client:
                await self._client.aclose()
                self._client = None

            self._connected = False
            logger.info("Disconnected from MCP server")

        except Exception as e:
            logger.error(f"Error during disconnect: {e}")

    def is_connected(self) -> bool:
        """Check if connected."""
        return self._connected

    async def execute_desktop_command(
        self,
        prompt: str,
        screenshot_before: bool = False,
        screenshot_after: bool = True,
        force_agent_s: bool = False
    ) -> Dict[str, Any]:
        """Execute a desktop command via Agent-S."""
        if not self._connected or not self._client:
            raise RuntimeError("Not connected to MCP server. Call connect() first.")

        logger.info(f"Calling execute_desktop_command | prompt='{prompt}'" + (" [FORCE AGENT-S]" if force_agent_s else ""))

        try:
            response = await self._client.post(
                "/tools/execute_desktop_command",
                json={
                    "prompt": prompt,
                    "screenshot_before": screenshot_before,
                    "screenshot_after": screenshot_after,
                    "force_agent_s": force_agent_s
                }
            )
            response.raise_for_status()

            result = response.json()
            logger.debug(f"Tool result: {result.get('status')}")

            # Merge status into data so webhook handler can check it
            data = result.get("data", {})
            data["status"] = result.get("status", "success")
            data["error"] = result.get("error")
            data["message"] = result.get("message")
            return data

        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error calling tool: {e.response.status_code}")
            return {
                "status": "error",
                "error": f"HTTP {e.response.status_code}: {e.response.text}"
            }

        except Exception as e:
            logger.exception(f"Error calling tool: {e}")
            return {
                "status": "error",
                "error": str(e)
            }

    async def capture_screen(self, save_path: Optional[str] = None) -> Dict[str, Any]:
        """Capture a screenshot."""
        if not self._connected or not self._client:
            raise RuntimeError("Not connected to MCP server. Call connect() first.")

        logger.info(f"Calling capture_screen | path={save_path}")

        try:
            response = await self._client.post(
                "/tools/capture_screen",
                json={"save_path": save_path}
            )
            response.raise_for_status()

            result = response.json()
            # Merge status into data so webhook handler can check it
            data = result.get("data", {})
            data["status"] = result.get("status", "success")
            data["error"] = result.get("error")
            return data

        except Exception as e:
            logger.exception(f"Error calling tool: {e}")
            return {
                "status": "error",
                "error": str(e)
            }

    async def get_active_applications(self) -> Dict[str, Any]:
        """Get list of active applications."""
        if not self._connected or not self._client:
            raise RuntimeError("Not connected to MCP server. Call connect() first.")

        logger.info("Calling get_active_applications")

        try:
            response = await self._client.get("/tools/get_active_applications")
            response.raise_for_status()

            result = response.json()
            # Merge status into data so webhook handler can check it
            data = result.get("data", {})
            data["status"] = result.get("status", "success")
            data["error"] = result.get("error")
            return data

        except Exception as e:
            logger.exception(f"Error calling tool: {e}")
            return {
                "status": "error",
                "error": str(e)
            }


# Global singleton client
_mcp_client: Optional[SimpleMCPClient] = None


async def get_mcp_client() -> SimpleMCPClient:
    """Get the global MCP client instance."""
    global _mcp_client

    if _mcp_client is None:
        _mcp_client = SimpleMCPClient()
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
