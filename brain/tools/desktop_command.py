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


async def _decompose_command_with_llm(prompt: str) -> list[dict[str, str]]:
    """
    Use LLM to break down a complex command into ordered steps.
    
    Each step is classified as either "applescript" (simple) or "agent_s3" (visual).
    
    Example: "Find my video and email it to john@email.com"
    Returns: [
        {"step": "Open Finder to Downloads folder", "method": "applescript"},
        {"step": "Search for video files", "method": "agent_s3"},
        {"step": "Select the most recent video", "method": "agent_s3"},
        {"step": "Open Mail app", "method": "applescript"},
        {"step": "Compose email to john@email.com and attach the video", "method": "agent_s3"},
    ]
    """
    import os
    import json
    
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set, skipping command decomposition")
        return []
    
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        
        system_prompt = """You are a macOS automation planner. Break down user commands into sequential steps.

For each step, classify it as:
- "applescript": Simple operations that can be done with AppleScript (opening apps, opening specific folders like Downloads/Documents/Desktop, navigating to URLs)
- "agent_s3": Complex operations requiring visual analysis (clicking UI elements, searching within apps, typing into fields, reading screen content)

CRITICAL RULES:
1. "Open Downloads folder" = ONE applescript step (NOT two steps!)
2. "Open Documents folder" = ONE applescript step
3. "Open [any folder]" = ONE applescript step  
4. DON'T split "open folder" into "open Finder" + "navigate to folder"
5. Searching within Finder = agent_s3
6. If command is simple (just opening something), return SINGLE step

Return ONLY valid JSON array. No explanation.

Example input: "Open downloads folder"
Example output: [{"step": "Open Downloads folder in Finder", "method": "applescript"}]

Example input: "Open Safari"
Example output: [{"step": "Open Safari", "method": "applescript"}]

Example input: "Open downloads folder and search for my presentation"
Example output: [{"step": "Open Downloads folder in Finder", "method": "applescript"}, {"step": "Search for presentation file in Downloads folder", "method": "agent_s3"}]

Example input: "Search for matrix.png in Downloads"
Example output: [{"step": "Open Downloads folder in Finder", "method": "applescript"}, {"step": "Search for matrix.png file", "method": "agent_s3"}]

Example input: "Find my video and email it to john@email.com"  
Example output: [{"step": "Open Downloads folder in Finder", "method": "applescript"}, {"step": "Search for video files and select the most recent one", "method": "agent_s3"}, {"step": "Open Mail app", "method": "applescript"}, {"step": "Compose email to john@email.com, attach the video, and send", "method": "agent_s3"}]"""

        start_time = datetime.now()
        response = client.messages.create(
            model="claude-3-5-haiku-20241022",
            max_tokens=500,
            messages=[{"role": "user", "content": f"Break down this command: {prompt}"}],
            system=system_prompt,
        )
        
        generation_time = (datetime.now() - start_time).total_seconds()
        result_text = response.content[0].text.strip()
        logger.info(f"Command decomposition took {generation_time:.2f}s")
        logger.debug(f"Decomposition result: {result_text}")
        
        # Parse JSON
        steps = json.loads(result_text)
        if isinstance(steps, list) and len(steps) > 0:
            logger.info(f"Decomposed into {len(steps)} steps: {[s.get('step', '')[:40] for s in steps]}")
            return steps
        
        return []
        
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse decomposition JSON: {e}")
        return []
    except Exception as e:
        logger.warning(f"Command decomposition failed: {e}")
        return []


def _split_compound_command(prompt: str) -> tuple[Optional[str], Optional[str]]:
    """
    DEPRECATED: Use _decompose_command_with_llm instead.
    Simple regex-based splitting kept as fallback.
    """
    import re
    
    # Common conjunctions that split compound commands
    split_patterns = [
        r'\s+and\s+then\s+',
        r'\s+then\s+',
        r'\s+and\s+',
    ]
    
    for pattern in split_patterns:
        parts = re.split(pattern, prompt, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) == 2:
            first_part = parts[0].strip()
            second_part = parts[1].strip()
            
            # Check if first part is a simple "open folder" type command
            simple_keywords = ['open', 'launch', 'go to', 'navigate']
            folder_keywords = ['download', 'document', 'desktop', 'folder', 'finder']
            
            first_lower = first_part.lower()
            is_simple_open = any(first_lower.startswith(kw) for kw in simple_keywords)
            is_folder_operation = any(kw in first_lower for kw in folder_keywords)
            
            if is_simple_open and is_folder_operation:
                logger.info(f"Regex split: '{first_part}' + '{second_part}'")
                return (first_part, second_part)
    
    return (None, None)


async def execute_desktop_command(
    prompt: str,
    screenshot_after: bool = True,
    force_agent_s: bool = False,
    max_agent_steps: int = 10  # Allow more steps for complex multi-part tasks
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
        # Use LLM to decompose complex commands into steps
        if not force_agent_s:
            steps = await _decompose_command_with_llm(prompt)
            
            if len(steps) > 1:
                # Multi-step command - execute each step
                logger.info(f"Executing {len(steps)} decomposed steps")
                all_steps_executed = []
                final_result = None
                
                for i, step_info in enumerate(steps):
                    step_prompt = step_info.get("step", "")
                    step_method = step_info.get("method", "agent_s3").lower()
                    
                    logger.info(f"Step {i+1}/{len(steps)}: [{step_method}] {step_prompt}")
                    
                    if step_method == "applescript":
                        # Execute via AppleScript
                        result = await _try_applescript_first(step_prompt)
                        if result and result.get("status") == "success":
                            all_steps_executed.append(f"[AppleScript] {step_prompt}")
                            await asyncio.sleep(0.5)  # Brief pause
                        else:
                            # AppleScript failed, try Agent S3 as fallback
                            logger.warning(f"AppleScript failed for step, trying Agent S3")
                            step_method = "agent_s3"
                    
                    if step_method == "agent_s3":
                        # Execute via Agent S3
                        agent = await get_agent_instance()
                        # Give more steps for complex operations
                        step_max = 5 if i < len(steps) - 1 else max_agent_steps
                        result = await asyncio.to_thread(agent.run, step_prompt, step_max)
                        if result.success:
                            all_steps_executed.append(f"[Agent S3] {step_prompt}")
                        else:
                            logger.warning(f"Agent S3 failed on step: {step_prompt}")
                            # Continue to next step anyway
                        await asyncio.sleep(0.3)
                    
                    final_result = result
                
                # Return combined result
                if screenshot_after:
                    screenshot_path = f"cache/after_{datetime.now().timestamp()}.png"
                    await asyncio.to_thread(capture_screen_sync, screenshot_path)
                
                return {
                    "status": "success",
                    "message": f"Completed {len(all_steps_executed)} steps",
                    "steps_executed": all_steps_executed,
                    "screenshot_path": screenshot_path if screenshot_after else None,
                }
        
        # Single step or force_agent_s - use original flow
        # For simple "open [app]" commands, use AppleScript directly
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


async def _generate_and_execute_applescript(prompt: str) -> Optional[dict[str, Any]]:
    """
    Use Claude to dynamically generate AppleScript for the user's command.
    
    This is a general-purpose approach that:
    - Understands context (e.g., "Downloads in Finder" = the folder)
    - Generates appropriate AppleScript for any macOS automation task
    - Is not hardcoded for specific commands
    - Falls back to None if the command is too complex for AppleScript
    
    Args:
        prompt: The user's natural language command
        
    Returns:
        Success dict if AppleScript worked, None if command needs Agent S3
    """
    import os
    
    try:
        import anthropic
    except ImportError:
        logger.warning("anthropic package not installed for AppleScript generation")
        return None
    
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set for AppleScript generation")
        return None
    
    # Prompt Claude to generate AppleScript
    generation_prompt = f'''You are a macOS automation expert. Generate AppleScript to accomplish the user's task.

RULES:
1. Output ONLY the AppleScript code, nothing else (no markdown, no explanation)
2. If the task CANNOT be done with AppleScript alone (requires visual analysis, clicking specific UI elements by appearance, or complex multi-step reasoning), output exactly: NEEDS_VISION
3. Use proper macOS paths (e.g., "Downloads" folder = folder "Downloads" of home)
4. Keep scripts simple and reliable
5. Include small delays where needed for UI responsiveness

EXAMPLES:

User: "Open the Downloads folder"
tell application "Finder"
    activate
    open folder "Downloads" of home
end tell

User: "Open Documents in Finder"
tell application "Finder"
    activate
    open folder "Documents" of home
end tell

User: "Create a new note"
tell application "Notes"
    activate
    make new note
end tell

User: "Open Terminal and run a command"
NEEDS_VISION

User: "Click the submit button"
NEEDS_VISION

User: "Find my most recent download and open it"
NEEDS_VISION

User: "{prompt}"
'''

    try:
        client = anthropic.Anthropic(api_key=api_key)
        
        start_time = datetime.now()
        response = client.messages.create(
            model="claude-3-5-haiku-20241022",
            max_tokens=500,
            messages=[{"role": "user", "content": generation_prompt}]
        )
        
        generated_script = response.content[0].text.strip()
        generation_time = (datetime.now() - start_time).total_seconds()
        
        logger.info(f"AppleScript generation took {generation_time:.2f}s")
        
        # Check if Claude says this needs vision
        if "NEEDS_VISION" in generated_script:
            logger.info(f"LLM determined command needs vision: {prompt}")
            return None
        
        # Validate it looks like AppleScript (basic check)
        if not any(keyword in generated_script.lower() for keyword in ["tell", "application", "activate", "open", "keystroke"]):
            logger.warning(f"Generated script doesn't look like AppleScript: {generated_script[:100]}")
            return None
        
        logger.info(f"Generated AppleScript: {generated_script[:100]}...")
        
        # Execute the generated AppleScript
        exec_start = datetime.now()
        result = await asyncio.to_thread(
            subprocess.run,
            ["osascript", "-e", generated_script],
            capture_output=True,
            timeout=15
        )
        execution_time = (datetime.now() - exec_start).total_seconds()
        total_time = (datetime.now() - start_time).total_seconds()
        
        if result.returncode == 0:
            logger.info(f"LLM-generated AppleScript succeeded in {total_time:.2f}s (gen: {generation_time:.2f}s, exec: {execution_time:.2f}s)")
            return {
                "status": "success",
                "message": f"Done",
                "steps_executed": [f"LLM-generated AppleScript executed"],
                "execution_time_seconds": total_time,
                "method": "llm_applescript",
            }
        else:
            stderr = result.stderr.decode() if result.stderr else ""
            logger.warning(f"LLM-generated AppleScript failed: {stderr}")
            return None
            
    except Exception as e:
        logger.warning(f"AppleScript generation/execution error: {e}")
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
    
    # Check if this is a LOCAL file/folder search (NOT a web search)
    # If it mentions Finder, Downloads, Documents, folder, file, etc., skip web search
    local_search_keywords = [
        "finder", "downloads", "documents", "desktop", "folder", "file",
        "directory", "local", "my computer", "my mac", "in the", "within"
    ]
    is_local_search = any(keyword in prompt_lower for keyword in local_search_keywords)
    
    # Only do web search if it's NOT a local file/folder search
    if not is_local_search:
        # Check for WEB search commands
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
    else:
        logger.info(f"Detected local file/folder search, skipping web search: {prompt}")
    
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
        # Not a simple "open [app]" command
        # Try LLM-generated AppleScript before falling back to Agent S3
        logger.info(f"Command not a simple app open, trying LLM-generated AppleScript: {prompt}")
        llm_result = await _generate_and_execute_applescript(prompt)
        if llm_result:
            return llm_result
        # LLM said this needs vision or failed, let Agent-S handle it
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
            # Simple AppleScript failed, try LLM-generated AppleScript
            logger.info(f"Simple AppleScript failed, trying LLM-generated AppleScript")
            llm_result = await _generate_and_execute_applescript(prompt)
            if llm_result:
                return llm_result
            return None
            
    except Exception as e:
        logger.warning(f"AppleScript error: {e}")
        # Try LLM-generated AppleScript before giving up
        llm_result = await _generate_and_execute_applescript(prompt)
        if llm_result:
            return llm_result
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
