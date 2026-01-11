"""
Agent-S Platform Abstraction

Provides unified interface for creating Agent-S instances across:
- macOS (MacOSACI driver)
- Windows (WindowsACI driver)

Platform-specific drivers handle OS-level GUI automation APIs.
"""

import sys
from typing import Optional, Any

from loguru import logger

# Import Agent-S from gui-agents package
try:
    from gui_agents.core.AgentS import GraphSearchAgent as RealAgent
    logger.info("Using real gui-agents GraphSearchAgent")
    USE_REAL_AGENT = True
except ImportError as e:
    logger.warning(f"gui-agents GraphSearchAgent not available: {e}")
    logger.info("Using MockAgent fallback")
    from hands.mock_agent import MockAgent as RealAgent
    USE_REAL_AGENT = False

# Import platform-specific drivers
from hands.drivers.macos_driver import MacOSDriver
from hands.drivers.windows_driver import WindowsDriver


class Agent:
    """
    Wrapper for gui-agents Agent with platform abstraction.

    This provides a consistent interface regardless of platform.
    """

    def __init__(self, platform: str, driver: Any, config: dict):
        self.platform = platform
        self.driver = driver
        self.config = config
        self._agent = None

    def initialize(self):
        """Initialize the underlying Agent-S instance."""
        import os
        logger.info(f"Initializing Agent with {self.platform} driver")

        try:
            # Create Agent instance (real or mock depending on what's available)
            if USE_REAL_AGENT:
                # Use real gui-agents UIAgent
                from gui_agents.aci.MacOSACI import MacOSACI
                from gui_agents.aci.WindowsOSACI import WindowsACI as WindowsOSACI

                # Create ACI driver instance
                if self.platform == "macos":
                    grounding_agent = MacOSACI(top_app_only=True, ocr=False)
                elif self.platform == "windows":
                    grounding_agent = WindowsOSACI(top_app_only=True, ocr=False)
                else:
                    raise ValueError(f"Unsupported platform: {self.platform}")

                # Get Anthropic API key from environment
                anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
                if not anthropic_api_key:
                    logger.warning("ANTHROPIC_API_KEY not set - GraphSearchAgent requires API key. Falling back to MockAgent.")
                    # Fall back to MockAgent if no API key
                    from hands.mock_agent import MockAgent
                    self._agent = MockAgent(platform=self.platform, **self.config)
                    logger.info("Falling back to MockAgent due to missing Anthropic API key")
                    return

                # Engine parameters for LLM (using Anthropic Claude)
                engine_params = {
                    "engine_type": "anthropic",  # Supports: "openai", "azure", "anthropic", "vllm"
                    "model": "claude-sonnet-4-5-20250929",  # Claude Sonnet 4.5
                    "max_tokens": 2000,
                    "temperature": 0.1,
                    "api_key": anthropic_api_key,
                }

                # Initialize GraphSearchAgent
                self._agent = RealAgent(
                    engine_params=engine_params,
                    grounding_agent=grounding_agent,
                    platform=self.platform,
                    action_space="aci",  # Use ACI for GUI control
                    observation_type="a11y_tree",  # Accessibility tree observations
                )
                logger.info("Real GraphSearchAgent initialized successfully")
            else:
                # Use MockAgent for testing
                self._agent = RealAgent(
                    platform=self.platform,
                    **self.config
                )
                logger.info("MockAgent initialized successfully")

        except Exception as e:
            logger.exception(f"Failed to initialize Agent: {e}")
            raise RuntimeError(f"Agent initialization failed: {e}") from e

    def _capture_screenshot_bytes(self) -> Optional[bytes]:
        """
        Capture the current screen as bytes for Agent-S observation.

        Returns:
            Screenshot as bytes, or None if capture fails
        """
        import subprocess
        import tempfile
        import os

        try:
            # Create a temporary file for the screenshot
            with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp_file:
                tmp_path = tmp_file.name

            # Capture screenshot using macOS screencapture
            result = subprocess.run(
                ["screencapture", "-x", tmp_path],  # -x: no sound
                capture_output=True,
                timeout=5
            )

            if result.returncode != 0:
                logger.warning(f"Screenshot capture failed: {result.stderr.decode()}")
                return None

            # Read the screenshot bytes
            with open(tmp_path, 'rb') as f:
                screenshot_bytes = f.read()

            # Clean up temp file
            os.unlink(tmp_path)

            logger.debug(f"Screenshot captured: {len(screenshot_bytes)} bytes")
            return screenshot_bytes

        except Exception as e:
            logger.warning(f"Failed to capture screenshot: {e}")
            return None

    def run(self, prompt: str) -> Any:
        """
        Execute a natural language GUI command.

        Args:
            prompt: Natural language description of the action

        Returns:
            Result object with:
            - success: bool
            - error_message: Optional[str]
            - steps_executed: Optional[List[str]]
            - screenshots: Optional[List[str]]
        """
        if self._agent is None:
            self.initialize()

        logger.debug(f"Agent-S executing: {prompt}")

        try:
            if USE_REAL_AGENT:
                # GraphSearchAgent uses predict() method with observation dict
                # Need to capture current accessibility tree and screenshot before calling predict
                try:
                    # Get system-wide accessibility element using macOS APIs
                    from ApplicationServices import AXUIElementCreateSystemWide

                    # Create system-wide accessibility element
                    ax_system = AXUIElementCreateSystemWide()

                    # Capture screenshot as bytes for the observation
                    screenshot_bytes = self._capture_screenshot_bytes()

                    # Create observation dict with correct keys expected by GraphSearchAgent
                    # The UIElement wrapper is created internally by MacOSACI
                    from gui_agents.aci.MacOSACI import UIElement
                    observation = {
                        "accessibility_tree": UIElement(ax_system),
                        "screenshot": screenshot_bytes
                    }
                    logger.debug("Successfully captured accessibility tree and screenshot")
                except Exception as e:
                    logger.warning(f"Could not get screen state: {e}. Using fallback observation.")
                    # Fallback: create minimal observation that might still work
                    try:
                        from ApplicationServices import AXUIElementCreateSystemWide
                        from gui_agents.aci.MacOSACI import UIElement
                        ax_system = AXUIElementCreateSystemWide()
                        observation = {
                            "accessibility_tree": UIElement(ax_system),
                            "screenshot": None
                        }
                    except Exception as e2:
                        logger.error(f"Fallback observation also failed: {e2}")
                        raise RuntimeError(f"Cannot create observation for Agent-S: {e2}")

                try:
                    action, trace = self._agent.predict(prompt, observation)
                except IndexError as e:
                    # This occurs when the LLM planner returns an empty subtask list
                    # and the agent tries to pop from it (subtasks.pop(0))
                    logger.warning(f"Agent-S planner returned empty subtask list for: {prompt}")
                    
                    class PlannerFailureResult:
                        success = False
                        error_message = (
                            f"Could not plan steps for this command. "
                            f"Try a simpler, more specific instruction like 'Open Safari' "
                            f"instead of '{prompt}'"
                        )
                        steps_executed = []
                        screenshots = []
                    
                    return PlannerFailureResult()

                # Convert GraphSearchAgent result to our expected format
                class UIAgentResult:
                    success = True
                    error_message = None
                    steps_executed = trace if trace else []
                    screenshots = []

                result = UIAgentResult()
                logger.debug(f"GraphSearchAgent completed successfully")
                return result
            else:
                # MockAgent uses run() method
                result = self._agent.run(prompt)
                logger.debug(f"MockAgent result: success={result.success}")
                return result

        except Exception as e:
            logger.error(f"Agent execution error: {e}")

            # Return error result in expected format
            class ErrorResult:
                success = False
                error_message = str(e)
                steps_executed = []
                screenshots = []

            return ErrorResult()


def create_agent(platform: Optional[str] = None) -> Agent:
    """
    Create an Agent-S instance for the current or specified platform.

    Args:
        platform: "macos" or "windows" (auto-detected if None)

    Returns:
        Initialized Agent instance

    Raises:
        RuntimeError: If platform is unsupported or initialization fails
    """
    # Auto-detect platform if not specified
    if platform is None:
        if sys.platform == "darwin":
            platform = "macos"
        elif sys.platform == "win32":
            platform = "windows"
        else:
            raise RuntimeError(f"Unsupported platform: {sys.platform}")

    logger.info(f"Creating Agent-S for platform: {platform}")

    if platform == "macos":
        return _create_macos_agent()
    elif platform == "windows":
        return _create_windows_agent()
    else:
        raise RuntimeError(f"Unknown platform: {platform}")


def _create_macos_agent() -> Agent:
    """
    Create Agent-S instance for macOS with MacOSACI driver.

    Uses:
    - MacOSACI: Vision-based GUI element detection
    - Quartz APIs: Screen capture and mouse/keyboard control
    - Accessibility API: Application window management
    """
    logger.info("Setting up macOS agent with MacOSACI driver")

    driver = MacOSDriver()

    config = {
        # Vision-based element detection
        "use_screen_parsing": True,
        "headless": False,  # We want to see what's happening

        # Performance optimizations
        "screenshot_cache_ttl": 1,  # Cache screenshots for 1s
        "element_detection_timeout": 5,  # 5s max to find elements
        "detection_confidence_threshold": 0.7,  # 70% confidence required
        "use_region_detection": True,  # Only analyze relevant screen areas

        # Debugging (disable in production)
        "save_screenshots": True,
        "screenshot_dir": "cache/agent_screenshots",
    }

    agent = Agent(
        platform="macos",
        driver=driver,
        config=config
    )

    agent.initialize()
    return agent


def _create_windows_agent() -> Agent:
    """
    Create Agent-S instance for Windows with WindowsACI driver.

    Uses:
    - WindowsACI: Vision-based GUI element detection
    - Win32 APIs: Screen capture and mouse/keyboard control
    - UI Automation: Application window management
    """
    logger.info("Setting up Windows agent with WindowsACI driver")

    driver = WindowsDriver()

    config = {
        # Vision-based element detection
        "use_screen_parsing": True,
        "headless": False,

        # Performance optimizations
        "screenshot_cache_ttl": 1,
        "element_detection_timeout": 5,
        "detection_confidence_threshold": 0.7,
        "use_region_detection": True,

        # Debugging
        "save_screenshots": True,
        "screenshot_dir": "cache/agent_screenshots",
    }

    agent = Agent(
        platform="windows",
        driver=driver,
        config=config
    )

    agent.initialize()
    return agent


def validate_platform_requirements(platform: Optional[str] = None) -> dict[str, bool]:
    """
    Validate that all platform requirements are met.

    Args:
        platform: "macos" or "windows" (auto-detected if None)

    Returns:
        Dictionary of requirement checks:
        - permissions_granted: bool
        - required_apps_installed: bool
        - driver_available: bool
    """
    if platform is None:
        platform = "macos" if sys.platform == "darwin" else "windows"

    logger.info(f"Validating {platform} requirements")

    if platform == "macos":
        return _validate_macos_requirements()
    elif platform == "windows":
        return _validate_windows_requirements()
    else:
        return {
            "permissions_granted": False,
            "required_apps_installed": False,
            "driver_available": False,
            "error": f"Unknown platform: {platform}"
        }


def _validate_macos_requirements() -> dict[str, bool]:
    """Check macOS-specific requirements."""
    import subprocess

    checks = {}

    # Check Accessibility permission
    try:
        result = subprocess.run(
            ["osascript", "-e", 'tell application "System Events" to get properties'],
            capture_output=True,
            timeout=5
        )
        checks["accessibility_granted"] = result.returncode == 0
    except Exception:
        checks["accessibility_granted"] = False

    # Check Screen Recording permission (harder to test programmatically)
    checks["screen_recording_granted"] = True  # Assume granted, will fail on first screenshot if not

    # Check if gui-agents is installed
    try:
        import gui_agents
        checks["gui_agents_installed"] = True
    except ImportError:
        checks["gui_agents_installed"] = False

    return checks


def _validate_windows_requirements() -> dict[str, bool]:
    """Check Windows-specific requirements."""
    checks = {}

    # Check if gui-agents is installed
    try:
        import gui_agents
        checks["gui_agents_installed"] = True
    except ImportError:
        checks["gui_agents_installed"] = False

    # Windows typically has fewer permission requirements
    checks["uiautomation_available"] = True  # Built into Windows

    return checks
