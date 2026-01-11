"""
Desktop Command Tool Implementation

Wraps Agent-S GUI automation with:
- Natural language command execution
- Screenshot capture and verification
- Error handling and recovery
- Spoken response generation
"""

import asyncio
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from brain.agent_manager import get_agent_instance, AgentExecutionError


async def execute_desktop_command(
    prompt: str,
    screenshot_after: bool = True,
    force_agent_s: bool = False,
    max_agent_steps: int = 3
) -> dict[str, Any]:
    """
    Execute a natural language GUI command using Agent-S.

    Args:
        prompt: Natural language description of the action
        screenshot_after: Capture screenshot after execution
        force_agent_s: If True, skip AppleScript fast path and use Agent-S only

    Returns:
        Dictionary with:
        - status: "success" | "failed" | "error"
        - message: Human-readable result (for TTS)
        - steps_executed: List of actions Agent-S performed
        - screenshot_path: Optional path to verification screenshot
        - error: Optional error message if failed
    """
    logger.info(f"Executing command: {prompt}" + (" [FORCE AGENT-S]" if force_agent_s else ""))

    try:
        # For simple "open [app]" commands, use AppleScript directly
        # This is more reliable than pyautogui which requires accessibility permissions
        # UNLESS force_agent_s is True
        if not force_agent_s:
            applescript_result = await _try_applescript_first(prompt)
            if applescript_result:
                logger.info(f"Command executed via AppleScript: {applescript_result['message']}")
                
                # Capture verification screenshot if requested
                if screenshot_after:
                    screenshot_path = f"cache/after_{datetime.now().timestamp()}.png"
                    await asyncio.to_thread(capture_screen_sync, screenshot_path)
                    applescript_result["screenshot_path"] = screenshot_path
                
                return applescript_result

        # For complex commands, use Agent-S (limited steps to prevent long runs)
        # Get Agent-S instance
        agent = await get_agent_instance()

        # Execute command (blocking, so run in thread)
        # Limit steps to prevent 4+ minute runs
        start_time = datetime.now()
        result = await asyncio.to_thread(agent.run, prompt, max_agent_steps)
        execution_time = (datetime.now() - start_time).total_seconds()

        logger.info(f"Command execution took {execution_time:.2f}s")

        if result.success:
            # Generate spoken confirmation
            spoken_response = _generate_spoken_confirmation(prompt, result)

            response = {
                "status": "success",
                "message": spoken_response,
                "steps_executed": result.steps_executed if hasattr(result, "steps_executed") else [],
                "execution_time_seconds": execution_time
            }

            # Capture verification screenshot if requested
            if screenshot_after:
                logger.debug("Capturing verification screenshot")
                screenshot_path = f"cache/after_{datetime.now().timestamp()}.png"
                await asyncio.to_thread(capture_screen_sync, screenshot_path)
                response["screenshot_path"] = screenshot_path

            logger.info(f"Command succeeded: {spoken_response}")
            return response

        else:
            # Agent-S failed to complete the task
            error_message = result.error_message if hasattr(result, "error_message") else "Unknown error"
            logger.warning(f"Agent-S failed: {error_message}")

            # Try AppleScript fallback for simple "open" commands
            fallback_result = await _try_applescript_fallback(prompt, error_message)
            if fallback_result:
                return fallback_result

            return {
                "status": "failed",
                "message": f"I couldn't complete that action: {error_message}",
                "error": error_message,
                "prompt": prompt,
                "execution_time_seconds": execution_time
            }

    except Exception as e:
        logger.exception(f"Command execution error: {e}")
        return {
            "status": "error",
            "message": "I encountered an error while trying to do that.",
            "error": str(e),
            "error_type": type(e).__name__,
            "prompt": prompt
        }


async def _execute_url_applescript(url: str) -> Optional[dict[str, Any]]:
    """
    Navigate to a URL using AppleScript.
    """
    try:
        applescript = f'''
        tell application "Safari"
            activate
            open location "{url}"
        end tell
        '''
        
        start_time = datetime.now()
        result = await asyncio.to_thread(
            subprocess.run,
            ["osascript", "-e", applescript],
            capture_output=True,
            timeout=10
        )
        execution_time = (datetime.now() - start_time).total_seconds()
        
        if result.returncode == 0:
            logger.info(f"AppleScript URL navigation succeeded: {url} in {execution_time:.2f}s")
            return {
                "status": "success",
                "message": f"Opening {url}",
                "steps_executed": [f"AppleScript: open Safari to {url}"],
                "execution_time_seconds": execution_time,
                "method": "applescript",
            }
        else:
            stderr = result.stderr.decode() if result.stderr else ""
            logger.warning(f"AppleScript URL navigation failed: {stderr}")
            return None
            
    except Exception as e:
        logger.warning(f"AppleScript URL error: {e}")
        return None


async def _execute_search_applescript(search_query: str) -> Optional[dict[str, Any]]:
    """
    Execute a search using AppleScript to open Safari with a Google search URL.
    
    Args:
        search_query: What to search for
        
    Returns:
        Success dict if search worked, None otherwise
    """
    import urllib.parse
    
    try:
        # URL encode the search query
        encoded_query = urllib.parse.quote(search_query)
        search_url = f"https://www.google.com/search?q={encoded_query}"
        
        # AppleScript to open Safari with the search URL
        applescript = f'''
        tell application "Safari"
            activate
            open location "{search_url}"
        end tell
        '''
        
        start_time = datetime.now()
        result = await asyncio.to_thread(
            subprocess.run,
            ["osascript", "-e", applescript],
            capture_output=True,
            timeout=10
        )
        execution_time = (datetime.now() - start_time).total_seconds()
        
        if result.returncode == 0:
            logger.info(f"AppleScript search succeeded: searched '{search_query}' in {execution_time:.2f}s")
            return {
                "status": "success",
                "message": f"Searching for {search_query}",
                "steps_executed": [f"AppleScript: open Safari with Google search for '{search_query}'"],
                "execution_time_seconds": execution_time,
                "method": "applescript",
            }
        else:
            stderr = result.stderr.decode() if result.stderr else ""
            logger.warning(f"AppleScript search failed: {stderr}")
            return None
            
    except Exception as e:
        logger.warning(f"AppleScript search error: {e}")
        return None


async def _try_applescript_first(prompt: str) -> Optional[dict[str, Any]]:
    """
    Try AppleScript FIRST for simple commands before using Agent-S.
    
    This handles common cases like "open Safari" that are simple enough
    to do directly without Agent-S LLM reasoning and pyautogui.
    
    Args:
        prompt: The command to execute
        
    Returns:
        Success dict if AppleScript worked, None if command isn't simple enough
    """
    import re
    import urllib.parse
    
    # Only works on macOS
    if sys.platform != "darwin":
        return None
    
    prompt_lower = prompt.lower().strip()
    
    # Check for "go to [url]" or "navigate to [url]" commands
    url_patterns = [
        r"(?:go to|navigate to|open|visit)\s+(?:the\s+)?(?:website\s+)?(?:https?://)?([a-zA-Z0-9][-a-zA-Z0-9]*(?:\.[a-zA-Z0-9][-a-zA-Z0-9]*)+(?:/\S*)?)",
        r"(?:go to|navigate to|open)\s+([a-zA-Z0-9]+\.(?:com|org|net|io|dev|co|app|ai)(?:/\S*)?)",
    ]
    
    for pattern in url_patterns:
        match = re.search(pattern, prompt_lower)
        if match:
            url = match.group(1).strip()
            if not url.startswith("http"):
                url = "https://" + url
            logger.info(f"Using AppleScript to navigate to: {url}")
            return await _execute_url_applescript(url)
    
    # Check for search commands first
    # Patterns: "search for X", "search X in safari", "google X", "look up X"
    search_patterns = [
        r"search\s+(?:for\s+)?(.+?)(?:\s+in\s+safari|\s+on\s+google)?$",
        r"google\s+(.+)$",
        r"look\s+up\s+(.+)$",
        r"find\s+(?:information\s+(?:on|about)\s+)?(.+?)(?:\s+online)?$",
    ]
    
    for pattern in search_patterns:
        match = re.search(pattern, prompt_lower)
        if match:
            search_query = match.group(1).strip()
            logger.info(f"Using AppleScript to search for: {search_query}")
            return await _execute_search_applescript(search_query)
    
    # Check if this is a simple "open [app]" command
    # Match patterns like "open safari", "launch chrome", "start finder"
    open_patterns = ["open ", "launch ", "start "]
    app_name = None
    
    for pattern in open_patterns:
        if prompt_lower.startswith(pattern):
            # Extract what comes after "open "
            remainder = prompt_lower[len(pattern):].strip()
            # Remove common suffixes
            for suffix in [" application", " app", " browser", " on my computer", " on my mac"]:
                if remainder.endswith(suffix):
                    remainder = remainder[:-len(suffix)].strip()
            app_name = remainder
            break
    
    if not app_name:
        # Not a simple open command, let Agent-S handle it
        return None
    
    logger.info(f"Using AppleScript to open: {app_name}")
    
    # Map common names to actual app names
    app_name_mapping = {
        "safari": "Safari",
        "chrome": "Google Chrome",
        "google chrome": "Google Chrome",
        "firefox": "Firefox",
        "finder": "Finder",
        "mail": "Mail",
        "messages": "Messages",
        "notes": "Notes",
        "calendar": "Calendar",
        "photos": "Photos",
        "music": "Music",
        "terminal": "Terminal",
        "settings": "System Settings",
        "system settings": "System Settings",
        "system preferences": "System Preferences",
        "slack": "Slack",
        "discord": "Discord",
        "zoom": "zoom.us",
        "teams": "Microsoft Teams",
        "vscode": "Visual Studio Code",
        "visual studio code": "Visual Studio Code",
        "spotify": "Spotify",
        "xcode": "Xcode",
        "word": "Microsoft Word",
        "excel": "Microsoft Excel",
        "powerpoint": "Microsoft PowerPoint",
        "preview": "Preview",
        "textedit": "TextEdit",
        "activity monitor": "Activity Monitor",
    }
    
    # Get the proper app name
    actual_app_name = app_name_mapping.get(app_name.lower(), app_name.title())
    
    try:
        # Special handling for Finder - open a new window
        if actual_app_name == "Finder":
            applescript = '''
            tell application "Finder"
                activate
                make new Finder window
            end tell
            '''
        else:
            # Standard activate for other apps
            applescript = f'tell application "{actual_app_name}" to activate'
        
        start_time = datetime.now()
        result = await asyncio.to_thread(
            subprocess.run,
            ["osascript", "-e", applescript],
            capture_output=True,
            timeout=10
        )
        execution_time = (datetime.now() - start_time).total_seconds()
        
        if result.returncode == 0:
            logger.info(f"AppleScript succeeded: opened {actual_app_name} in {execution_time:.2f}s")
            return {
                "status": "success",
                "message": f"Opening {actual_app_name}",
                "steps_executed": [f"AppleScript: activate {actual_app_name}"],
                "execution_time_seconds": execution_time,
                "method": "applescript",
            }
        else:
            stderr = result.stderr.decode() if result.stderr else ""
            logger.warning(f"AppleScript failed: {stderr}")
            # Return None to let Agent-S try
            return None
            
    except Exception as e:
        logger.warning(f"AppleScript error: {e}")
        # Return None to let Agent-S try
        return None


async def _try_applescript_fallback(prompt: str, error_message: str) -> Optional[dict[str, Any]]:
    """
    Try AppleScript as a fallback for simple commands when Agent-S fails.
    
    This handles common cases like "open Safari" that Agent-S might struggle with
    due to LLM planner issues (empty subtask list).
    
    Args:
        prompt: The original command that failed
        error_message: The error message from Agent-S
        
    Returns:
        Success dict if AppleScript worked, None otherwise
    """
    # Only try fallback on macOS and for specific failure types
    if sys.platform != "darwin":
        return None
    
    # Check if this is a simple "open [app]" command
    prompt_lower = prompt.lower().strip()
    
    # Match patterns like "open safari", "launch chrome", "start finder"
    open_patterns = ["open ", "launch ", "start "]
    app_name = None
    
    for pattern in open_patterns:
        if prompt_lower.startswith(pattern):
            # Extract what comes after "open "
            remainder = prompt_lower[len(pattern):].strip()
            # Remove common suffixes
            for suffix in [" application", " app", " browser"]:
                if remainder.endswith(suffix):
                    remainder = remainder[:-len(suffix)].strip()
            app_name = remainder
            break
    
    if not app_name:
        # Not a simple open command, can't use fallback
        return None
    
    logger.info(f"Agent-S failed, trying AppleScript fallback to open: {app_name}")
    
    # Map common names to actual app names
    app_name_mapping = {
        "safari": "Safari",
        "chrome": "Google Chrome",
        "google chrome": "Google Chrome",
        "firefox": "Firefox",
        "finder": "Finder",
        "mail": "Mail",
        "messages": "Messages",
        "notes": "Notes",
        "calendar": "Calendar",
        "photos": "Photos",
        "music": "Music",
        "terminal": "Terminal",
        "settings": "System Settings",
        "system settings": "System Settings",
        "system preferences": "System Preferences",
        "slack": "Slack",
        "discord": "Discord",
        "zoom": "zoom.us",
        "teams": "Microsoft Teams",
        "vscode": "Visual Studio Code",
        "visual studio code": "Visual Studio Code",
        "spotify": "Spotify",
        "xcode": "Xcode",
    }
    
    # Get the proper app name
    actual_app_name = app_name_mapping.get(app_name.lower(), app_name.title())
    
    try:
        # Use AppleScript to open the application
        applescript = f'tell application "{actual_app_name}" to activate'
        
        result = await asyncio.to_thread(
            subprocess.run,
            ["osascript", "-e", applescript],
            capture_output=True,
            timeout=10
        )
        
        if result.returncode == 0:
            logger.info(f"AppleScript fallback succeeded: opened {actual_app_name}")
            return {
                "status": "success",
                "message": f"Opening {actual_app_name}",
                "steps_executed": [f"AppleScript: activate {actual_app_name}"],
                "fallback_used": "applescript",
                "original_error": error_message
            }
        else:
            stderr = result.stderr.decode() if result.stderr else ""
            logger.warning(f"AppleScript fallback failed: {stderr}")
            return None
            
    except Exception as e:
        logger.warning(f"AppleScript fallback error: {e}")
        return None


def _generate_spoken_confirmation(prompt: str, result: Any) -> str:
    """
    Generate a short, natural spoken confirmation for TTS.

    Examples:
    - "open Chrome" → "Opening Chrome"
    - "click send button" → "Clicked send button"
    - "type hello in search box" → "Typed hello in search box"

    Keeps responses SHORT (<15 words) for phone call UX.
    """
    # Extract action verb from prompt
    prompt_lower = prompt.lower()

    # Common action patterns
    if "open" in prompt_lower:
        # Extract app name
        app_name = _extract_app_name(prompt)
        return f"Opening {app_name}"

    elif "click" in prompt_lower:
        return f"Done. I clicked that for you."

    elif "type" in prompt_lower or "enter" in prompt_lower:
        return f"Done. I typed that for you."

    elif "navigate" in prompt_lower or "go to" in prompt_lower:
        return f"Navigated to that page"

    elif "close" in prompt_lower:
        return f"Closed that window"

    elif "scroll" in prompt_lower:
        return f"Scrolled the page"

    elif "search" in prompt_lower or "find" in prompt_lower:
        return f"Found that for you"

    else:
        # Generic confirmation
        return "Done. That action completed successfully."


def _extract_app_name(prompt: str) -> str:
    """Extract application name from open command."""
    prompt_lower = prompt.lower()

    # Common app names
    apps = [
        "chrome", "safari", "firefox", "edge",
        "mail", "messages", "notes", "calendar",
        "finder", "terminal", "settings", "system settings",
        "slack", "discord", "zoom", "teams",
        "vscode", "xcode", "pycharm",
        "spotify", "music", "photos"
    ]

    for app in apps:
        if app in prompt_lower:
            return app.title()

    # Default
    return "that application"


def capture_screen_sync(save_path: str) -> str:
    """
    Capture a screenshot synchronously.

    Args:
        save_path: Path to save the screenshot

    Returns:
        Path to the saved screenshot

    Raises:
        RuntimeError: If screenshot capture fails
    """
    logger.debug(f"Capturing screenshot to: {save_path}")

    # Ensure cache directory exists
    Path(save_path).parent.mkdir(parents=True, exist_ok=True)

    try:
        if sys.platform == "darwin":
            # macOS: Use screencapture
            subprocess.run(
                ["screencapture", "-x", save_path],
                check=True,
                capture_output=True
            )

        elif sys.platform == "win32":
            # Windows: Use PowerShell
            ps_script = f"""
            Add-Type -AssemblyName System.Windows.Forms
            $screen = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
            $bitmap = New-Object System.Drawing.Bitmap($screen.Width, $screen.Height)
            $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
            $graphics.CopyFromScreen($screen.Location, [System.Drawing.Point]::Empty, $screen.Size)
            $bitmap.Save("{save_path}")
            """
            subprocess.run(
                ["powershell", "-Command", ps_script],
                check=True,
                capture_output=True
            )

        else:
            raise RuntimeError(f"Screenshot not supported on platform: {sys.platform}")

        logger.debug(f"Screenshot saved: {save_path}")
        return save_path

    except subprocess.CalledProcessError as e:
        error_msg = f"Screenshot capture failed: {e.stderr.decode() if e.stderr else str(e)}"
        logger.error(error_msg)
        raise RuntimeError(error_msg) from e


def get_active_apps() -> list[str]:
    """
    Get list of currently running applications.

    Returns:
        List of application names

    Raises:
        RuntimeError: If unable to get application list
    """
    logger.debug("Getting active applications")

    try:
        if sys.platform == "darwin":
            # macOS: Use osascript
            result = subprocess.run(
                [
                    "osascript",
                    "-e",
                    'tell application "System Events" to get name of every process whose background only is false'
                ],
                check=True,
                capture_output=True,
                text=True
            )

            # Parse comma-separated list
            apps = [app.strip() for app in result.stdout.split(",")]
            logger.debug(f"Found {len(apps)} active applications")
            return apps

        elif sys.platform == "win32":
            # Windows: Use PowerShell
            ps_script = """
            Get-Process | Where-Object {$_.MainWindowTitle -ne ""} | Select-Object -ExpandProperty ProcessName
            """
            result = subprocess.run(
                ["powershell", "-Command", ps_script],
                check=True,
                capture_output=True,
                text=True
            )

            # Parse line-separated list
            apps = [app.strip() for app in result.stdout.split("\n") if app.strip()]
            logger.debug(f"Found {len(apps)} active applications")
            return apps

        else:
            raise RuntimeError(f"Platform not supported: {sys.platform}")

    except subprocess.CalledProcessError as e:
        error_msg = f"Failed to get active applications: {e.stderr.decode() if e.stderr else str(e)}"
        logger.error(error_msg)
        raise RuntimeError(error_msg) from e
