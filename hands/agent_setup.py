"""
Agent S3 Platform Abstraction

Uses the latest Agent S3 (gui-agents 0.3.1) for GUI automation.
Agent S3 uses screenshot-based visual grounding instead of accessibility tree,
resulting in much better accuracy and more reliable multi-step execution.
"""

import sys
import os
import io
import time
from typing import Optional, Any

from loguru import logger
import pyautogui

# Try to import Agent S3 (newer, better)
try:
    from gui_agents.s3.agents.agent_s import AgentS3
    from gui_agents.s3.agents.grounding import OSWorldACI
    logger.info("Using Agent S3 (gui-agents 0.3.1)")
    USE_AGENT_S3 = True
except ImportError as e:
    logger.warning(f"Agent S3 not available: {e}, falling back to old GraphSearchAgent")
    USE_AGENT_S3 = False
    try:
        from gui_agents.core.AgentS import GraphSearchAgent
    except ImportError:
        GraphSearchAgent = None


class Agent:
    """
    Wrapper for Agent S3 with platform abstraction.
    Uses screenshot-based visual grounding for better accuracy.
    """

    def __init__(self, platform: str, config: dict):
        self.platform = platform
        self.config = config
        self._agent = None
        self._grounding_agent = None

    def initialize(self):
        """Initialize Agent S3."""
        logger.info(f"Initializing Agent S3 for {self.platform}")

        anthropic_key = os.getenv("ANTHROPIC_API_KEY")
        
        if not anthropic_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")

        # Engine params for main generation (Claude - best reasoning)
        engine_params = {
            "engine_type": "anthropic",
            "model": "claude-sonnet-4-5-20250929",
            "api_key": anthropic_key,
        }

        # Grounding model - Claude Sonnet 4.5 (same as reasoning model)
        logger.info("Using Claude Sonnet 4.5 for grounding")
        engine_params_for_grounding = {
            "engine_type": "anthropic",
            "model": "claude-sonnet-4-5-20250929",
            "api_key": anthropic_key,
            "grounding_width": 1920,
            "grounding_height": 1080,
        }

        try:
            # Create grounding agent
            self._grounding_agent = OSWorldACI(
                env=None,  # No local code execution
                platform=self.platform,
                engine_params_for_generation=engine_params,
                engine_params_for_grounding=engine_params_for_grounding,
                width=1920,
                height=1080,
            )
            logger.info("OSWorldACI grounding agent created")

            # Create Agent S3
            self._agent = AgentS3(
                engine_params,
                self._grounding_agent,
                platform=self.platform,
                max_trajectory_length=self.config.get("max_trajectory_length", 5),
                enable_reflection=self.config.get("enable_reflection", False),
            )
            logger.info("Agent S3 initialized successfully")

        except Exception as e:
            logger.exception(f"Failed to initialize Agent S3: {e}")
            raise RuntimeError(f"Agent S3 initialization failed: {e}") from e

    def _focus_target_app(self, prompt: str) -> Optional[str]:
        """
        Extract target app from prompt and bring it to focus.
        """
        import subprocess
        import re

        prompt_lower = prompt.lower()

        app_patterns = {
            r'\bsafari\b': 'Safari',
            r'\bchrome\b': 'Google Chrome',
            r'\bfinder\b': 'Finder',
            r'\bmail\b': 'Mail',
            r'\bmessages\b': 'Messages',
            r'\bnotes\b': 'Notes',
            r'\bcalendar\b': 'Calendar',
            r'\bterminal\b': 'Terminal',
            r'\bvscode\b': 'Visual Studio Code',
            r'\bcursor\b': 'Cursor',
            r'\bslack\b': 'Slack',
            r'\bdiscord\b': 'Discord',
            r'\bspotify\b': 'Spotify',
        }

        target_app = None
        for pattern, app_name in app_patterns.items():
            if re.search(pattern, prompt_lower):
                target_app = app_name
                break

        if target_app:
            try:
                applescript = f'tell application "{target_app}" to activate'
                result = subprocess.run(
                    ["osascript", "-e", applescript],
                    capture_output=True,
                    timeout=3
                )
                if result.returncode == 0:
                    logger.info(f"Focused target app: {target_app}")
                    time.sleep(0.5)
                    return target_app
            except Exception as e:
                logger.warning(f"Error focusing app {target_app}: {e}")

        return None

    def _capture_screenshot(self) -> bytes:
        """Capture screenshot for Agent S3."""
        screenshot = pyautogui.screenshot()
        buffered = io.BytesIO()
        screenshot.save(buffered, format="PNG")
        return buffered.getvalue()

    def run(self, prompt: str, max_steps: int = 5) -> Any:
        """
        Execute a natural language GUI command using Agent S3.

        Args:
            prompt: Natural language description of the action
            max_steps: Maximum number of actions to execute

        Returns:
            Result object with success, error_message, steps_executed
        """
        if self._agent is None:
            self.initialize()
        
        # Reset agent state from previous runs
        self._agent.reset()
        logger.info(f"Agent S3 executing: {prompt}")
        all_steps_executed = []

        try:
            # Focus target app first
            focused_app = self._focus_target_app(prompt)
            if focused_app:
                logger.info(f"Pre-focused app: {focused_app}")

            # Multi-step execution loop
            for step_num in range(max_steps):
                logger.info(f"=== Step {step_num + 1}/{max_steps} ===")

                # Capture fresh screenshot
                screenshot_bytes = self._capture_screenshot()
                obs = {"screenshot": screenshot_bytes}

                # Get prediction from Agent S3
                start_time = time.time()
                try:
                    info, action = self._agent.predict(
                        instruction=prompt,
                        observation=obs
                    )
                    elapsed = time.time() - start_time
                    logger.info(f"Prediction took {elapsed:.2f}s")
                except Exception as e:
                    logger.error(f"Agent S3 prediction error: {e}")
                    break

                if action and len(action) > 0:
                    action_code = action[0]
                    logger.info(f"Action: {action_code[:100]}...")

                    # Check for DONE/FAIL (can be "DONE", "done()", etc.)
                    action_lower = str(action_code).lower().strip()
                    if action_lower in ("done", "done()", "\"done\"", "'done'") or "done()" in action_lower:
                        logger.info("Agent S3 completed with DONE")
                        class Result:
                            pass
                        result = Result()
                        result.success = True
                        result.error_message = None
                        result.steps_executed = all_steps_executed
                        result.screenshots = []
                        return result

                    if action_lower in ("fail", "fail()", "\"fail\"", "'fail'") or "fail()" in action_lower:
                        logger.info("Agent S3 reported FAIL")
                        class Result:
                            pass
                        result = Result()
                        result.success = False
                        result.error_message = "Agent reported failure"
                        result.steps_executed = all_steps_executed
                        result.screenshots = []
                        return result

                    # Execute the action
                    try:
                        exec(action_code)
                        logger.info(f"Action executed successfully")
                        all_steps_executed.append(action_code)
                        time.sleep(0.5)  # Brief pause after action
                    except Exception as exec_error:
                        logger.error(f"Failed to execute: {exec_error}")
                        class Result:
                            pass
                        result = Result()
                        result.success = False
                        result.error_message = f"Execution failed: {exec_error}"
                        result.steps_executed = all_steps_executed
                        result.screenshots = []
                        return result
                else:
                    logger.warning("No action returned")
                    break

            # Loop completed
            logger.info(f"Agent S3 completed with {len(all_steps_executed)} steps")
            class Result:
                pass
            result = Result()
            result.success = len(all_steps_executed) > 0
            result.error_message = None if all_steps_executed else "No actions executed"
            result.steps_executed = all_steps_executed
            result.screenshots = []
            return result

        except Exception as e:
            logger.error(f"Agent S3 error: {e}")
            import traceback
            traceback.print_exc()
            class Result:
                pass
            result = Result()
            result.success = False
            result.error_message = str(e)
            result.steps_executed = all_steps_executed
            result.screenshots = []
            return result


def create_agent(platform: Optional[str] = None) -> Agent:
    """
    Create an Agent S3 instance for the current or specified platform.
    """
    if platform is None:
        if sys.platform == "darwin":
            platform = "darwin"
        elif sys.platform == "win32":
            platform = "windows"
        else:
            platform = "linux"

    logger.info(f"Creating Agent S3 for platform: {platform}")

    config = {
        "max_trajectory_length": 5,
        "enable_reflection": False,  # Disabled for speed
    }

    agent = Agent(platform=platform, config=config)
    agent.initialize()
    return agent


def validate_platform_requirements(platform: Optional[str] = None) -> dict[str, bool]:
    """
    Validate that all platform requirements are met.
    """
    if platform is None:
        platform = "darwin" if sys.platform == "darwin" else "windows"

    checks = {
        "gui_agents_installed": USE_AGENT_S3,
        "api_key_available": bool(os.getenv("ANTHROPIC_API_KEY")),
        "platform_supported": platform in ["darwin", "windows", "linux"],
    }

    return checks
