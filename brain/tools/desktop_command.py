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
from brain.task_state import get_state_manager, TaskStatus


async def _try_simple_applescript(prompt: str) -> Optional[dict[str, Any]]:
    """
    Handle ONLY truly simple, single-action commands via AppleScript.
    
    This is intentionally limited to avoid over-engineering.
    Complex multi-step tasks should go to Agent S3 which can observe and adapt.
    
    Handles:
    - "Open Safari" / "Open Chrome" / "Open Mail" etc.
    - "Go to google.com" / "Open youtube.com"
    - "Open Downloads folder" / "Open Documents"
    
    Does NOT handle:
    - Anything with "search", "find", "email", "send", "click", etc.
    - Multi-step commands
    """
    import re
    
    if sys.platform != "darwin":
        return None
    
    prompt_lower = prompt.lower().strip()
    
    # If command contains action words, let Agent S3 handle it
    action_words = ['search', 'find', 'email', 'send', 'click', 'type', 'compose', 
                    'attach', 'select', 'drag', 'scroll', 'look for', 'and then', 'then ']
    if any(word in prompt_lower for word in action_words):
        logger.debug(f"Command contains action words, delegating to Agent S3: {prompt}")
        return None
    
    # Simple "open [app]" command
    open_match = re.match(r'^(?:open|launch|start)\s+(.+?)(?:\s+app(?:lication)?)?$', prompt_lower)
    if open_match:
        app_or_folder = open_match.group(1).strip()
        
        # Check if it's a folder
        folder_keywords = ['downloads', 'documents', 'desktop', 'folder', 'directory']
        if any(kw in app_or_folder for kw in folder_keywords):
            # Use LLM-generated AppleScript for folders
            return await _generate_and_execute_applescript(prompt)
        
        # It's an app - simple activate
        app_mapping = {
            'safari': 'Safari', 'chrome': 'Google Chrome', 'firefox': 'Firefox',
            'mail': 'Mail', 'messages': 'Messages', 'notes': 'Notes',
            'calendar': 'Calendar', 'finder': 'Finder', 'terminal': 'Terminal',
            'spotify': 'Spotify', 'slack': 'Slack', 'discord': 'Discord',
        }
        actual_app = app_mapping.get(app_or_folder, app_or_folder.title())
        
        try:
            script = f'tell application "{actual_app}" to activate'
            result = await asyncio.to_thread(
                subprocess.run, ["osascript", "-e", script],
                capture_output=True, timeout=5
            )
            if result.returncode == 0:
                return {
                    "status": "success",
                    "message": f"Opened {actual_app}",
                    "steps_executed": [f"AppleScript: open {actual_app}"],
                    "method": "simple_applescript",
                }
        except Exception as e:
            logger.warning(f"Simple AppleScript failed: {e}")
        return None
    
    # Simple "go to [url]" command
    url_match = re.match(r'^(?:go to|navigate to|open)\s+(https?://\S+|\S+\.(com|org|net|io|dev|co|app)(?:/\S*)?)$', prompt_lower)
    if url_match:
        url = url_match.group(1)
        if not url.startswith('http'):
            url = 'https://' + url
        return await _execute_url_applescript(url)
    
    # Not a simple command - let Agent S3 handle it
    return None


async def _capture_screenshot_for_planning() -> str | None:
    """
    Capture and resize a screenshot for vision-based planning.

    Returns base64-encoded JPEG string, or None if capture fails.
    """
    import subprocess
    import io
    import base64
    from PIL import Image

    try:
        # Capture screenshot
        screenshot_path = f"cache/planning_{datetime.now().timestamp()}.png"
        Path("cache").mkdir(exist_ok=True)

        subprocess.run(
            ["screencapture", "-x", screenshot_path],
            check=True,
            capture_output=True
        )

        # Read and resize for Claude vision (max 5MB, resize to 1280px width)
        with Image.open(screenshot_path) as img:
            max_width = 1280
            if img.width > max_width:
                ratio = max_width / img.width
                new_size = (max_width, int(img.height * ratio))
                img = img.resize(new_size, Image.LANCZOS)

            # Convert to JPEG
            buffer = io.BytesIO()
            if img.mode in ('RGBA', 'LA', 'P'):
                img = img.convert('RGB')
            img.save(buffer, format='JPEG', quality=85)
            buffer.seek(0)
            screenshot_base64 = base64.b64encode(buffer.read()).decode("utf-8")

        logger.info(f"Captured planning screenshot: {len(screenshot_base64) // 1024}KB")
        return screenshot_base64

    except Exception as e:
        logger.warning(f"Failed to capture planning screenshot: {e}")
        return None


async def _analyze_screen_and_plan(prompt: str, screenshot_base64: str) -> list[dict[str, str]]:
    """
    Analyze the current screen state and plan only the REMAINING steps needed.
    
    This is the key to being truly agentic - we LOOK first, then plan.
    """
    import os
    import json
    import base64
    
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set, falling back to blind decomposition")
        return await _decompose_command_with_llm(prompt)
    
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        
        system_prompt = """You are a macOS automation planner with VISION. You can see the current screen.

FIRST, analyze what's currently visible on screen:
- What app is in focus?
- What files/windows are visible?
- What state is the system in?

THEN, determine what steps are NEEDED to complete the user's goal.

For each step, provide:
- "step": Description of the goal
- "method": "applescript" (opening apps/folders) or "agent_s3" (visual interaction)
- "focus_app": Which app should be in focus for this step (e.g., "Finder", "Mail", "Safari", or null if N/A)

IMPORTANT RULES:
- ONLY return empty array [] if the EXACT goal is VISIBLY complete on screen
- "Search for X" is NOT complete unless X is visibly highlighted/selected in a search result
- "Open folder" is NOT complete unless that specific folder is open and visible
- If unsure, include the steps - it's better to try than to wrongly skip
- The "focus_app" tells the agent which app window to work in

BE CONSERVATIVE: When in doubt, include the step. Only skip if 100% certain it's done.

Return ONLY valid JSON array. No explanation.

Examples:
- Screen shows: Finder with Downloads open, confusion_matrix.png visible
- User says: "Email this to john@email.com"
- Output: [{"step": "Open Mail app", "method": "applescript", "focus_app": null}, {"step": "Compose email to john@email.com with confusion_matrix.png attached and send", "method": "agent_s3", "focus_app": "Mail"}]

- Screen shows: Mail compose window open with attachment
- User says: "Send the email"
- Output: [{"step": "Click send button", "method": "agent_s3", "focus_app": "Mail"}]

- Screen shows: Desktop with no relevant windows
- User says: "Find my video and email it"
- Output: [{"step": "Open Downloads folder", "method": "applescript", "focus_app": null}, {"step": "Find and select a video file", "method": "agent_s3", "focus_app": "Finder"}, {"step": "Open Mail app", "method": "applescript", "focus_app": null}, {"step": "Compose email with video attached and send", "method": "agent_s3", "focus_app": "Mail"}]"""

        start_time = datetime.now()
        response = client.messages.create(
            model="claude-3-5-haiku-20241022",
            max_tokens=500,
            messages=[{
                "role": "user", 
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": screenshot_base64,
                        },
                    },
                    {
                        "type": "text",
                        "text": f"Current screen is shown above. User wants to: {prompt}\n\nWhat steps are STILL NEEDED? Return JSON array only."
                    }
                ]
            }],
            system=system_prompt,
        )
        
        generation_time = (datetime.now() - start_time).total_seconds()
        result_text = response.content[0].text.strip()
        logger.info(f"Screen analysis + planning took {generation_time:.2f}s")
        logger.debug(f"Plan based on screen: {result_text}")
        
        # Parse JSON
        steps = json.loads(result_text)
        if isinstance(steps, list):
            if len(steps) == 0:
                logger.warning(f"Screen analysis returned empty array - LLM thinks goal is complete. Raw response: {result_text}")
                # Be conservative - if LLM returned empty, fall back to blind decomposition
                logger.info("Falling back to blind decomposition to be safe...")
                return await _decompose_command_with_llm(prompt)
            else:
                logger.info(f"Screen analysis: {len(steps)} steps needed: {[s.get('step', '')[:40] for s in steps]}")
            return steps
        
        return []
        
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse screen analysis JSON: {e}")
        return await _decompose_command_with_llm(prompt)
    except Exception as e:
        logger.warning(f"Screen analysis failed: {e}, falling back to blind decomposition")
        return await _decompose_command_with_llm(prompt)


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
        
        system_prompt = """You are a macOS automation planner. Break down commands into EXPLICIT, ACTIONABLE steps.

For each step, provide:
- "step": SPECIFIC instructions with HOW to do it (not just what)
- "method": "applescript" (opening apps/folders/sending emails) or "agent_s3" (visual interaction like finding files)
- "focus_app": Which app should be focused (e.g., "Finder", "Mail", "Safari", or null)

CRITICAL RULES:
1. For FILE SEARCHES in Finder: Use agent_s3 and say "Click the search bar at the top right, type [filename], then click on the file to select it"
2. For SENDING EMAIL with attachment: Use APPLESCRIPT (not agent_s3)! Say "Email the selected file to [email]"
3. "Open [folder]" = ONE applescript step with focus_app: null
4. Be EXPLICIT about which UI elements to interact with (search bar, etc.)
5. If command says "the file" or implies a file is already selected, assume it IS visible/selected

Return ONLY valid JSON array. No explanation.

Example: "Open downloads folder"
Output: [{"step": "Open Downloads folder", "method": "applescript", "focus_app": null}]

Example: "Search for matrix.png in Downloads"
Output: [{"step": "Open Downloads folder", "method": "applescript", "focus_app": null}, {"step": "Click the search bar at the top right corner, type matrix.png, then click on the file to select it", "method": "agent_s3", "focus_app": "Finder"}]

Example: "Email the file to john@email.com"
Output: [{"step": "Email the selected file to john@email.com", "method": "applescript", "focus_app": null}]

Example: "Find my video and email it to john@email.com"  
Output: [{"step": "Open Downloads folder", "method": "applescript", "focus_app": null}, {"step": "Click the search bar at the top right, type video, click on a video file to select it", "method": "agent_s3", "focus_app": "Finder"}, {"step": "Email the selected file to john@email.com", "method": "applescript", "focus_app": null}]"""

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

    # Create task for state tracking
    state_manager = get_state_manager()
    task = state_manager.create_task(prompt)
    state_manager.start_task(task.id)

    try:
        # HYBRID APPROACH:
        # 1. Try AppleScript for truly simple commands (open app, go to URL)
        # 2. For complex commands: decompose into goals, then execute each agentically

        if not force_agent_s:
            # Only use AppleScript for genuinely simple, single-action commands
            state_manager.update_progress(task.id, "Trying fast path (AppleScript)")
            applescript_result = await _try_simple_applescript(prompt)
            if applescript_result:
                logger.info(f"Simple command executed via AppleScript: {applescript_result['message']}")

                if screenshot_after:
                    screenshot_path = f"cache/after_{datetime.now().timestamp()}.png"
                    await asyncio.to_thread(capture_screen_sync, screenshot_path)
                    applescript_result["screenshot_path"] = screenshot_path
                    state_manager.set_screenshot(screenshot_path)

                # Mark task complete
                state_manager.complete_task(task.id, applescript_result)
                return applescript_result

            # SCREEN-AWARE APPROACH:
            # 1. Capture current screen to understand state
            # 2. Plan only the REMAINING steps needed (not from scratch)
            # 3. This prevents re-doing work that's already done

            state_manager.update_progress(task.id, "Analyzing screen and planning steps")

            # Capture and resize screenshot for vision analysis
            screenshot_base64 = await _capture_screenshot_for_planning()
            if screenshot_base64:
                steps = await _analyze_screen_and_plan(prompt, screenshot_base64)
            else:
                # Fallback to blind decomposition if screenshot fails
                logger.warning("Screenshot capture failed, using blind decomposition")
                steps = await _decompose_command_with_llm(prompt)

            if len(steps) >= 1:
                logger.info(f"Decomposed into {len(steps)} goals - executing agentically")
                state_manager.update_progress(task.id, f"Planned {len(steps)} steps to complete")
                all_steps_executed = []
                agent = await get_agent_instance()

                for i, step_info in enumerate(steps):
                    step_goal = step_info.get("step", "")
                    step_method = step_info.get("method", "agent_s3").lower()
                    focus_app = step_info.get("focus_app")  # Which app to focus for this step

                    # Update progress with current step
                    state_manager.update_progress(
                        task.id,
                        f"Step {i+1}/{len(steps)}: {step_goal[:50]}...",
                        details={"step": i+1, "total": len(steps), "method": step_method}
                    )

                    logger.info(f"Goal {i+1}/{len(steps)}: [{step_method}] {step_goal}" +
                               (f" [focus: {focus_app}]" if focus_app else ""))

                    if step_method == "applescript":
                        # Simple goal - try AppleScript
                        result = await _try_applescript_first(step_goal)
                        if result and result.get("status") == "success":
                            all_steps_executed.append(f"[AppleScript] {step_goal}")
                            await asyncio.sleep(0.5)
                            continue
                        # AppleScript failed, fall through to Agent S3
                        logger.info(f"AppleScript failed, using Agent S3 for: {step_goal}")

                    # Agent S3 executes this goal agentically
                    # Pass the focus_app so it knows which window to focus
                    agentic_prompt = f"{step_goal}. (Part of: {prompt})"

                    # Pass focus_app to agent.run
                    result = await asyncio.to_thread(
                        agent.run, agentic_prompt, max_agent_steps, focus_app
                    )
                    if result.success:
                        all_steps_executed.append(f"[Agent S3] {step_goal}")
                        logger.info(f"Goal completed: {step_goal}")
                    else:
                        logger.warning(f"Goal failed: {step_goal}, continuing to next...")

                    await asyncio.sleep(0.3)

                # Return combined result
                screenshot_path = None
                if screenshot_after:
                    screenshot_path = f"cache/after_{datetime.now().timestamp()}.png"
                    await asyncio.to_thread(capture_screen_sync, screenshot_path)
                    state_manager.set_screenshot(screenshot_path)

                # Generate a specific completion message based on what was done
                completion_message = _generate_multi_step_completion_message(prompt, all_steps_executed)

                final_result = {
                    "status": "success",
                    "message": completion_message,
                    "steps_executed": all_steps_executed,
                    "screenshot_path": screenshot_path,
                }
                state_manager.complete_task(task.id, final_result)
                return final_result

        # For complex commands, use Agent-S (limited steps to prevent long runs)
        # Get Agent-S instance
        state_manager.update_progress(task.id, "Starting visual automation (Agent S3)")
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
                state_manager.set_screenshot(screenshot_path)

            logger.info(f"Command succeeded: {spoken_response}")
            state_manager.complete_task(task.id, response)
            return response

        else:
            # Agent-S failed to complete the task
            error_message = result.error_message if hasattr(result, "error_message") else "Unknown error"
            logger.warning(f"Agent-S failed: {error_message}")

            # Try AppleScript fallback for simple "open" commands
            fallback_result = await _try_applescript_fallback(prompt, error_message)
            if fallback_result:
                state_manager.complete_task(task.id, fallback_result)
                return fallback_result

            fail_response = {
                "status": "failed",
                "message": f"I couldn't complete that action: {error_message}",
                "error": error_message,
                "prompt": prompt,
                "execution_time_seconds": execution_time
            }
            state_manager.fail_task(task.id, error_message)
            return fail_response

    except Exception as e:
        logger.exception(f"Command execution error: {e}")
        state_manager.fail_task(task.id, str(e))
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

User: "Email the selected file to john@email.com"
tell application "Finder"
    set theSelection to selection
    if (count of theSelection) > 0 then
        set theFile to item 1 of theSelection as alias
        set fileName to name of theFile
    else
        error "No file selected in Finder"
    end if
end tell
tell application "Mail"
    activate
    set newMessage to make new outgoing message with properties {{visible:true, subject:"Sharing: " & fileName, content:"Please find the attached file."}}
    tell newMessage
        make new to recipient at end of to recipients with properties {{address:"john@email.com"}}
        make new attachment with properties {{file name:theFile}} at after the last paragraph
    end tell
    delay 1
    send newMessage
end tell
delay 1
tell application "System Events"
    if exists (button "Send Anyway" of window 1 of process "Mail") then
        click button "Send Anyway" of window 1 of process "Mail"
    end if
end tell

User: "Send email to test@example.com with the file I have selected"
tell application "Finder"
    set theSelection to selection
    if (count of theSelection) > 0 then
        set theFile to item 1 of theSelection as alias
        set fileName to name of theFile
    else
        error "No file selected"
    end if
end tell
tell application "Mail"
    activate
    set newMessage to make new outgoing message with properties {{visible:true, subject:"Sharing: " & fileName, content:"Attached file for you."}}
    tell newMessage
        make new to recipient at end of to recipients with properties {{address:"test@example.com"}}
        make new attachment with properties {{file name:theFile}} at after the last paragraph
    end tell
    delay 1
    send newMessage
end tell
delay 1
tell application "System Events"
    if exists (button "Send Anyway" of window 1 of process "Mail") then
        click button "Send Anyway" of window 1 of process "Mail"
    end if
end tell

User: "Open Terminal and run a command"
NEEDS_VISION

User: "Click the submit button"
NEEDS_VISION

User: "Find my most recent download and open it"
NEEDS_VISION

User: "Find a specific file by looking at the screen"
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

            # Generate a more descriptive message based on what was done
            prompt_lower = prompt.lower()
            if "email" in prompt_lower and "@" in prompt_lower:
                done_message = "Email sent successfully"
            elif "open" in prompt_lower:
                done_message = f"Opened {prompt.split('open')[-1].strip()[:30] if 'open' in prompt_lower else 'application'}"
            else:
                done_message = "Done"

            return {
                "status": "success",
                "message": done_message,
                "steps_executed": [f"LLM-generated AppleScript executed: {prompt[:50]}"],
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


def _generate_multi_step_completion_message(prompt: str, steps_executed: list[str]) -> str:
    """
    Generate a specific completion message for multi-step tasks.

    Instead of "Completed 3 of 3 goals", returns messages like:
    - "Email sent to john@example.com"
    - "Found and opened the presentation file"
    - "Chrome opened and navigated to Gmail"
    """
    prompt_lower = prompt.lower()

    # Extract key action from the original prompt
    if "email" in prompt_lower and "@" in prompt_lower:
        # Extract email address
        import re
        email_match = re.search(r'[\w\.-]+@[\w\.-]+', prompt)
        if email_match:
            return f"Email sent to {email_match.group()}"
        return "Email sent successfully"

    elif "send" in prompt_lower and "email" in prompt_lower:
        return "Email sent successfully"

    elif "find" in prompt_lower and "email" in prompt_lower:
        return "Found the file and sent the email"

    elif "find" in prompt_lower and ("open" in prompt_lower or "send" in prompt_lower):
        # Find and open/send pattern
        return "Found and completed the action"

    elif "open" in prompt_lower and "go to" in prompt_lower:
        # Open browser and navigate
        return "Opened and navigated to the page"

    elif "open" in prompt_lower and ("navigate" in prompt_lower or "search" in prompt_lower):
        return "Opened and completed navigation"

    elif "download" in prompt_lower:
        return "Download started"

    elif "upload" in prompt_lower:
        return "Upload completed"

    elif "copy" in prompt_lower and "paste" in prompt_lower:
        return "Copied and pasted successfully"

    elif "create" in prompt_lower:
        return "Created successfully"

    elif "delete" in prompt_lower or "remove" in prompt_lower:
        return "Deleted successfully"

    elif "save" in prompt_lower:
        return "Saved successfully"

    else:
        # Fall back to summarizing what was done
        if len(steps_executed) == 1:
            # Single step - extract action from it
            step = steps_executed[0].replace("[AppleScript]", "").replace("[Agent S3]", "").strip()
            return f"Done: {step[:50]}"
        else:
            # Multiple steps - give count with context
            return f"All {len(steps_executed)} steps completed successfully"


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
