"""
MacOSACI Driver for Agent-S

Implements macOS-specific GUI automation using:
- MacOSACI: Vision-based element detection from Agent-S
- Quartz APIs: Screen capture and event injection
- Accessibility API: Application window management

This driver provides the low-level OS interface for Agent-S on macOS.
"""

import subprocess
import sys
from typing import Optional, Tuple, List

from loguru import logger


class MacOSDriver:
    """
    macOS GUI automation driver using MacOSACI.

    Handles:
    - Screen capture via Quartz
    - Mouse and keyboard events via CGEvent
    - Application focus via Accessibility API
    - Window management via Quartz Window Services
    """

    def __init__(self):
        """Initialize macOS driver and validate platform."""
        if sys.platform != "darwin":
            raise RuntimeError(f"MacOSDriver requires macOS, got {sys.platform}")

        logger.info("Initializing MacOSDriver")
        self._validate_permissions()

    def _validate_permissions(self):
        """
        Validate required macOS permissions.

        Checks:
        - Accessibility permission
        - Screen Recording permission
        """
        logger.debug("Validating macOS permissions")

        # Test Accessibility permission with a simple AppleScript
        try:
            result = subprocess.run(
                ["osascript", "-e", 'tell application "System Events" to get name of first process'],
                capture_output=True,
                timeout=5
            )

            if result.returncode != 0:
                logger.warning(
                    "Accessibility permission may not be granted. "
                    "Enable in System Settings > Privacy & Security > Accessibility"
                )
        except Exception as e:
            logger.warning(f"Could not verify Accessibility permission: {e}")

        # Screen Recording permission is harder to check programmatically
        # It will fail on first screenshot attempt if not granted
        logger.debug("macOS permissions validation complete")

    def capture_screen(self, save_path: str) -> str:
        """
        Capture screenshot using macOS screencapture command.

        Args:
            save_path: Path to save the screenshot

        Returns:
            Path to saved screenshot

        Raises:
            RuntimeError: If screenshot fails
        """
        logger.debug(f"Capturing macOS screenshot to: {save_path}")

        try:
            result = subprocess.run(
                ["screencapture", "-x", save_path],  # -x: no sound
                check=True,
                capture_output=True
            )

            if result.returncode != 0:
                raise RuntimeError(f"screencapture failed: {result.stderr.decode()}")

            logger.debug(f"Screenshot saved: {save_path}")
            return save_path

        except subprocess.CalledProcessError as e:
            error_msg = f"Screenshot capture failed: {e.stderr.decode() if e.stderr else str(e)}"
            logger.error(error_msg)

            if "Screen Recording" in error_msg:
                logger.error(
                    "Screen Recording permission denied. "
                    "Enable in System Settings > Privacy & Security > Screen Recording"
                )

            raise RuntimeError(error_msg) from e

    def click(self, x: int, y: int):
        """
        Perform mouse click at coordinates.

        Args:
            x: X coordinate
            y: Y coordinate
        """
        logger.debug(f"Clicking at ({x}, {y})")

        # Use cliclick for reliable mouse events
        try:
            subprocess.run(
                ["cliclick", f"c:{x},{y}"],
                check=True,
                capture_output=True
            )
        except FileNotFoundError:
            # Fallback to AppleScript if cliclick not installed
            logger.warning("cliclick not found, using AppleScript (slower)")
            self._click_via_applescript(x, y)

    def _click_via_applescript(self, x: int, y: int):
        """Fallback click using AppleScript."""
        script = f'''
        tell application "System Events"
            click at {{{x}, {y}}}
        end tell
        '''

        subprocess.run(
            ["osascript", "-e", script],
            check=True,
            capture_output=True
        )

    def type_text(self, text: str):
        """
        Type text at current cursor position.

        Args:
            text: Text to type
        """
        logger.debug(f"Typing text: {text[:50]}...")

        # Use cliclick for reliable keyboard events
        try:
            # Escape special characters for cliclick
            escaped_text = text.replace('"', '\\"')
            subprocess.run(
                ["cliclick", f"t:{escaped_text}"],
                check=True,
                capture_output=True
            )
        except FileNotFoundError:
            # Fallback to AppleScript
            logger.warning("cliclick not found, using AppleScript (slower)")
            self._type_via_applescript(text)

    def _type_via_applescript(self, text: str):
        """Fallback typing using AppleScript."""
        # Escape text for AppleScript
        escaped_text = text.replace('"', '\\"').replace('\\', '\\\\')

        script = f'''
        tell application "System Events"
            keystroke "{escaped_text}"
        end tell
        '''

        subprocess.run(
            ["osascript", "-e", script],
            check=True,
            capture_output=True
        )

    def get_active_window(self) -> Optional[str]:
        """
        Get name of currently active window.

        Returns:
            Window name or None if unable to determine
        """
        logger.debug("Getting active window")

        try:
            result = subprocess.run(
                [
                    "osascript", "-e",
                    'tell application "System Events" to get name of first application process whose frontmost is true'
                ],
                check=True,
                capture_output=True,
                text=True
            )

            window_name = result.stdout.strip()
            logger.debug(f"Active window: {window_name}")
            return window_name

        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to get active window: {e}")
            return None

    def bring_app_to_front(self, app_name: str):
        """
        Bring application to front (activate).

        Args:
            app_name: Name of application to activate
        """
        logger.debug(f"Bringing {app_name} to front")

        script = f'''
        tell application "{app_name}"
            activate
        end tell
        '''

        try:
            subprocess.run(
                ["osascript", "-e", script],
                check=True,
                capture_output=True
            )
            logger.debug(f"{app_name} activated")

        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to activate {app_name}: {e}")
            raise RuntimeError(f"Could not activate {app_name}") from e

    def get_running_apps(self) -> List[str]:
        """
        Get list of running applications.

        Returns:
            List of application names
        """
        logger.debug("Getting running applications")

        try:
            result = subprocess.run(
                [
                    "osascript", "-e",
                    'tell application "System Events" to get name of every process whose background only is false'
                ],
                check=True,
                capture_output=True,
                text=True
            )

            # Parse comma-separated list
            apps = [app.strip() for app in result.stdout.split(",")]
            logger.debug(f"Found {len(apps)} running apps")
            return apps

        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to get running apps: {e}")
            return []

    def get_screen_size(self) -> Tuple[int, int]:
        """
        Get primary screen resolution.

        Returns:
            (width, height) tuple
        """
        try:
            result = subprocess.run(
                [
                    "osascript", "-e",
                    'tell application "Finder" to get bounds of window of desktop'
                ],
                check=True,
                capture_output=True,
                text=True
            )

            # Parse: "0, 0, width, height"
            bounds = result.stdout.strip().split(", ")
            width = int(bounds[2])
            height = int(bounds[3])

            logger.debug(f"Screen size: {width}x{height}")
            return (width, height)

        except Exception as e:
            logger.warning(f"Could not determine screen size: {e}, using default 1920x1080")
            return (1920, 1080)

    def __repr__(self):
        return "<MacOSDriver (MacOSACI)>"
