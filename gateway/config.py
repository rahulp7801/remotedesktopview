"""
Configuration management for the Voice-Controlled Remote Desktop Agent.

This module uses pydantic-settings to provide type-safe, validated configuration
loaded from environment variables and .env files. All settings are centralized
here for easy management and documentation.

Usage:
    from gateway.config import settings

    # Access configuration values
    api_key = settings.VAPI_API_KEY
    timeout = settings.AGENT_S_TIMEOUT_SEC
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables and .env file.

    All configuration is validated at startup. Required fields without defaults
    will raise validation errors if not provided, ensuring the application
    fails fast with clear error messages.

    Environment variables take precedence over .env file values.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",  # Ignore extra env vars not defined here
    )

    # ==========================================================================
    # VAPI Configuration
    # ==========================================================================

    VAPI_API_KEY: str = Field(
        default="",
        description="API key for VAPI voice assistant service. "
                    "Required for voice-to-text and assistant functionality.",
    )

    VAPI_ASSISTANT_ID: str = Field(
        default="",
        description="Unique identifier for the VAPI assistant instance. "
                    "Used to route voice commands to the correct assistant.",
    )

    VAPI_PHONE_NUMBER: str = Field(
        default="",
        description="Phone number associated with the VAPI assistant. "
                    "Format should include country code (e.g., +1234567890).",
    )

    # ==========================================================================
    # External Service API Keys
    # ==========================================================================

    GROQ_API_KEY: str = Field(
        default="",
        description="API key for Groq LLM service. "
                    "Used for error analysis and intelligent response generation.",
    )

    FETCHAI_AGENT_ADDRESS: str = Field(
        default="",
        description="Address of the Fetch.ai agent for distributed monitoring. "
                    "Format: agent1q... (Fetch.ai agent address format).",
    )

    # ==========================================================================
    # Environment Configuration
    # ==========================================================================

    ENV: Literal["development", "production"] = Field(
        default="development",
        description="Application environment. "
                    "'development' enables debug logging and relaxed validation. "
                    "'production' enables stricter security and optimized performance.",
    )

    # ==========================================================================
    # Feature Flags
    # ==========================================================================

    ENABLE_SCREENSHOT_VERIFICATION: bool = Field(
        default=True,
        description="Enable automatic screenshot capture after GUI actions. "
                    "Useful for visual verification of completed tasks. "
                    "Disable to reduce latency and storage usage.",
    )

    ENABLE_GROQ_ERROR_ANALYSIS: bool = Field(
        default=False,
        description="Enable Groq LLM-powered analysis of Agent-S errors. "
                    "Provides intelligent suggestions for failed GUI operations. "
                    "Requires valid GROQ_API_KEY.",
    )

    ENABLE_FETCHAI_MONITORING: bool = Field(
        default=False,
        description="Enable Fetch.ai distributed monitoring integration. "
                    "Sends telemetry and health metrics to Fetch.ai agents. "
                    "Requires valid FETCHAI_AGENT_ADDRESS.",
    )

    # ==========================================================================
    # Timeout Configuration
    # ==========================================================================

    AGENT_S_TIMEOUT_SEC: int = Field(
        default=5,
        ge=1,
        le=300,
        description="Maximum time in seconds to wait for Agent-S GUI operations. "
                    "Increase for complex multi-step tasks. "
                    "Valid range: 1-300 seconds.",
    )

    MCP_TIMEOUT_SEC: int = Field(
        default=7,
        ge=1,
        le=300,
        description="Maximum time in seconds to wait for MCP tool responses. "
                    "Should be slightly higher than AGENT_S_TIMEOUT_SEC to allow "
                    "for overhead. Valid range: 1-300 seconds.",
    )

    # ==========================================================================
    # Server Configuration
    # ==========================================================================

    SERVER_HOST: str = Field(
        default="0.0.0.0",
        description="Host address for the FastAPI server to bind to. "
                    "Use '0.0.0.0' to accept connections from any interface, "
                    "or '127.0.0.1' for localhost only.",
    )

    SERVER_PORT: int = Field(
        default=8000,
        ge=1,
        le=65535,
        description="Port number for the FastAPI server. "
                    "Valid range: 1-65535. Ports below 1024 may require root.",
    )

    WEBHOOK_URL: str = Field(
        default="",
        description="URL to send webhook notifications for completed actions. "
                    "Leave empty to disable webhooks. "
                    "Must be a valid HTTP/HTTPS URL when provided.",
    )

    # ==========================================================================
    # Validators
    # ==========================================================================

    @field_validator("WEBHOOK_URL")
    @classmethod
    def validate_webhook_url(cls, v: str) -> str:
        """Validate webhook URL format if provided."""
        if v and not v.startswith(("http://", "https://")):
            raise ValueError(
                "WEBHOOK_URL must start with 'http://' or 'https://' when provided"
            )
        return v

    @field_validator("VAPI_PHONE_NUMBER")
    @classmethod
    def validate_phone_number(cls, v: str) -> str:
        """Validate phone number format if provided."""
        if v and not v.startswith("+"):
            raise ValueError(
                "VAPI_PHONE_NUMBER should include country code (e.g., +1234567890)"
            )
        return v

    @model_validator(mode="after")
    def validate_feature_dependencies(self) -> "Settings":
        """Validate that feature flags have required dependencies."""
        if self.ENABLE_GROQ_ERROR_ANALYSIS and not self.GROQ_API_KEY:
            raise ValueError(
                "GROQ_API_KEY is required when ENABLE_GROQ_ERROR_ANALYSIS is True"
            )

        if self.ENABLE_FETCHAI_MONITORING and not self.FETCHAI_AGENT_ADDRESS:
            raise ValueError(
                "FETCHAI_AGENT_ADDRESS is required when ENABLE_FETCHAI_MONITORING is True"
            )

        return self

    @model_validator(mode="after")
    def validate_timeout_relationship(self) -> "Settings":
        """Warn if MCP timeout is shorter than Agent-S timeout."""
        if self.MCP_TIMEOUT_SEC < self.AGENT_S_TIMEOUT_SEC:
            import logging
            logging.warning(
                f"MCP_TIMEOUT_SEC ({self.MCP_TIMEOUT_SEC}s) is less than "
                f"AGENT_S_TIMEOUT_SEC ({self.AGENT_S_TIMEOUT_SEC}s). "
                "Consider increasing MCP_TIMEOUT_SEC to avoid premature timeouts."
            )
        return self

    # ==========================================================================
    # Helper Properties
    # ==========================================================================

    @property
    def is_production(self) -> bool:
        """Check if running in production environment."""
        return self.ENV == "production"

    @property
    def is_development(self) -> bool:
        """Check if running in development environment."""
        return self.ENV == "development"

    @property
    def has_vapi_configured(self) -> bool:
        """Check if VAPI is fully configured."""
        return bool(self.VAPI_API_KEY and self.VAPI_ASSISTANT_ID)

    @property
    def has_groq_configured(self) -> bool:
        """Check if Groq is configured."""
        return bool(self.GROQ_API_KEY)

    @property
    def has_fetchai_configured(self) -> bool:
        """Check if Fetch.ai is configured."""
        return bool(self.FETCHAI_AGENT_ADDRESS)

    @property
    def server_url(self) -> str:
        """Get the full server URL."""
        return f"http://{self.SERVER_HOST}:{self.SERVER_PORT}"


@lru_cache
def get_settings() -> Settings:
    """
    Get cached application settings.

    Uses lru_cache to ensure settings are only loaded once and reused
    across the application. This is the recommended way to access settings
    in FastAPI dependency injection.

    Returns:
        Settings: Validated application settings instance.

    Raises:
        ValidationError: If required settings are missing or invalid.

    Example:
        # In FastAPI route
        from gateway.config import get_settings

        @app.get("/status")
        def status(settings: Settings = Depends(get_settings)):
            return {"env": settings.ENV}
    """
    return Settings()


# Convenience instance for direct imports
# Use get_settings() for dependency injection in FastAPI
settings = get_settings()


__all__ = ["Settings", "get_settings", "settings"]
