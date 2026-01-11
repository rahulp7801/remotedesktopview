"""
Gateway module for the Voice-Controlled Remote Desktop Agent.

This module provides configuration management, Pydantic models,
and core gateway functionality.
"""

from gateway.config import Settings, get_settings, settings
from gateway.models import (
    # Enums
    WebhookMessageType,
    ToolCallStatus,
    # VAPI Incoming Models
    ToolCall,
    VAPIWebhookMessage,
    VAPIWebhookPayload,
    # VAPI Response Models
    ToolCallResult,
    VAPIToolCallResponse,
    VAPIAssistantResponse,
    # Health Check
    HealthCheckResponse,
    # Desktop Command Models
    DesktopCommandRequest,
    DesktopCommandResult,
)

__all__ = [
    # Config
    "Settings",
    "get_settings",
    "settings",
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
