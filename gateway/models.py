"""
Pydantic models for VAPI webhook integration and API responses.

This module defines all data models for:
- VAPI webhook payloads (incoming requests)
- Tool call responses (outgoing responses)
- Health check and status endpoints

All models use Pydantic V2 syntax with comprehensive validation and documentation.

Usage:
    from gateway.models import VAPIWebhookPayload, VAPIToolCallResponse

    # Parse incoming webhook
    payload = VAPIWebhookPayload.model_validate(request_json)

    # Create tool call response
    response = VAPIToolCallResponse(
        results=[ToolCallResult(toolCallId="123", result="Done")]
    )
"""

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


# =============================================================================
# Enums
# =============================================================================


class WebhookMessageType(str, Enum):
    """
    Types of VAPI webhook messages.

    Each type triggers different handling logic in the webhook endpoint.
    """

    TOOL_CALLS = "tool-calls"
    ASSISTANT_REQUEST = "assistant-request"
    STATUS_UPDATE = "status-update"
    END_OF_CALL_REPORT = "end-of-call-report"
    TRANSCRIPT = "transcript"
    HANG = "hang"
    SPEECH_UPDATE = "speech-update"
    CONVERSATION_UPDATE = "conversation-update"


class ToolCallStatus(str, Enum):
    """Status of a tool call execution."""

    SUCCESS = "success"
    FAILED = "failed"
    ERROR = "error"
    TIMEOUT = "timeout"


# =============================================================================
# VAPI Webhook Models (Incoming)
# =============================================================================


class FunctionCall(BaseModel):
    """
    Nested function call details in OpenAI format.

    VAPI uses OpenAI's function calling structure where function details
    are nested under a 'function' key.
    """

    name: str = Field(
        ...,
        description="Name of the function/tool to execute.",
        min_length=1,
    )

    arguments: str | dict[str, Any] = Field(
        default_factory=dict,
        description="Parameters as JSON string or dict.",
    )


class ToolCall(BaseModel):
    """
    Represents a single tool call from VAPI in OpenAI format.

    When VAPI's assistant determines a tool should be called, it sends
    a tool-calls webhook with one or more ToolCall objects in OpenAI's
    function calling format (nested structure).

    Attributes:
        type: Always "function" for function calls.
        id: Unique identifier for this tool call. Must be included in the
            response to match the result with the request.
        function: Nested object containing function name and arguments.

    Example VAPI format:
        {
            "type": "function",
            "id": "call_abc123",
            "function": {
                "name": "execute_desktop_command",
                "arguments": "{\"prompt\": \"Open Chrome\"}"
            }
        }
    """

    type: str = Field(
        default="function",
        description="Type of tool call (always 'function').",
    )

    id: str = Field(
        ...,
        description="Unique identifier for this tool call (toolCallId). "
                    "Must be returned in the response to match results.",
        min_length=1,
    )

    function: FunctionCall = Field(
        ...,
        description="Function call details (name and arguments).",
    )

    @property
    def name(self) -> str:
        """Get function name from nested structure."""
        return self.function.name

    @property
    def arguments(self) -> dict[str, Any]:
        """Get arguments as dict, parsing JSON string if needed."""
        import json
        args = self.function.arguments
        if isinstance(args, str):
            try:
                return json.loads(args) if args else {}
            except json.JSONDecodeError:
                return {}
        return args

    model_config = {
        "populate_by_name": True,
        "json_schema_extra": {
            "examples": [
                {
                    "type": "function",
                    "id": "call_abc123xyz",
                    "function": {
                        "name": "execute_desktop_command",
                        "arguments": "{\"prompt\": \"Open Google Chrome\", \"screenshot_after\": true}",
                    },
                },
                {
                    "type": "function",
                    "id": "call_def456uvw",
                    "function": {
                        "name": "capture_screen",
                        "arguments": "{}",
                    },
                },
            ]
        },
    }


class VAPIWebhookMessage(BaseModel):
    """
    The message wrapper within a VAPI webhook payload.

    Different message types contain different fields. The `type` field
    determines which optional fields will be present.

    Attributes:
        type: Event type determining the webhook's purpose.
        toolCallList: List of tool calls (present for 'tool-calls' events).
        call: Metadata about the current call session.
        timestamp: ISO 8601 timestamp of when the event occurred.
        assistant: Assistant configuration (for 'assistant-request' events).
        transcript: Transcription data (for 'transcript' events).
        artifact: Call artifacts (for 'end-of-call-report' events).
        status: Call status information (for 'status-update' events).

    Example:
        >>> message = VAPIWebhookMessage(
        ...     type="tool-calls",
        ...     toolCallList=[
        ...         {"id": "call_123", "name": "capture_screen", "arguments": {}}
        ...     ]
        ... )
        >>> message.type
        'tool-calls'
    """

    type: str = Field(
        ...,
        description="Event type: 'tool-calls', 'assistant-request', "
                    "'status-update', 'end-of-call-report', 'transcript', etc.",
    )

    toolCallList: Optional[list[ToolCall]] = Field(
        default=None,
        description="List of tool calls to execute. "
                    "Present only for 'tool-calls' message type.",
    )

    call: Optional[dict[str, Any]] = Field(
        default=None,
        description="Call session metadata including callId, phoneNumber, "
                    "status, and other call-specific information.",
    )

    timestamp: Optional[int | str] = Field(
        default=None,
        description="Timestamp of the event (Unix milliseconds as int or ISO 8601 string).",
    )

    assistant: Optional[dict[str, Any]] = Field(
        default=None,
        description="Assistant configuration. Present for 'assistant-request' events. "
                    "Contains assistant settings and capabilities.",
    )

    transcript: Optional[str] = Field(
        default=None,
        description="Speech transcription text. Present for 'transcript' events.",
    )

    artifact: Optional[dict[str, Any]] = Field(
        default=None,
        description="Call artifacts like recordings and summaries. "
                    "Present for 'end-of-call-report' events.",
    )

    status: Optional[str] = Field(
        default=None,
        description="Call status (e.g., 'in-progress', 'ended'). "
                    "Present for 'status-update' events.",
    )

    @field_validator("type")
    @classmethod
    def validate_message_type(cls, v: str) -> str:
        """
        Validate message type against known values.

        Logs a warning for unknown types but doesn't reject them
        to allow for forward compatibility with new VAPI features.
        """
        known_types = {t.value for t in WebhookMessageType}
        if v not in known_types:
            import logging
            logging.warning(f"Unknown VAPI message type received: {v}")
        return v

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "type": "tool-calls",
                    "toolCallList": [
                        {
                            "id": "call_abc123",
                            "name": "execute_desktop_command",
                            "arguments": {"prompt": "Open Safari"},
                        }
                    ],
                    "call": {"id": "call_xyz789", "status": "in-progress"},
                    "timestamp": "2024-01-15T10:30:00Z",
                }
            ]
        }
    }


class VAPIWebhookPayload(BaseModel):
    """
    Top-level VAPI webhook payload.

    All VAPI webhooks are wrapped in this structure with a `message` field
    containing the actual event data.

    Attributes:
        message: The webhook message containing event type and data.

    Example:
        >>> import json
        >>> payload_json = '''
        ... {
        ...     "message": {
        ...         "type": "tool-calls",
        ...         "toolCallList": [
        ...             {"id": "call_123", "name": "capture_screen", "arguments": {}}
        ...         ]
        ...     }
        ... }
        ... '''
        >>> payload = VAPIWebhookPayload.model_validate_json(payload_json)
        >>> payload.message.type
        'tool-calls'
        >>> len(payload.message.toolCallList)
        1
    """

    message: VAPIWebhookMessage = Field(
        ...,
        description="The webhook message containing event type and associated data.",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "message": {
                        "type": "tool-calls",
                        "toolCallList": [
                            {
                                "id": "call_abc123",
                                "name": "execute_desktop_command",
                                "arguments": {
                                    "prompt": "Click the Chrome icon in the Dock"
                                },
                            }
                        ],
                        "call": {"id": "call_session_xyz"},
                        "timestamp": "2024-01-15T10:30:00Z",
                    }
                }
            ]
        }
    }

    @property
    def is_tool_call(self) -> bool:
        """Check if this webhook is a tool-calls event."""
        return self.message.type == WebhookMessageType.TOOL_CALLS.value

    @property
    def is_assistant_request(self) -> bool:
        """Check if this webhook is an assistant-request event."""
        return self.message.type == WebhookMessageType.ASSISTANT_REQUEST.value

    @property
    def tool_calls(self) -> list[ToolCall]:
        """Get tool calls, returning empty list if not a tool-calls event."""
        return self.message.toolCallList or []


# =============================================================================
# VAPI Response Models (Outgoing)
# =============================================================================


class ToolCallResult(BaseModel):
    """
    Result of a single tool execution.

    Each tool call from VAPI must receive a corresponding result with
    a matching toolCallId. The result string is what VAPI will speak
    back to the user.

    Attributes:
        toolCallId: Must match the `id` from the original ToolCall.
        result: Human-readable result that VAPI will speak to the user.
                Keep it concise and natural-sounding.

    Example:
        >>> result = ToolCallResult(
        ...     toolCallId="call_abc123",
        ...     result="I've opened Chrome and navigated to Gmail. "
        ...            "You should see your inbox now."
        ... )
        >>> result.model_dump()
        {'toolCallId': 'call_abc123', 'result': "I've opened Chrome..."}
    """

    toolCallId: str = Field(
        ...,
        description="Identifier matching the original tool call's id. "
                    "VAPI uses this to correlate results with requests.",
        min_length=1,
    )

    result: str = Field(
        ...,
        description="Human-readable result that VAPI will speak to the user. "
                    "Should be concise, natural-sounding, and informative.",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "toolCallId": "call_abc123xyz",
                    "result": "I've opened Google Chrome and navigated to Gmail. "
                              "Your inbox is now visible on the screen.",
                },
                {
                    "toolCallId": "call_def456uvw",
                    "result": "Screenshot captured and saved. "
                              "The current screen shows the System Settings app.",
                },
            ]
        }
    }

    @classmethod
    def success(cls, tool_call_id: str, message: str) -> "ToolCallResult":
        """
        Factory method for creating a success result.

        Args:
            tool_call_id: The original tool call ID.
            message: Success message to speak to user.

        Returns:
            ToolCallResult with the success message.
        """
        return cls(toolCallId=tool_call_id, result=message)

    @classmethod
    def error(cls, tool_call_id: str, error_message: str) -> "ToolCallResult":
        """
        Factory method for creating an error result.

        Args:
            tool_call_id: The original tool call ID.
            error_message: Error description to speak to user.

        Returns:
            ToolCallResult with the error message.
        """
        return cls(
            toolCallId=tool_call_id,
            result=f"I encountered an issue: {error_message}. Please try again.",
        )


class VAPIToolCallResponse(BaseModel):
    """
    Response payload for tool-calls webhook events.

    Contains results for all tool calls in the original request.
    Each tool call must have a corresponding result in the results list.

    Attributes:
        results: List of results, one for each tool call received.

    Example:
        >>> response = VAPIToolCallResponse(
        ...     results=[
        ...         ToolCallResult(
        ...             toolCallId="call_123",
        ...             result="Chrome is now open"
        ...         ),
        ...         ToolCallResult(
        ...             toolCallId="call_456",
        ...             result="Screenshot saved"
        ...         ),
        ...     ]
        ... )
        >>> len(response.results)
        2
    """

    results: list[ToolCallResult] = Field(
        ...,
        description="List of results corresponding to each tool call. "
                    "Order should match the original toolCallList order.",
        min_length=1,
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "results": [
                        {
                            "toolCallId": "call_abc123",
                            "result": "I've opened Chrome and navigated to Gmail.",
                        }
                    ]
                }
            ]
        }
    }

    @classmethod
    def from_tool_calls(
        cls,
        tool_calls: list[ToolCall],
        results: dict[str, str],
    ) -> "VAPIToolCallResponse":
        """
        Factory method to create response from tool calls and their results.

        Args:
            tool_calls: Original list of tool calls from webhook.
            results: Dictionary mapping tool call IDs to result strings.

        Returns:
            VAPIToolCallResponse with all results populated.

        Example:
            >>> tool_calls = [ToolCall(id="123", name="test", arguments={})]
            >>> results = {"123": "Task completed"}
            >>> response = VAPIToolCallResponse.from_tool_calls(tool_calls, results)
        """
        return cls(
            results=[
                ToolCallResult(
                    toolCallId=tc.id,
                    result=results.get(tc.id, "No result available"),
                )
                for tc in tool_calls
            ]
        )


class VAPIAssistantResponse(BaseModel):
    """
    Response payload for assistant-request webhook events.

    When VAPI sends an assistant-request, respond with the assistant ID
    to use for this call session.

    Attributes:
        assistantId: The VAPI assistant ID to use for handling this call.

    Example:
        >>> response = VAPIAssistantResponse(
        ...     assistantId="asst_abc123xyz"
        ... )
        >>> response.model_dump()
        {'assistantId': 'asst_abc123xyz'}
    """

    assistantId: str = Field(
        ...,
        description="VAPI assistant ID to use for this call session. "
                    "Must match an assistant configured in your VAPI dashboard.",
        min_length=1,
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"assistantId": "asst_remote_desktop_agent_v1"},
            ]
        }
    }


# =============================================================================
# Health Check and Status Models
# =============================================================================


class HealthCheckResponse(BaseModel):
    """
    Response model for the /health endpoint.

    Provides comprehensive health status including all dependent services.
    Useful for monitoring, load balancers, and debugging.

    Attributes:
        status: Overall health status ("healthy", "degraded", "unhealthy").
        mcp_connected: Whether MCP server connection is active.
        agent_s_ready: Whether Agent-S is initialized and ready for commands.
        timestamp: ISO 8601 timestamp of the health check.
        version: Application version string.
        uptime_seconds: Time since application started.
        last_error: Most recent error message, if any.

    Example:
        >>> health = HealthCheckResponse(
        ...     status="healthy",
        ...     mcp_connected=True,
        ...     agent_s_ready=True
        ... )
        >>> health.is_healthy
        True
    """

    status: str = Field(
        default="healthy",
        description="Overall health status: 'healthy' (all systems operational), "
                    "'degraded' (some features unavailable), or "
                    "'unhealthy' (critical failures).",
    )

    mcp_connected: bool = Field(
        default=False,
        description="Whether the MCP (Model Context Protocol) server connection "
                    "is established and responsive.",
    )

    agent_s_ready: bool = Field(
        default=False,
        description="Whether Agent-S GUI automation engine is initialized "
                    "and ready to execute desktop commands.",
    )

    timestamp: Optional[str] = Field(
        default=None,
        description="ISO 8601 timestamp of this health check.",
    )

    version: Optional[str] = Field(
        default=None,
        description="Application version string (e.g., '1.0.0').",
    )

    uptime_seconds: Optional[float] = Field(
        default=None,
        ge=0,
        description="Seconds since the application started.",
    )

    last_error: Optional[str] = Field(
        default=None,
        description="Most recent error message, if any. Useful for debugging "
                    "degraded or unhealthy states.",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "status": "healthy",
                    "mcp_connected": True,
                    "agent_s_ready": True,
                    "timestamp": "2024-01-15T10:30:00Z",
                    "version": "1.0.0",
                    "uptime_seconds": 3600.5,
                    "last_error": None,
                },
                {
                    "status": "degraded",
                    "mcp_connected": True,
                    "agent_s_ready": False,
                    "timestamp": "2024-01-15T10:30:00Z",
                    "version": "1.0.0",
                    "uptime_seconds": 120.0,
                    "last_error": "Agent-S initialization timed out",
                },
            ]
        }
    }

    @property
    def is_healthy(self) -> bool:
        """Check if all systems are operational."""
        return self.status == "healthy" and self.mcp_connected and self.agent_s_ready

    @property
    def is_degraded(self) -> bool:
        """Check if system is in degraded state."""
        return self.status == "degraded" or (self.mcp_connected != self.agent_s_ready)

    @classmethod
    def healthy(
        cls,
        version: Optional[str] = None,
        uptime_seconds: Optional[float] = None,
    ) -> "HealthCheckResponse":
        """
        Factory method for creating a healthy response.

        Args:
            version: Application version string.
            uptime_seconds: Time since application started.

        Returns:
            HealthCheckResponse indicating healthy status.
        """
        return cls(
            status="healthy",
            mcp_connected=True,
            agent_s_ready=True,
            timestamp=datetime.utcnow().isoformat() + "Z",
            version=version,
            uptime_seconds=uptime_seconds,
        )

    @classmethod
    def degraded(
        cls,
        mcp_connected: bool = True,
        agent_s_ready: bool = False,
        error: Optional[str] = None,
    ) -> "HealthCheckResponse":
        """
        Factory method for creating a degraded response.

        Args:
            mcp_connected: Whether MCP is connected.
            agent_s_ready: Whether Agent-S is ready.
            error: Error message describing the degradation.

        Returns:
            HealthCheckResponse indicating degraded status.
        """
        return cls(
            status="degraded",
            mcp_connected=mcp_connected,
            agent_s_ready=agent_s_ready,
            timestamp=datetime.utcnow().isoformat() + "Z",
            last_error=error,
        )

    @classmethod
    def unhealthy(cls, error: str) -> "HealthCheckResponse":
        """
        Factory method for creating an unhealthy response.

        Args:
            error: Error message describing the failure.

        Returns:
            HealthCheckResponse indicating unhealthy status.
        """
        return cls(
            status="unhealthy",
            mcp_connected=False,
            agent_s_ready=False,
            timestamp=datetime.utcnow().isoformat() + "Z",
            last_error=error,
        )


# =============================================================================
# Desktop Command Models (Internal)
# =============================================================================


class DesktopCommandRequest(BaseModel):
    """
    Internal model for desktop command execution requests.

    Used by the MCP server to validate and process desktop commands
    before passing them to Agent-S.

    Attributes:
        prompt: Natural language description of the GUI task.
        screenshot_before: Whether to capture screen before action.
        screenshot_after: Whether to capture screen after action.
        timeout_seconds: Maximum time to wait for command completion.

    Example:
        >>> cmd = DesktopCommandRequest(
        ...     prompt="Open Chrome and go to gmail.com",
        ...     screenshot_after=True
        ... )
        >>> cmd.prompt
        'Open Chrome and go to gmail.com'
    """

    prompt: str = Field(
        ...,
        description="Natural language description of the GUI task to execute. "
                    "Be specific about UI elements and their visual appearance.",
        min_length=1,
        max_length=1000,
    )

    screenshot_before: bool = Field(
        default=False,
        description="Capture a screenshot before executing the action. "
                    "Useful for comparing before/after states.",
    )

    screenshot_after: bool = Field(
        default=True,
        description="Capture a screenshot after executing the action. "
                    "Provides visual confirmation of completed task.",
    )

    timeout_seconds: Optional[int] = Field(
        default=None,
        ge=1,
        le=300,
        description="Maximum seconds to wait for command completion. "
                    "Defaults to AGENT_S_TIMEOUT_SEC from settings.",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "prompt": "Click the Chrome icon in the Dock",
                    "screenshot_before": False,
                    "screenshot_after": True,
                    "timeout_seconds": 30,
                },
            ]
        }
    }


class DesktopCommandResult(BaseModel):
    """
    Result of a desktop command execution.

    Returned by the MCP server after Agent-S executes a command.
    Contains status, any screenshots, and error information.

    Attributes:
        status: Execution status (success, failed, error, timeout).
        message: Human-readable description of the result.
        steps_executed: List of steps Agent-S performed.
        screenshot_path: Path to the captured screenshot, if any.
        error_message: Error description if status is not success.
        suggestion: Suggested next action or retry approach.
        execution_time_ms: Time taken to execute the command.

    Example:
        >>> result = DesktopCommandResult(
        ...     status=ToolCallStatus.SUCCESS,
        ...     message="Opened Chrome successfully",
        ...     steps_executed=["Clicked Chrome icon in Dock", "Waited for window"],
        ...     screenshot_path="/tmp/screenshot.png"
        ... )
        >>> result.succeeded
        True
    """

    status: ToolCallStatus = Field(
        ...,
        description="Execution status indicating success or type of failure.",
    )

    message: str = Field(
        ...,
        description="Human-readable description of what happened. "
                    "Suitable for speaking to the user.",
    )

    steps_executed: list[str] = Field(
        default_factory=list,
        description="List of steps Agent-S performed during execution. "
                    "Useful for debugging and understanding the action flow.",
    )

    screenshot_path: Optional[str] = Field(
        default=None,
        description="Absolute path to the captured screenshot file, if any.",
    )

    error_message: Optional[str] = Field(
        default=None,
        description="Detailed error message if the command failed. "
                    "More technical than the user-facing message.",
    )

    suggestion: Optional[str] = Field(
        default=None,
        description="Suggested action to recover from failure or improve results. "
                    "E.g., 'Try rephrasing with more specific UI element descriptions'.",
    )

    execution_time_ms: Optional[int] = Field(
        default=None,
        ge=0,
        description="Time taken to execute the command in milliseconds.",
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "status": "success",
                    "message": "Opened Chrome and navigated to Gmail",
                    "steps_executed": [
                        "Found Chrome icon in Dock",
                        "Clicked Chrome icon",
                        "Waited for Chrome window",
                        "Typed gmail.com in address bar",
                        "Pressed Enter",
                    ],
                    "screenshot_path": "/tmp/cache/action_1705312200.png",
                    "execution_time_ms": 2500,
                },
            ]
        }
    }

    @property
    def succeeded(self) -> bool:
        """Check if the command executed successfully."""
        return self.status == ToolCallStatus.SUCCESS

    @property
    def failed(self) -> bool:
        """Check if the command failed."""
        return self.status in (ToolCallStatus.FAILED, ToolCallStatus.ERROR)

    @classmethod
    def success(
        cls,
        message: str,
        steps: Optional[list[str]] = None,
        screenshot_path: Optional[str] = None,
        execution_time_ms: Optional[int] = None,
    ) -> "DesktopCommandResult":
        """
        Factory method for creating a success result.

        Args:
            message: Success message describing what was done.
            steps: List of steps executed.
            screenshot_path: Path to confirmation screenshot.
            execution_time_ms: Execution time in milliseconds.

        Returns:
            DesktopCommandResult indicating success.
        """
        return cls(
            status=ToolCallStatus.SUCCESS,
            message=message,
            steps_executed=steps or [],
            screenshot_path=screenshot_path,
            execution_time_ms=execution_time_ms,
        )

    @classmethod
    def failure(
        cls,
        message: str,
        error: str,
        suggestion: Optional[str] = None,
    ) -> "DesktopCommandResult":
        """
        Factory method for creating a failure result.

        Args:
            message: User-facing failure message.
            error: Technical error description.
            suggestion: Suggested recovery action.

        Returns:
            DesktopCommandResult indicating failure.
        """
        return cls(
            status=ToolCallStatus.FAILED,
            message=message,
            error_message=error,
            suggestion=suggestion or "Try rephrasing with more specific UI element descriptions",
        )


# =============================================================================
# Exports
# =============================================================================


__all__ = [
    # Enums
    "WebhookMessageType",
    "ToolCallStatus",
    # VAPI Incoming Models
    "ToolCall",
    "VAPIWebhookMessage",
    "VAPIWebhookPayload",
    # VAPI Response Models
    "ToolCallResult",
    "VAPIToolCallResponse",
    "VAPIAssistantResponse",
    # Health Check
    "HealthCheckResponse",
    # Desktop Command Models
    "DesktopCommandRequest",
    "DesktopCommandResult",
]
