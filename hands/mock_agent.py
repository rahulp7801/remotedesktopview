"""
Mock Agent for Phase 2 Testing

This is a temporary mock implementation of Agent-S for testing the MCP infrastructure.
Will be replaced with real gui-agents integration once we determine the correct API.
"""

from typing import List, Optional
from loguru import logger


class MockAgentResult:
    """Mock result object from Agent execution."""

    def __init__(self, success: bool, error_message: Optional[str] = None):
        self.success = success
        self.error_message = error_message
        self.steps_executed = []
        self.screenshots = []

        if success:
            self.steps_executed = [
                "Analyzed UI elements",
                "Located target element",
                "Executed action",
                "Verified completion"
            ]


class MockAgent:
    """
    Mock Agent for testing MCP integration.

    Simulates Agent-S behavior without requiring the actual gui-agents package.
    """

    def __init__(self, platform: str = "macos", **kwargs):
        """Initialize mock agent."""
        self.platform = platform
        self.config = kwargs
        logger.info(f"MockAgent initialized for platform: {platform}")

    def run(self, prompt: str) -> MockAgentResult:
        """
        Mock execution of a GUI command.

        For Phase 2 testing, this returns success for most commands
        to validate the MCP pipeline works end-to-end.
        """
        logger.info(f"MockAgent.run: {prompt}")

        # Simulate some commands failing for testing
        if "fail" in prompt.lower() or "error" in prompt.lower():
            logger.warning("MockAgent: Simulating failure")
            return MockAgentResult(
                success=False,
                error_message="Mock failure: Could not find UI element"
            )

        # Most commands succeed
        logger.info("MockAgent: Simulating success")
        return MockAgentResult(success=True)

    def __repr__(self):
        return f"<MockAgent platform={self.platform}>"
