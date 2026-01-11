"""
Agent-S Lifecycle Manager

Manages the Agent-S instance lifecycle with:
- Singleton pattern for reuse across tool calls
- Platform detection (macOS/Windows)
- Pre-warming on startup
- Graceful error handling
- Resource cleanup

Agent-S is expensive to initialize (~500ms), so we reuse a single instance.
"""

import asyncio
import sys
from typing import Optional
from datetime import datetime

from loguru import logger

# Will import Agent-S setup from hands module
from hands.agent_setup import create_agent, Agent


class AgentExecutionError(Exception):
    """Raised when Agent-S fails to execute a command."""
    pass


class AgentManager:
    """
    Singleton manager for Agent-S instance.

    Handles initialization, reuse, and lifecycle management.
    """

    _instance: Optional["AgentManager"] = None
    _agent: Optional[Agent] = None
    _initialized: bool = False
    _platform: Optional[str] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @property
    def platform(self) -> str:
        """Get current platform (macOS/Windows)."""
        if self._platform is None:
            if sys.platform == "darwin":
                self._platform = "macos"
            elif sys.platform == "win32":
                self._platform = "windows"
            else:
                raise RuntimeError(f"Unsupported platform: {sys.platform}")
        return self._platform

    async def initialize(self) -> None:
        """
        Initialize Agent-S instance.

        This is called once on server startup to pre-warm Agent-S.
        Saves ~500ms on first tool call.
        """
        if self._initialized:
            logger.debug("Agent-S already initialized")
            return

        logger.info(f"Initializing Agent-S for platform: {self.platform}")
        start_time = datetime.now()

        try:
            # Create Agent-S instance with platform-specific driver
            self._agent = await asyncio.to_thread(
                create_agent,
                platform=self.platform
            )

            init_time = (datetime.now() - start_time).total_seconds()
            logger.info(f"Agent-S initialized successfully in {init_time:.2f}s")

            self._initialized = True

        except Exception as e:
            logger.exception("Failed to initialize Agent-S")
            raise RuntimeError(f"Agent-S initialization failed: {e}") from e

    async def get_agent(self) -> Agent:
        """
        Get the Agent-S instance, initializing if needed.

        Returns:
            Agent-S instance ready for use

        Raises:
            RuntimeError: If initialization fails
        """
        if not self._initialized:
            await self.initialize()

        if self._agent is None:
            raise RuntimeError("Agent-S instance is None after initialization")

        return self._agent

    async def is_ready(self) -> bool:
        """Check if Agent-S is initialized and ready."""
        return self._initialized and self._agent is not None

    async def restart(self) -> None:
        """
        Restart Agent-S instance.

        Useful if Agent-S gets into a bad state.
        """
        logger.warning("Restarting Agent-S instance")

        # Clean up old instance
        self._agent = None
        self._initialized = False

        # Reinitialize
        await self.initialize()

    async def shutdown(self) -> None:
        """Clean up Agent-S resources on server shutdown."""
        logger.info("Shutting down Agent-S")

        if self._agent is not None:
            # Agent-S cleanup if needed
            self._agent = None

        self._initialized = False


# Global singleton instance
_manager = AgentManager()


async def get_agent_instance() -> Agent:
    """
    Get the global Agent-S instance.

    This is the main entry point for tool implementations.

    Returns:
        Agent-S instance ready for use
    """
    return await _manager.get_agent()


async def initialize_agent() -> None:
    """Initialize Agent-S on server startup."""
    await _manager.initialize()


async def shutdown_agent() -> None:
    """Clean up Agent-S on server shutdown."""
    await _manager.shutdown()


async def is_agent_ready() -> bool:
    """Check if Agent-S is ready for use."""
    return await _manager.is_ready()


async def restart_agent() -> None:
    """Restart Agent-S instance (for error recovery)."""
    await _manager.restart()
