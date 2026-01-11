#!/usr/bin/env python3
"""
Simplified MCP Server using FastAPI HTTP

Exposes Agent-S as HTTP endpoints instead of using MCP SDK transport.
This is simpler and more reliable than MCP's SSE/stdio transports.
"""

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

# Add project root to Python path
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn
from loguru import logger

# Import Agent-S integration
from brain.agent_manager import get_agent_instance, AgentExecutionError,  initialize_agent, shutdown_agent
from brain.tools.desktop_command import (
    execute_desktop_command,
    capture_screen_sync,
    get_active_apps,
)
from brain.task_state import (
    get_state_manager,
    get_real_status,
    describe_current_screen,
)
from brain.tools.claude_code import (
    execute_claude_code,
    ask_claude_code,
    check_claude_cli_available,
)

# FastAPI app
app = FastAPI(title="Agent-S MCP Server", version="1.0.0")


# Request/Response models
class ExecuteCommandRequest(BaseModel):
    prompt: str
    screenshot_before: bool = False
    screenshot_after: bool = True
    force_agent_s: bool = False  # Skip AppleScript fast path, use Agent-S only


class CaptureScreenRequest(BaseModel):
    save_path: str | None = None


class ClaudeCodeRequest(BaseModel):
    instruction: str
    project: str | None = None
    timeout_seconds: int = 300


class ToolCallResponse(BaseModel):
    status: str
    message: str | None = None
    error: str | None = None
    data: Dict[str, Any] | None = None


@app.on_event("startup")
async def startup():
    """Initialize Agent-S on startup."""
    logger.info("=== Agent-S MCP Server Starting ===")
    logger.info(f"Initializing Agent-S...")

    try:
        await initialize_agent()
        logger.info("Agent-S initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize Agent-S: {e}")
        logger.warning("Server will start but tool calls may fail")

    logger.info("=== MCP Server Ready ===")


@app.on_event("shutdown")
async def shutdown_event():
    """Clean up Agent-S on shutdown."""
    logger.info("Shutting down Agent-S MCP Server")
    await shutdown_agent()


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    claude_cli_available = await check_claude_cli_available()
    return {
        "status": "healthy",
        "tools": [
            "execute_desktop_command",
            "capture_screen",
            "get_active_applications",
            "get_status",
            "describe_screen",
            # "execute_claude_code"  # Disabled to force visual activation via desktop_command
        ],
        "claude_cli_available": claude_cli_available,
        "timestamp": datetime.now().isoformat()
    }


@app.post("/tools/execute_desktop_command")
async def api_execute_desktop_command(request: ExecuteCommandRequest) -> ToolCallResponse:
    """Execute a desktop command via Agent-S."""
    logger.info(f"Tool call: execute_desktop_command | prompt='{request.prompt}'" + (" [FORCE AGENT-S]" if request.force_agent_s else ""))

    try:
        result = await execute_desktop_command(
            prompt=request.prompt,
            screenshot_after=request.screenshot_after,
            force_agent_s=request.force_agent_s
        )

        return ToolCallResponse(
            status=result.get("status", "success"),
            message=result.get("message"),
            error=result.get("error"),
            data=result
        )

    except Exception as e:
        logger.exception(f"Tool execution error: {e}")
        return ToolCallResponse(
            status="error",
            error=str(e),
            message="Internal server error"
        )


@app.post("/tools/capture_screen")
async def api_capture_screen(request: CaptureScreenRequest) -> ToolCallResponse:
    """Capture a screenshot."""
    logger.info(f"Tool call: capture_screen | path={request.save_path}")

    try:
        save_path = request.save_path
        if not save_path:
            save_path = f"cache/screenshot_{datetime.now().timestamp()}.png"

        # Ensure cache directory exists
        Path(save_path).parent.mkdir(parents=True, exist_ok=True)

        # Capture screenshot
        await asyncio.to_thread(capture_screen_sync, save_path)

        return ToolCallResponse(
            status="success",
            message=f"Screenshot captured",
            data={
                "screenshot_path": save_path,
                "timestamp": datetime.now().isoformat()
            }
        )

    except Exception as e:
        logger.exception(f"Screenshot capture error: {e}")
        return ToolCallResponse(
            status="error",
            error=str(e),
            message="Failed to capture screenshot"
        )


@app.get("/tools/get_active_applications")
async def api_get_active_applications() -> ToolCallResponse:
    """Get list of active applications."""
    logger.info("Tool call: get_active_applications")

    try:
        apps = await asyncio.to_thread(get_active_apps)

        return ToolCallResponse(
            status="success",
            message=f"Found {len(apps)} applications",
            data={
                "applications": apps,
                "count": len(apps),
                "timestamp": datetime.now().isoformat()
            }
        )

    except Exception as e:
        logger.exception(f"Get applications error: {e}")
        return ToolCallResponse(
            status="error",
            error=str(e),
            message="Failed to get applications"
        )


@app.get("/tools/get_status")
async def api_get_status() -> ToolCallResponse:
    """
    Get REAL current status of the system.

    This is the key tool for awareness - it returns:
    - Whether a task is currently running
    - Progress of the current task
    - Result of the last completed task
    - Actual status, not canned responses
    """
    logger.info("Tool call: get_status")

    try:
        status = await get_real_status()

        return ToolCallResponse(
            status="success",
            message=status.get("status_message", "Status retrieved"),
            data=status
        )

    except Exception as e:
        logger.exception(f"Get status error: {e}")
        return ToolCallResponse(
            status="error",
            error=str(e),
            message="Failed to get status"
        )


@app.post("/tools/describe_screen")
async def api_describe_screen() -> ToolCallResponse:
    """
    Capture and describe what's currently on screen.

    This gives the agent "eyes" - it:
    - Takes a screenshot
    - Uses Claude vision to describe what's visible
    - Returns the active application
    - Provides real visual context
    """
    logger.info("Tool call: describe_screen")

    try:
        result = await describe_current_screen()

        return ToolCallResponse(
            status="success",
            message=result.get("description", "Screen captured"),
            data=result
        )

    except Exception as e:
        logger.exception(f"Describe screen error: {e}")
        return ToolCallResponse(
            status="error",
            error=str(e),
            message="Failed to describe screen"
        )


@app.post("/tools/execute_claude_code")
async def api_execute_claude_code(request: ClaudeCodeRequest) -> ToolCallResponse:
    """
    Execute Claude Code CLI with the given instruction.

    This allows users to:
    - Fix bugs via phone call
    - Make code changes
    - Ask questions about the codebase
    - Run development tasks

    The task runs in the background with progress tracking.
    Use get_status to check progress.
    """
    logger.info(f"Tool call: execute_claude_code | instruction='{request.instruction[:80]}...'")

    try:
        result = await execute_claude_code(
            instruction=request.instruction,
            project=request.project,
            timeout_seconds=request.timeout_seconds
        )

        return ToolCallResponse(
            status=result.get("status", "success"),
            message=result.get("message"),
            error=result.get("error"),
            data=result
        )

    except Exception as e:
        logger.exception(f"Claude Code execution error: {e}")
        return ToolCallResponse(
            status="error",
            error=str(e),
            message="Failed to execute Claude Code"
        )


def main():
    """Run the MCP server."""
    # Configure logging
    logger.remove()
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>",
        level="INFO"
    )

    # Ensure cache directory exists
    Path("cache").mkdir(exist_ok=True)

    # Run server on port 8001
    logger.info("Starting MCP Server on http://localhost:8001")
    uvicorn.run(
        app,
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
