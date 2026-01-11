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
        # Window bounds for constraining clicks to target app
        self._window_bounds = None  # (x, y, width, height) or None for full screen

    def initialize(self):
        """Initialize Agent S3."""
        logger.info(f"Initializing Agent S3 for {self.platform}")

        anthropic_key = os.getenv("ANTHROPIC_API_KEY")
        hf_ground_url = os.getenv("HF_GROUND_URL")  # Optional: UI-TARS endpoint
        hf_token = os.getenv("HF_TOKEN")
        
        if not anthropic_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")

        # Get actual screen resolution for accurate grounding
        screen_width, screen_height = pyautogui.size()
        logger.info(f"Detected screen resolution: {screen_width}x{screen_height}")

        # Engine params for main generation (Claude - best reasoning)
        engine_params = {
            "engine_type": "anthropic",
            "model": "claude-sonnet-4-5-20250929",
            "api_key": anthropic_key,
        }

        # Grounding model - UI-TARS if available, otherwise Claude
        if hf_ground_url and hf_token:
            # UI-TARS on HuggingFace TGI endpoint (faster for grounding)
            logger.info("Using UI-TARS for grounding (faster)")
            engine_params_for_grounding = {
                "engine_type": "huggingface",
                "base_url": hf_ground_url.rstrip("/") + "/v1",
                "api_key": hf_token,
                "grounding_width": screen_width,
                "grounding_height": screen_height,
            }
        else:
            # Claude Sonnet 4.5 (reliable fallback)
            logger.info("Using Claude Sonnet 4.5 for grounding")
            engine_params_for_grounding = {
                "engine_type": "anthropic",
                "model": "claude-sonnet-4-5-20250929",
                "api_key": anthropic_key,
                "grounding_width": screen_width,
                "grounding_height": screen_height,
            }

        try:
            # Create grounding agent with actual screen dimensions
            self._grounding_agent = OSWorldACI(
                env=None,  # No local code execution
                platform=self.platform,
                engine_params_for_generation=engine_params,
                engine_params_for_grounding=engine_params_for_grounding,
                width=screen_width,
                height=screen_height,
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

    def _focus_app_by_name(self, app_name: str) -> Optional[str]:
        """
        Focus an app by its explicit name (provided by the planner).
        This is the robust approach - no pattern matching, just focus the specified app.
        """
        import subprocess
        
        if not app_name:
            return None
            
        try:
            # Activate the app AND hide all other apps
            activate_script = f'''
            tell application "System Events"
                -- Hide all other apps
                set visible of every process whose visible is true and name is not "{app_name}" to false
            end tell
            tell application "{app_name}"
                activate
            end tell
            '''
            result = subprocess.run(
                ["osascript", "-e", activate_script],
                capture_output=True,
                timeout=5
            )
            if result.returncode == 0:
                logger.info(f"Focused app: {app_name} (hid other apps)")
                time.sleep(0.5)
                
                # Get window bounds
                bounds_script = f'''
                tell application "System Events"
                    tell process "{app_name}"
                        if exists window 1 then
                            set winPos to position of window 1
                            set winSize to size of window 1
                            return (item 1 of winPos as text) & "," & (item 2 of winPos as text) & "," & (item 1 of winSize as text) & "," & (item 2 of winSize as text)
                        end if
                    end tell
                end tell
                '''
                bounds_result = subprocess.run(
                    ["osascript", "-e", bounds_script],
                    capture_output=True,
                    timeout=3
                )
                if bounds_result.returncode == 0 and bounds_result.stdout:
                    bounds_str = bounds_result.stdout.decode().strip()
                    parts = bounds_str.split(",")
                    if len(parts) == 4:
                        x, y, w, h = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
                        self._window_bounds = (x, y, w, h)
                        logger.info(f"Window bounds: x={x}, y={y}, w={w}, h={h}")
                
                return app_name
        except Exception as e:
            logger.warning(f"Error focusing app {app_name}: {e}")
        
        return None

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
            r'\bdownloads?\b': 'Finder',  # Downloads folder = Finder
            r'\bdocuments?\b': 'Finder',  # Documents folder = Finder
            r'\bdesktop\b': 'Finder',     # Desktop folder = Finder
            r'\bfolder\b': 'Finder',      # Any folder = Finder
            r'\bfiles?\b': 'Finder',      # Any file = Finder
            r'\bsearch\s+for\b': 'Finder', # "Search for X" = Finder (file search)
            r'\bfind\b': 'Finder',        # "Find X" = Finder
            r'\bpng\b': 'Finder',         # File extensions
            r'\bjpg\b': 'Finder',
            r'\bpdf\b': 'Finder',
            r'\bvideo\b': 'Finder',
            r'\bmail\b': 'Mail',
            r'\bemail\b': 'Mail',
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
                # Activate the app AND hide all other apps
                activate_script = f'''
                tell application "System Events"
                    -- Hide all other apps
                    set visible of every process whose visible is true and name is not "{target_app}" to false
                end tell
                tell application "{target_app}"
                    activate
                end tell
                '''
                result = subprocess.run(
                    ["osascript", "-e", activate_script],
                    capture_output=True,
                    timeout=5
                )
                if result.returncode == 0:
                    logger.info(f"Focused target app: {target_app} (hid other apps)")
                    time.sleep(0.5)
                    
                    # Get window bounds to constrain clicks
                    bounds_script = f'''
                    tell application "System Events"
                        tell process "{target_app}"
                            if exists window 1 then
                                set winPos to position of window 1
                                set winSize to size of window 1
                                return (item 1 of winPos as text) & "," & (item 2 of winPos as text) & "," & (item 1 of winSize as text) & "," & (item 2 of winSize as text)
                            end if
                        end tell
                    end tell
                    '''
                    bounds_result = subprocess.run(
                        ["osascript", "-e", bounds_script],
                        capture_output=True,
                        timeout=3
                    )
                    logger.debug(f"Bounds script result: rc={bounds_result.returncode}, stdout={bounds_result.stdout}, stderr={bounds_result.stderr}")
                    if bounds_result.returncode == 0 and bounds_result.stdout:
                        bounds_str = bounds_result.stdout.decode().strip()
                        parts = bounds_str.split(",")
                        if len(parts) == 4:
                            x, y, w, h = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
                            self._window_bounds = (x, y, w, h)
                            logger.info(f"Window bounds: x={x}, y={y}, w={w}, h={h}")
                        else:
                            logger.warning(f"Could not parse window bounds: {bounds_str}")
                    else:
                        logger.warning(f"Window bounds query failed or returned empty")
                    
                    return target_app
            except Exception as e:
                logger.warning(f"Error focusing app {target_app}: {e}")

        return None

    def _capture_screenshot(self) -> bytes:
        """
        Capture screenshot for Agent S3, cropped to target window and resized.
        
        On Retina displays, screenshots are captured at 2x resolution but
        pyautogui.click() uses logical coordinates. We resize to match.
        If window bounds are set, we crop to only show the target window.
        """
        from PIL import Image
        
        screenshot = pyautogui.screenshot()
        
        # Get logical screen size (what pyautogui.click uses)
        logical_width, logical_height = pyautogui.size()
        
        logger.debug(f"Screenshot captured: {screenshot.width}x{screenshot.height}, logical screen: {logical_width}x{logical_height}")
        
        # Calculate Retina scale factor
        scale_x = screenshot.width / logical_width
        scale_y = screenshot.height / logical_height
        
        # Resize to logical resolution first
        if screenshot.width != logical_width or screenshot.height != logical_height:
            original_size = (screenshot.width, screenshot.height)
            screenshot = screenshot.resize((logical_width, logical_height), Image.LANCZOS)
            logger.info(f"Resized screenshot from {original_size} to {logical_width}x{logical_height} (Retina: {scale_x}x)")
        
        # If we have window bounds, crop to only show that window
        if self._window_bounds:
            x, y, w, h = self._window_bounds
            # Ensure bounds are within screen
            x = max(0, x)
            y = max(0, y)
            right = min(logical_width, x + w)
            bottom = min(logical_height, y + h)
            
            screenshot = screenshot.crop((x, y, right, bottom))
            logger.info(f"Cropped to window bounds: ({x}, {y}) -> ({right}, {bottom}), size: {right-x}x{bottom-y}")
        else:
            logger.warning("No window bounds - sending FULL SCREEN to grounding model (risk of misclicks!)")
        
        buffered = io.BytesIO()
        screenshot.save(buffered, format="PNG")
        return buffered.getvalue()

    def run(self, prompt: str, max_steps: int = 7, focus_app: Optional[str] = None) -> Any:
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
        
        # Reset agent state and window bounds from previous runs
        self._agent.reset()
        self._window_bounds = None  # Reset - will be set by _focus_target_app if needed
        logger.info(f"Agent S3 executing: {prompt}")
        all_steps_executed = []

        try:
            # Focus target app - use explicit focus_app if provided, otherwise detect from prompt
            if focus_app:
                # Explicit focus app provided by planner
                focused_app = self._focus_app_by_name(focus_app)
            else:
                # Fall back to detecting from prompt (legacy behavior)
                focused_app = self._focus_target_app(prompt)
            
            if focused_app:
                logger.info(f"Pre-focused app: {focused_app}")
                if self._window_bounds:
                    logger.info(f"Constraining clicks to window: {self._window_bounds}")

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

                    # Skip sleep-only actions but continue executing
                    if action_code.strip().startswith("import time; time.sleep") and "pyautogui" not in action_code:
                        logger.debug("Skipping sleep-only action, continuing...")
                        continue

                    # Execute the action, offsetting coordinates if we have window bounds
                    try:
                        adjusted_action = action_code
                        
                        # Log original coordinates for debugging
                        import re
                        click_match = re.search(r'pyautogui\.click\((\d+),\s*(\d+)', action_code)
                        if click_match:
                            orig_x, orig_y = int(click_match.group(1)), int(click_match.group(2))
                            logger.info(f"Original click coords: ({orig_x}, {orig_y})")
                        
                        # If we have window bounds, offset all click coordinates
                        if self._window_bounds:
                            offset_x, offset_y = self._window_bounds[0], self._window_bounds[1]
                            # Find and adjust pyautogui.click(x, y, ...) coordinates
                            def offset_coords(match):
                                x = int(match.group(1)) + offset_x
                                y = int(match.group(2)) + offset_y
                                logger.info(f"Adjusted click: ({match.group(1)}, {match.group(2)}) -> ({x}, {y})")
                                return f"pyautogui.click({x}, {y}"
                            adjusted_action = re.sub(
                                r'pyautogui\.click\((\d+),\s*(\d+)',
                                offset_coords,
                                action_code
                            )
                        else:
                            logger.debug("No window bounds set, using original coordinates")
                        
                        exec(adjusted_action)
                        logger.info(f"Action executed successfully")
                        all_steps_executed.append(adjusted_action)
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
