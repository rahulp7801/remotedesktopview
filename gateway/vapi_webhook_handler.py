"""
VAPI Webhook Handler for Voice-Controlled Remote Desktop Agent.

This module provides a production-ready FastAPI router for handling VAPI webhook
events including tool-calls, assistant-request, status-update, and end-of-call-report.

The handler is designed to respond within VAPI's 7.5-second timeout constraint
with comprehensive logging and error handling.

Usage:
    from fastapi import FastAPI
    from gateway.vapi_webhook_handler import router

    app = FastAPI()
    app.include_router(router)

Phase 2 COMPLETE:
    - Integrated with MCP server for actual Agent-S execution
    - Screenshot capture implemented via MCP
    - Async tool execution with proper error handling
    - All three tools working: execute_desktop_command, capture_screen, get_active_applications

Phase 3 TODO:
    - Groq error analysis for Agent-S failures
    - Fetch.ai monitoring for system health
    - Advanced retry strategies
"""

# Load environment variables FIRST
from dotenv import load_dotenv
load_dotenv()

import asyncio
import re
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from loguru import logger

from gateway.config import Settings, get_settings
from gateway.mcp_client_simple import get_mcp_client
from gateway.models import (
    ToolCall,
    ToolCallResult,
    VAPIAssistantResponse,
    VAPIToolCallResponse,
    VAPIWebhookPayload,
    WebhookMessageType,
)


# =============================================================================
# Constants
# =============================================================================

# VAPI webhook timeout is 7.5 seconds - we track latency to ensure we respond in time
VAPI_TIMEOUT_SECONDS = 7.5
LATENCY_WARNING_THRESHOLD_SECONDS = 5.0


# =============================================================================
# Router Setup
# =============================================================================

router = APIRouter(
    prefix="/vapi",
    tags=["vapi"],
    responses={
        500: {"description": "Internal server error"},
        400: {"description": "Invalid webhook payload"},
    },
)


# =============================================================================
# Tool Handlers (MVP Phase 1 - Hardcoded Responses)
# =============================================================================


def _generate_immediate_response(prompt: str) -> str:
    """
    Generate an immediate spoken response based on the command prompt.

    This allows us to respond to VAPI quickly while the actual command
    executes in the background.
    """
    prompt_lower = prompt.lower()

    # Extract app name for common patterns
    app_patterns = [
        (r"open\s+(\w+)", "Opening {}"),
        (r"launch\s+(\w+)", "Launching {}"),
        (r"start\s+(\w+)", "Starting {}"),
        (r"click\s+(?:on\s+)?(?:the\s+)?(.+)", "Clicking {}"),
        (r"type\s+(.+)", "Typing the text"),
        (r"navigate\s+to\s+(.+)", "Navigating to {}"),
        (r"go\s+to\s+(.+)", "Going to {}"),
        (r"close\s+(\w+)", "Closing {}"),
        (r"find\s+(.+)", "Finding {}"),
        (r"search\s+(?:for\s+)?(.+)", "Searching for {}"),
    ]

    for pattern, response_template in app_patterns:
        match = re.search(pattern, prompt_lower)
        if match:
            target = match.group(1).strip()
            # Capitalize first letter for nicer speech
            target = target.capitalize() if target else target
            return response_template.format(target)

    # Default response
    return "Working on it"


CLASSIFIER_PROMPT = """You are a command routing classifier for Koda, a voice-controlled Mac automation system.

Your task: Analyze each user voice command and output ONLY one of these two words:
- AppleScript
- Agent_S3

# Decision Criteria

## Choose "AppleScript" when the command:
- Opens an application by name (e.g., "open Safari", "launch Mail")
- Opens a specific URL or website (e.g., "go to YouTube.com", "open Netflix")
- Performs a Google search (e.g., "search for weather", "Google best restaurants", "search for cats in Safari")
- Opens a standard folder by name (e.g., "open Downloads", "go to Documents folder", "show Desktop")
- Does basic Finder operations (e.g., "open a new Finder window")
- Combines two simple actions (e.g., "Open Safari and go to YouTube")
- Requires NO visual analysis or UI element interaction

## Choose "Agent_S3" when the command:
- Clicks specific UI elements (e.g., "click the download button", "press submit")
- Types into specific input fields (e.g., "enter my email", "type a message")
- Navigates menus or settings (e.g., "go to settings and enable dark mode")
- Requires multi-step reasoning (e.g., "find my video file and email it")
- Involves authentication (e.g., "log into my account")
- Needs to locate items visually (e.g., "find the red icon", "scroll to the bottom")
- Performs drag-and-drop or complex gestures
- Requires finding files by criteria (e.g., "open my most recent download", "find the latest document")
- Involves composing/sending messages (e.g., "send an email", "compose a message")

# Important Rules
1. When uncertain whether AppleScript can fully handle the task → choose Agent_S3
2. If the command is vague or incomplete → choose Agent_S3
3. Output ONLY the method name, nothing else

# Examples

User: "Open Chrome"
Output: AppleScript

User: "Search Google for pizza places"
Output: AppleScript

User: "Search for cats in Safari"
Output: AppleScript

User: "Go to amazon.com"
Output: AppleScript

User: "Open Safari and go to YouTube"
Output: AppleScript

User: "New Finder window"
Output: AppleScript

User: "Open Mail"
Output: AppleScript

User: "Open Downloads folder"
Output: AppleScript

User: "Go to my Documents"
Output: AppleScript

User: "Show Desktop folder"
Output: AppleScript

User: "Open Applications"
Output: AppleScript

User: "Click the buy now button"
Output: Agent_S3

User: "Find my presentation and share it"
Output: Agent_S3

User: "Type hello world into the search box"
Output: Agent_S3

User: "Open settings and change my password"
Output: Agent_S3

User: "Open my most recent download"
Output: Agent_S3

User: "Send an email to John"
Output: Agent_S3

User: "Log into Gmail"
Output: Agent_S3

Now classify this command:
User: "{command}"
Output:"""


async def _classify_command_with_llm(prompt: str) -> bool:
    """
    Use Claude to classify if a command requires Agent S3.
    
    Returns True if Agent S3 is needed, False if AppleScript can handle it.
    """
    import os
    try:
        import anthropic
    except ImportError:
        logger.warning("anthropic package not installed, falling back to Agent S3")
        return True
    
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set, falling back to Agent S3")
        return True
    
    try:
        client = anthropic.Anthropic(api_key=api_key)
        
        # Use Haiku for fast classification (~1-2s)
        response = client.messages.create(
            model="claude-3-5-haiku-latest",
            max_tokens=20,
            messages=[
                {"role": "user", "content": CLASSIFIER_PROMPT.format(command=prompt)}
            ]
        )
        
        result = response.content[0].text.strip().lower()
        logger.info(f"LLM classifier result: {result} | prompt='{prompt}'")
        
        # Return True if Agent S3 needed
        return "agent_s3" in result
        
    except Exception as e:
        logger.error(f"LLM classification failed: {e}, falling back to Agent S3")
        return True  # Safe fallback


async def _is_complex_command(prompt: str) -> bool:
    """
    Use LLM to intelligently classify if command needs Agent S3.
    
    Returns True if Agent S3 is needed, False if AppleScript can handle it.
    """
    return await _classify_command_with_llm(prompt)


async def _execute_command_background(prompt: str, screenshot_after: bool, tool_call_id: str):
    """Execute the desktop command in the background (fire and forget)."""
    try:
        mcp_client = await get_mcp_client()
        
        # Use LLM to classify if command needs Agent S3
        force_agent_s = await _is_complex_command(prompt)
        if force_agent_s:
            logger.info(f"LLM classified as complex, forcing Agent S3 | prompt='{prompt}'")
        
        result = await mcp_client.execute_desktop_command(
            prompt=prompt,
            screenshot_before=False,
            screenshot_after=screenshot_after,
            force_agent_s=force_agent_s
        )

        if result.get("status") == "success":
            logger.info(f"Background command succeeded | tool_call_id={tool_call_id} | prompt='{prompt}'")
        else:
            logger.warning(f"Background command failed | tool_call_id={tool_call_id} | error={result.get('error')}")

    except Exception as e:
        logger.error(f"Background command exception | tool_call_id={tool_call_id} | error={e}")


async def handle_execute_desktop_command(
    tool_call: ToolCall,
    settings: Settings,
) -> ToolCallResult:
    """
    Handle execute_desktop_command tool calls.

    Phase 2 UPDATE: Returns immediately with acknowledgment, executes in background.

    This is necessary because Agent-S takes 12-15 seconds (LLM reasoning + GUI automation)
    but VAPI has a ~7 second webhook timeout.

    Args:
        tool_call: The tool call from VAPI containing the prompt.
        settings: Application settings for configuration.

    Returns:
        ToolCallResult with immediate acknowledgment message.
    """
    prompt = tool_call.arguments.get("prompt", "")
    screenshot_after = tool_call.arguments.get("screenshot_after", True)

    logger.info(
        f"Executing desktop command | tool_call_id={tool_call.id} | prompt='{prompt}'"
    )

    if not prompt:
        logger.warning(f"Empty prompt received | tool_call_id={tool_call.id}")
        return ToolCallResult.error(
            tool_call_id=tool_call.id,
            error_message="No prompt provided for desktop command",
        )

    # Generate immediate response for VAPI (avoids timeout)
    immediate_response = _generate_immediate_response(prompt)

    # Fire off the command in the background (don't await)
    asyncio.create_task(
        _execute_command_background(prompt, screenshot_after, tool_call.id)
    )

    logger.info(
        f"Returning immediate response | tool_call_id={tool_call.id} | "
        f"response='{immediate_response}' | command executing in background"
    )

    # Return immediately so VAPI doesn't timeout
    return ToolCallResult.success(
        tool_call_id=tool_call.id,
        message=immediate_response,
    )


def _generate_hardcoded_response(prompt: str) -> str:
    """
    Generate hardcoded response based on prompt pattern matching.

    MVP Phase 1: Simple pattern matching for common commands.
    TODO Phase 2: Remove this function when MCP integration is complete.

    Args:
        prompt: The natural language prompt from the user.

    Returns:
        A hardcoded response string for the given prompt.
    """
    prompt_lower = prompt.lower().strip()

    # Pattern: "open [application]"
    open_match = re.search(r"open\s+(?:the\s+)?([a-zA-Z0-9\s]+?)(?:\s+app(?:lication)?)?$", prompt_lower)
    if open_match or "open" in prompt_lower:
        # Extract application name
        if open_match:
            app_name = open_match.group(1).strip().title()
        else:
            # Fallback: extract words after "open"
            parts = prompt_lower.split("open", 1)
            if len(parts) > 1:
                app_name = parts[1].strip().title()
            else:
                app_name = "the application"

        return f"Opening {app_name}. The application should now be visible on your screen."

    # Pattern: "click [element]"
    if "click" in prompt_lower:
        return "I've clicked the element you specified. The action has been completed."

    # Pattern: "type [text]" or "enter [text]"
    if "type" in prompt_lower or "enter" in prompt_lower:
        return "I've typed the text you specified into the active field."

    # Pattern: "go to [url]" or "navigate to [url]"
    if "go to" in prompt_lower or "navigate to" in prompt_lower:
        return "I've navigated to the specified URL. The page should now be loading."

    # Pattern: "scroll [direction]"
    if "scroll" in prompt_lower:
        return "I've scrolled the page as requested."

    # Pattern: "close [application/window]"
    if "close" in prompt_lower:
        return "I've closed the specified window or application."

    # Pattern: "search [query]"
    if "search" in prompt_lower:
        return "I've performed the search you requested."

    # Default response for unrecognized commands
    return f"I've executed the command: {prompt}. The action has been completed."


async def handle_capture_screen(
    tool_call: ToolCall,
    settings: Settings,
) -> ToolCallResult:
    """
    Handle capture_screen tool calls.

    Phase 2: Integrated with MCP server for actual screenshot capture.

    Args:
        tool_call: The tool call from VAPI.
        settings: Application settings.

    Returns:
        ToolCallResult with screenshot path or error message.
    """
    logger.info(f"Capture screen requested | tool_call_id={tool_call.id}")

    try:
        # Get MCP client and capture screenshot
        mcp_client = await get_mcp_client()

        save_path = tool_call.arguments.get("save_path")
        result = await mcp_client.capture_screen(save_path=save_path)

        if result.get("status") == "success":
            screenshot_path = result.get("screenshot_path", "unknown")
            logger.info(f"Screenshot captured | path={screenshot_path}")

            return ToolCallResult.success(
                tool_call_id=tool_call.id,
                message=f"Screenshot captured and saved to {screenshot_path}",
            )

        else:
            error = result.get("error", "Unknown error")
            logger.warning(f"Screenshot capture failed | error={error}")

            return ToolCallResult.error(
                tool_call_id=tool_call.id,
                error_message=f"Couldn't capture screenshot: {error}",
            )

    except Exception as e:
        logger.exception(f"Screenshot capture error | error={e}")

        return ToolCallResult.error(
            tool_call_id=tool_call.id,
            error_message="I encountered an error while capturing the screenshot.",
        )


async def handle_get_active_applications(
    tool_call: ToolCall,
    settings: Settings,
) -> ToolCallResult:
    """
    Handle get_active_applications tool calls.

    Phase 2: Integrated with MCP server for actual application listing.

    Args:
        tool_call: The tool call from VAPI.
        settings: Application settings.

    Returns:
        ToolCallResult with application list or error message.
    """
    logger.info(f"Get active applications requested | tool_call_id={tool_call.id}")

    try:
        # Get MCP client and fetch applications
        mcp_client = await get_mcp_client()

        result = await mcp_client.get_active_applications()

        if result.get("status") == "success":
            apps = result.get("applications", [])
            app_count = result.get("count", len(apps))

            # Format for TTS (keep it short)
            if app_count == 0:
                message = "No applications are currently running."
            elif app_count <= 5:
                app_list = ", ".join(apps[:5])
                message = f"Currently running: {app_list}"
            else:
                # Too many to list, give summary
                app_list = ", ".join(apps[:3])
                remaining = app_count - 3
                message = f"Running {app_count} applications including {app_list} and {remaining} others"

            logger.info(f"Active applications listed | count={app_count}")

            return ToolCallResult.success(
                tool_call_id=tool_call.id,
                message=message,
            )

        else:
            error = result.get("error", "Unknown error")
            logger.warning(f"Get active applications failed | error={error}")

            return ToolCallResult.error(
                tool_call_id=tool_call.id,
                error_message=f"Couldn't get application list: {error}",
            )

    except Exception as e:
        logger.exception(f"Get active applications error | error={e}")

        return ToolCallResult.error(
            tool_call_id=tool_call.id,
            error_message="I encountered an error while getting the application list.",
        )


def handle_unknown_tool(
    tool_call: ToolCall,
    settings: Settings,
) -> ToolCallResult:
    """
    Handle unknown/unrecognized tool calls.

    Args:
        tool_call: The unrecognized tool call.
        settings: Application settings.

    Returns:
        ToolCallResult with error message.
    """
    logger.warning(
        f"Unknown tool requested | tool_call_id={tool_call.id} | tool_name={tool_call.name}"
    )

    return ToolCallResult.error(
        tool_call_id=tool_call.id,
        error_message=f"Unknown tool: {tool_call.name}",
    )


# Tool handler dispatch map
TOOL_HANDLERS = {
    "execute_desktop_command": handle_execute_desktop_command,
    "capture_screen": handle_capture_screen,
    "get_active_applications": handle_get_active_applications,
}


# =============================================================================
# Webhook Event Handlers
# =============================================================================


async def process_tool_calls(
    tool_calls: list[ToolCall],
    settings: Settings,
) -> VAPIToolCallResponse:
    """
    Process a list of tool calls and return results.

    Handles async tool execution with proper error handling.

    Args:
        tool_calls: List of tool calls to process.
        settings: Application settings.

    Returns:
        VAPIToolCallResponse containing results for all tool calls.
    """
    results: list[ToolCallResult] = []

    for tool_call in tool_calls:
        handler = TOOL_HANDLERS.get(tool_call.name, handle_unknown_tool)

        try:
            # All handlers are now async
            result = await handler(tool_call, settings)
            results.append(result)

        except Exception as e:
            logger.exception(f"Tool handler error | tool={tool_call.name} | error={e}")

            # Return error result
            error_result = ToolCallResult.error(
                tool_call_id=tool_call.id,
                error_message=f"Internal error processing {tool_call.name}",
            )
            results.append(error_result)

    return VAPIToolCallResponse(results=results)


def handle_assistant_request(
    payload: VAPIWebhookPayload,
    settings: Settings,
) -> VAPIAssistantResponse:
    """
    Handle assistant-request webhook events.

    Returns the configured VAPI assistant ID to use for this call.

    Args:
        payload: The webhook payload.
        settings: Application settings containing assistant ID.

    Returns:
        VAPIAssistantResponse with the assistant ID.
    """
    call_id = payload.message.call.get("id", "unknown") if payload.message.call else "unknown"

    logger.info(
        f"Assistant request received | call_id={call_id} | "
        f"assistant_id={settings.VAPI_ASSISTANT_ID or 'not_configured'}"
    )

    if not settings.VAPI_ASSISTANT_ID:
        logger.error("VAPI_ASSISTANT_ID not configured")
        # Return empty string which VAPI will handle as an error
        return VAPIAssistantResponse(assistantId="")

    return VAPIAssistantResponse(assistantId=settings.VAPI_ASSISTANT_ID)


def handle_status_update(
    payload: VAPIWebhookPayload,
    settings: Settings,
) -> dict[str, Any]:
    """
    Handle status-update webhook events.

    Logs the call status for monitoring and debugging.

    Args:
        payload: The webhook payload containing status information.
        settings: Application settings.

    Returns:
        Empty dict (VAPI expects no specific response for status updates).
    """
    call_info = payload.message.call or {}
    call_id = call_info.get("id", "unknown")
    status = payload.message.status or call_info.get("status", "unknown")

    logger.info(
        f"Call status update | call_id={call_id} | status={status}"
    )

    # TODO Phase 2: Implement status tracking for analytics
    # Store status in database or emit metrics

    return {}


def handle_end_of_call_report(
    payload: VAPIWebhookPayload,
    settings: Settings,
) -> dict[str, Any]:
    """
    Handle end-of-call-report webhook events.

    Performs cleanup and logging for completed calls.

    Args:
        payload: The webhook payload containing call report.
        settings: Application settings.

    Returns:
        Empty dict (VAPI expects no specific response for end-of-call).
    """
    call_info = payload.message.call or {}
    call_id = call_info.get("id", "unknown")
    artifact = payload.message.artifact or {}

    # Extract useful information from the report
    transcript = artifact.get("transcript", "")
    recording_url = artifact.get("recordingUrl", "")
    duration = call_info.get("duration", 0)

    logger.info(
        f"Call ended | call_id={call_id} | duration={duration}s | "
        f"has_transcript={bool(transcript)} | has_recording={bool(recording_url)}"
    )

    # TODO Phase 2: Implement call report storage and analytics
    # - Store transcript for review
    # - Save recording URL
    # - Update call metrics
    # - Cleanup any temporary resources created during the call

    return {}


# =============================================================================
# Main Webhook Endpoint
# =============================================================================


@router.post(
    "/webhook",
    summary="VAPI Webhook Handler",
    description="Handles incoming webhook events from VAPI voice assistant service.",
    response_model=None,  # Response varies by event type
)
async def vapi_webhook(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> dict[str, Any] | VAPIToolCallResponse | VAPIAssistantResponse:
    """
    Main VAPI webhook endpoint.

    Processes incoming webhook events and returns appropriate responses
    based on the event type. Must respond within 7.5 seconds (VAPI timeout).

    Supported event types:
        - tool-calls: Execute tool functions and return results
        - assistant-request: Return assistant ID for the call
        - status-update: Log call status updates
        - end-of-call-report: Handle call completion and cleanup

    Args:
        request: The incoming FastAPI request.
        settings: Application settings (injected via dependency).

    Returns:
        Response appropriate for the webhook event type.

    Raises:
        HTTPException: If the payload is invalid or processing fails.
    """
    request_start_time = time.time()

    # Parse the raw request body
    try:
        body = await request.json()
    except Exception as e:
        logger.error(f"Failed to parse webhook body | error={e}")
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    # Validate and parse the webhook payload
    try:
        payload = VAPIWebhookPayload.model_validate(body)
    except Exception as e:
        logger.error(f"Failed to validate webhook payload | error={e}")
        raise HTTPException(status_code=400, detail=f"Invalid webhook payload: {e}")

    message_type = payload.message.type
    call_id = (
        payload.message.call.get("id", "unknown")
        if payload.message.call
        else "unknown"
    )

    logger.info(
        f"Webhook received | type={message_type} | call_id={call_id}"
    )

    # Route to appropriate handler based on event type
    try:
        if message_type == WebhookMessageType.TOOL_CALLS.value:
            if not payload.tool_calls:
                logger.warning(f"Tool calls event with no tools | call_id={call_id}")
                raise HTTPException(
                    status_code=400,
                    detail="tool-calls event received but no toolCallList provided",
                )
            response = await process_tool_calls(payload.tool_calls, settings)

        elif message_type == WebhookMessageType.ASSISTANT_REQUEST.value:
            response = handle_assistant_request(payload, settings)

        elif message_type == WebhookMessageType.STATUS_UPDATE.value:
            response = handle_status_update(payload, settings)

        elif message_type == WebhookMessageType.END_OF_CALL_REPORT.value:
            response = handle_end_of_call_report(payload, settings)

        else:
            # Handle unknown event types gracefully
            logger.warning(
                f"Unknown webhook event type | type={message_type} | call_id={call_id}"
            )
            response = {}

    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        logger.exception(f"Error processing webhook | type={message_type} | error={e}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal error processing webhook: {e}",
        )

    # Log latency and warn if approaching timeout
    elapsed_seconds = time.time() - request_start_time
    elapsed_ms = int(elapsed_seconds * 1000)

    if elapsed_seconds >= LATENCY_WARNING_THRESHOLD_SECONDS:
        logger.warning(
            f"Webhook response slow | type={message_type} | call_id={call_id} | "
            f"elapsed_ms={elapsed_ms} | threshold_ms={int(LATENCY_WARNING_THRESHOLD_SECONDS * 1000)}"
        )

    if elapsed_seconds >= VAPI_TIMEOUT_SECONDS:
        logger.error(
            f"Webhook response exceeded VAPI timeout | type={message_type} | "
            f"call_id={call_id} | elapsed_ms={elapsed_ms}"
        )

    logger.info(
        f"Webhook response sent | type={message_type} | call_id={call_id} | "
        f"elapsed_ms={elapsed_ms}"
    )

    # Return appropriate response type
    if isinstance(response, (VAPIToolCallResponse, VAPIAssistantResponse)):
        return response.model_dump()

    return response


# =============================================================================
# Health Check Endpoint
# =============================================================================


@router.get(
    "/health",
    summary="VAPI Webhook Handler Health Check",
    description="Returns health status of the VAPI webhook handler.",
)
async def health_check(
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """
    Health check endpoint for the VAPI webhook handler.

    Returns:
        Dict with health status and configuration info.
    """
    return {
        "status": "healthy",
        "service": "vapi_webhook_handler",
        "vapi_configured": settings.has_vapi_configured,
        "assistant_id_set": bool(settings.VAPI_ASSISTANT_ID),
        "environment": settings.ENV,
    }


# =============================================================================
# Exports
# =============================================================================


__all__ = [
    "router",
    "vapi_webhook",
    "health_check",
]
