"""
FastAPI Application Entry Point for Voice-Controlled Remote Desktop Agent.

This module provides the main FastAPI application with:
- VAPI webhook router integration for handling voice assistant events
- Health check endpoints for monitoring and load balancers
- CORS middleware configured for development and production
- Request logging middleware with timing information
- Structured logging via loguru with proper formatting
- Lifecycle event handlers for startup and shutdown

Usage:
    # Run in development mode with auto-reload
    uvicorn gateway.main:app --reload

    # Run directly
    python -m gateway.main

    # Or programmatically
    from gateway.main import app
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

Environment Variables:
    See gateway/config.py for all configuration options.
    Key variables:
    - ENV: "development" or "production" (default: development)
    - SERVER_HOST: Server bind address (default: 0.0.0.0)
    - SERVER_PORT: Server port (default: 8000)
"""

import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from gateway.config import Settings, get_settings, settings
from gateway.mcp_client_simple import initialize_mcp_client, shutdown_mcp_client
from gateway.models import HealthCheckResponse
from gateway.vapi_webhook_handler import router as vapi_router


# =============================================================================
# Application Constants
# =============================================================================

APP_TITLE = "Voice-Controlled Remote Desktop Agent"
APP_DESCRIPTION = """
A voice-controlled agent that enables remote desktop control through natural
speech commands. Built for the UCSB Hackathon "Make Your App Talk Back" challenge.

## Features

- **Voice Commands**: Control your Mac desktop through natural speech
- **VAPI Integration**: Real-time voice assistant via webhook handling
- **GUI Automation**: Agent-S powered desktop automation
- **Screenshot Verification**: Visual confirmation of completed actions

## Endpoints

- `/health` - Application health check
- `/vapi/webhook` - VAPI webhook handler for voice events
- `/vapi/health` - VAPI handler specific health check

## Authentication

VAPI webhooks are validated using the configured API key. See CLAUDE.md for setup.
"""
APP_VERSION = "1.0.0"

# Track application startup time for uptime calculation
_startup_time: float | None = None


# =============================================================================
# Logging Configuration
# =============================================================================


def configure_logging(settings: Settings) -> None:
    """
    Configure loguru logging with environment-appropriate settings.

    In development:
        - DEBUG level logging
        - Colorful console output
        - Detailed format with file/function info

    In production:
        - INFO level logging
        - Structured JSON-like format
        - Optimized for log aggregation

    Args:
        settings: Application settings containing ENV configuration.
    """
    # Remove default loguru handler
    logger.remove()

    if settings.is_development:
        # Development: colorful, detailed output
        log_format = (
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        )
        log_level = "DEBUG"
    else:
        # Production: structured, concise output
        log_format = (
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | "
            "{level: <8} | "
            "{name}:{function}:{line} | "
            "{message}"
        )
        log_level = "INFO"

    # Add console handler with environment-specific settings
    logger.add(
        sys.stderr,
        format=log_format,
        level=log_level,
        colorize=settings.is_development,
        backtrace=settings.is_development,
        diagnose=settings.is_development,
    )

    # In production, optionally add file logging
    if settings.is_production:
        logger.add(
            "logs/app.log",
            format=log_format,
            level="INFO",
            rotation="10 MB",
            retention="7 days",
            compression="gz",
            backtrace=False,
            diagnose=False,
        )

    logger.info(
        f"Logging configured | level={log_level} | env={settings.ENV}"
    )


# =============================================================================
# Lifecycle Event Handlers
# =============================================================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan context manager for startup and shutdown events.

    Startup:
        - Configure logging
        - Log application startup with configuration
        - Initialize any async resources

    Shutdown:
        - Log graceful shutdown
        - Cleanup any resources

    Args:
        app: The FastAPI application instance.
    """
    global _startup_time

    # Startup
    configure_logging(settings)
    _startup_time = time.time()

    logger.info("=" * 60)
    logger.info(f"{APP_TITLE} starting up")
    logger.info("=" * 60)
    logger.info(f"Version: {APP_VERSION}")
    logger.info(f"Environment: {settings.ENV}")
    logger.info(f"Server: {settings.SERVER_HOST}:{settings.SERVER_PORT}")
    logger.info(f"VAPI configured: {settings.has_vapi_configured}")
    logger.info(f"Groq configured: {settings.has_groq_configured}")
    logger.info(f"Fetch.ai configured: {settings.has_fetchai_configured}")
    logger.info(f"Screenshot verification: {settings.ENABLE_SCREENSHOT_VERIFICATION}")
    logger.info("=" * 60)

    # Initialize MCP client for Agent-S communication
    try:
        logger.info("Initializing MCP client connection to Agent-S server...")
        await initialize_mcp_client()
        logger.info("MCP client initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize MCP client: {e}")
        logger.warning("Server will start but tool calls may fail")

    logger.info("=" * 60)
    logger.info(f"{APP_TITLE} ready to accept requests")
    logger.info("=" * 60)

    yield

    # Shutdown
    logger.info("=" * 60)
    logger.info(f"{APP_TITLE} shutting down")
    logger.info("=" * 60)

    # Cleanup MCP client
    try:
        logger.info("Shutting down MCP client...")
        await shutdown_mcp_client()
        logger.info("MCP client shutdown complete")
    except Exception as e:
        logger.error(f"Error during MCP client shutdown: {e}")

    uptime = time.time() - _startup_time if _startup_time else 0
    logger.info(f"Uptime: {uptime:.2f} seconds")
    logger.info("=" * 60)


# =============================================================================
# FastAPI Application Factory
# =============================================================================


def create_app() -> FastAPI:
    """
    Create and configure the FastAPI application instance.

    Returns:
        FastAPI: Configured application with all middleware and routers.
    """
    app = FastAPI(
        title=APP_TITLE,
        description=APP_DESCRIPTION,
        version=APP_VERSION,
        lifespan=lifespan,
        docs_url="/docs" if settings.is_development else None,
        redoc_url="/redoc" if settings.is_development else None,
        openapi_url="/openapi.json" if settings.is_development else None,
    )

    # Configure CORS middleware
    configure_cors(app)

    # Add request logging middleware
    app.middleware("http")(request_logging_middleware)

    # Include VAPI webhook router
    app.include_router(vapi_router)

    # Register root health check endpoint
    app.add_api_route(
        "/health",
        health_check,
        methods=["GET"],
        summary="Application Health Check",
        description="Returns overall application health status for monitoring and load balancers.",
        response_model=dict,
        tags=["health"],
    )

    return app


def configure_cors(app: FastAPI) -> None:
    """
    Configure CORS middleware based on environment settings.

    Development:
        - Allow all origins for local testing
        - Allow all methods and headers

    Production:
        - Should be configured with specific allowed origins
        - More restrictive headers

    Args:
        app: FastAPI application instance.
    """
    if settings.is_development:
        # Development: permissive CORS for local testing
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["*"],
        )
        logger.debug("CORS configured for development (allow all origins)")
    else:
        # Production: restrict to known origins
        # TODO: Add ALLOWED_ORIGINS to settings for production
        allowed_origins = [
            "https://vapi.ai",
            "https://*.vapi.ai",
        ]
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        )
        logger.info(f"CORS configured for production | origins={allowed_origins}")


# =============================================================================
# Middleware
# =============================================================================


async def request_logging_middleware(
    request: Request,
    call_next: Callable,
) -> Response:
    """
    Middleware for logging all HTTP requests with timing information.

    Logs:
        - Request method and path
        - Response status code
        - Request duration in milliseconds
        - Client IP address (for debugging)

    Skips logging for:
        - Health check endpoints (to reduce noise)

    Args:
        request: The incoming FastAPI request.
        call_next: The next middleware/handler in the chain.

    Returns:
        Response from the downstream handler.
    """
    # Skip logging for health check endpoints to reduce noise
    if request.url.path in ("/health", "/vapi/health"):
        return await call_next(request)

    start_time = time.time()
    request_id = request.headers.get("X-Request-ID", "no-request-id")
    client_ip = request.client.host if request.client else "unknown"

    logger.info(
        f"Request started | method={request.method} | path={request.url.path} | "
        f"client_ip={client_ip} | request_id={request_id}"
    )

    try:
        response = await call_next(request)
        elapsed_ms = int((time.time() - start_time) * 1000)

        log_level = "info" if response.status_code < 400 else "warning"
        getattr(logger, log_level)(
            f"Request completed | method={request.method} | path={request.url.path} | "
            f"status={response.status_code} | elapsed_ms={elapsed_ms} | request_id={request_id}"
        )

        # Add timing header to response
        response.headers["X-Response-Time-Ms"] = str(elapsed_ms)

        return response

    except Exception as e:
        elapsed_ms = int((time.time() - start_time) * 1000)
        logger.exception(
            f"Request failed | method={request.method} | path={request.url.path} | "
            f"error={e} | elapsed_ms={elapsed_ms} | request_id={request_id}"
        )
        raise


# =============================================================================
# Health Check Endpoint
# =============================================================================


async def health_check() -> dict[str, Any]:
    """
    Root health check endpoint for application monitoring.

    Returns basic application status including:
        - Health status (healthy/degraded/unhealthy)
        - Current timestamp
        - Application version
        - Uptime in seconds
        - Environment information
        - Configuration status

    Used by:
        - Load balancers for routing decisions
        - Monitoring systems for alerting
        - Deployment systems for readiness checks

    Returns:
        Dict containing health status and application metadata.
    """
    global _startup_time

    uptime_seconds = time.time() - _startup_time if _startup_time else 0

    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "version": APP_VERSION,
        "uptime_seconds": round(uptime_seconds, 2),
        "environment": settings.ENV,
        "service": APP_TITLE,
        "configuration": {
            "vapi_configured": settings.has_vapi_configured,
            "groq_configured": settings.has_groq_configured,
            "fetchai_configured": settings.has_fetchai_configured,
            "screenshot_verification_enabled": settings.ENABLE_SCREENSHOT_VERIFICATION,
        },
    }


# =============================================================================
# Application Instance
# =============================================================================

# Create the FastAPI application instance
app = create_app()


# =============================================================================
# Main Entry Point
# =============================================================================


def main() -> None:
    """
    Main entry point for running the application directly.

    Configures uvicorn with settings from gateway/config.py:
        - Host and port from SERVER_HOST and SERVER_PORT
        - Reload enabled in development mode
        - Log level based on environment
        - Access logging enabled

    Usage:
        python -m gateway.main
    """
    import uvicorn

    # Determine log level based on environment
    log_level = "debug" if settings.is_development else "info"

    logger.info(
        f"Starting uvicorn server | host={settings.SERVER_HOST} | "
        f"port={settings.SERVER_PORT} | reload={settings.is_development}"
    )

    uvicorn.run(
        "gateway.main:app",
        host=settings.SERVER_HOST,
        port=settings.SERVER_PORT,
        reload=settings.is_development,
        log_level=log_level,
        access_log=True,
        # In production, you might want to configure workers
        # workers=4 if settings.is_production else 1,
    )


if __name__ == "__main__":
    main()


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    "app",
    "create_app",
    "main",
    "APP_TITLE",
    "APP_VERSION",
]
