"""
Claude Code Tool Implementation

Enables invoking Claude Code CLI from phone calls to:
- Fix bugs in codebases
- Run commands
- Make code changes
- Answer questions about code

This integrates with the task state manager for real progress tracking.
"""

import asyncio
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from brain.task_state import get_state_manager


@dataclass
class ClaudeCodeResult:
    """Result from a Claude Code execution."""
    success: bool
    output: str
    summary: str  # Short TTS-friendly summary
    error: Optional[str] = None
    execution_time_seconds: float = 0.0
    exit_code: int = 0


# Common project paths for discovery
KNOWN_PROJECTS = {
    "koda": "~/Documents/GitHub/remotedesktopview",
    "remotedesktopview": "~/Documents/GitHub/remotedesktopview",
    "this project": "~/Documents/GitHub/remotedesktopview",
    "remote desktop": "~/Documents/GitHub/remotedesktopview",
}


def _find_project_directory(project_hint: Optional[str]) -> Optional[str]:
    """
    Intelligently find the project directory based on hints.

    Strategies:
    1. Check known project shortcuts
    2. Look in common locations (~/Documents/GitHub, ~/Projects, etc.)
    3. Check if it's already a valid path
    """
    if not project_hint:
        return None

    hint_lower = project_hint.lower().strip()

    # Check known shortcuts
    for shortcut, path in KNOWN_PROJECTS.items():
        if shortcut in hint_lower:
            expanded = os.path.expanduser(path)
            if os.path.isdir(expanded):
                return expanded

    # Check if it's already a valid path
    expanded_hint = os.path.expanduser(project_hint)
    if os.path.isdir(expanded_hint):
        return expanded_hint

    # Search common locations
    common_locations = [
        "~/Documents/GitHub",
        "~/Projects",
        "~/Code",
        "~/Development",
        "~/dev",
    ]

    for base in common_locations:
        base_expanded = os.path.expanduser(base)
        if not os.path.isdir(base_expanded):
            continue

        # Try to find a matching directory
        try:
            for item in os.listdir(base_expanded):
                item_path = os.path.join(base_expanded, item)
                if os.path.isdir(item_path):
                    if hint_lower in item.lower():
                        return item_path
        except Exception:
            continue

    return None


def _generate_summary(output: str, prompt: str) -> str:
    """
    Generate a short TTS-friendly summary of Claude Code's output.

    This is what gets spoken back to the user over the phone.
    """
    if not output:
        return "Claude Code finished but had no output."

    output_lower = output.lower()

    # Detect common patterns
    if "error" in output_lower and "fixed" not in output_lower:
        # Extract first error line if possible
        lines = output.split("\n")
        for line in lines:
            if "error" in line.lower():
                return f"Claude Code encountered an issue: {line[:80]}"
        return "Claude Code ran into some errors. Check the output for details."

    if "fixed" in output_lower or "resolved" in output_lower:
        return "Claude Code fixed the issue. The changes have been made."

    if "created" in output_lower or "added" in output_lower:
        return "Claude Code made the changes. New code has been added."

    if "no changes" in output_lower or "nothing to" in output_lower:
        return "Claude Code found that no changes were needed."

    if "commit" in output_lower:
        return "Claude Code made the changes and committed them."

    # For questions - try to extract the answer
    if "?" in prompt:
        # Get first meaningful sentence of response
        sentences = re.split(r'[.!?\n]', output)
        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) > 20:  # Skip short fragments
                return f"Claude Code says: {sentence[:100]}..."

    # Default - summarize length
    lines = output.strip().split("\n")
    if len(lines) > 5:
        return f"Claude Code finished. Generated {len(lines)} lines of output."
    else:
        return "Claude Code completed the task."


async def execute_claude_code(
    instruction: str,
    project: Optional[str] = None,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    """
    Execute Claude Code with the given instruction.

    This is the main entry point for the MCP tool.

    Args:
        instruction: Natural language instruction for Claude Code
            (e.g., "fix the login bug", "add error handling to the API")
        project: Optional project name or path. If not provided, uses current directory.
        timeout_seconds: Maximum execution time (default 5 minutes)

    Returns:
        Dictionary with:
        - status: "success" | "failed" | "error"
        - message: Human-readable summary for TTS
        - output: Full Claude Code output
        - error: Error message if failed
        - execution_time_seconds: Duration
    """
    logger.info(f"execute_claude_code | instruction='{instruction[:80]}...' | project={project}")

    # Create task for state tracking
    state_manager = get_state_manager()
    task = state_manager.create_task(f"Claude Code: {instruction[:50]}...")
    state_manager.start_task(task.id)

    try:
        # Find project directory
        state_manager.update_progress(task.id, "Finding project directory")
        cwd = _find_project_directory(project)

        if not cwd:
            cwd = os.getcwd()
            logger.info(f"No project specified, using current directory: {cwd}")
        else:
            logger.info(f"Found project directory: {cwd}")

        state_manager.update_progress(task.id, f"Working in: {os.path.basename(cwd)}")

        # Check if claude CLI is available
        state_manager.update_progress(task.id, "Starting Claude Code")

        # Build command
        # Use --print for non-interactive mode (outputs result and exits)
        # Use --dangerously-skip-permissions to skip permission prompts
        cmd = [
            "claude",
            "--print",
            "--dangerously-skip-permissions",
            instruction
        ]

        # Run the command
        start_time = time.time()

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "TERM": "dumb", "NO_COLOR": "1"}
            )

            # Update progress periodically while waiting
            async def progress_updater():
                elapsed = 0
                while True:
                    await asyncio.sleep(10)
                    elapsed += 10
                    if process.returncode is None:  # Still running
                        state_manager.update_progress(
                            task.id,
                            f"Claude Code working... ({elapsed}s elapsed)"
                        )

            progress_task = asyncio.create_task(progress_updater())

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout_seconds
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                progress_task.cancel()

                error_msg = f"Claude Code timed out after {timeout_seconds} seconds"
                state_manager.fail_task(task.id, error_msg)

                return {
                    "status": "failed",
                    "message": error_msg,
                    "output": "",
                    "error": error_msg,
                    "execution_time_seconds": timeout_seconds
                }
            finally:
                progress_task.cancel()
                try:
                    await progress_task
                except asyncio.CancelledError:
                    pass

        except FileNotFoundError:
            error_msg = "Claude CLI not found. Make sure claude is installed."
            state_manager.fail_task(task.id, error_msg)
            return {
                "status": "error",
                "message": error_msg,
                "output": "",
                "error": error_msg,
                "execution_time_seconds": 0
            }

        execution_time = time.time() - start_time

        # Decode output
        stdout_text = stdout.decode("utf-8", errors="replace") if stdout else ""
        stderr_text = stderr.decode("utf-8", errors="replace") if stderr else ""

        # Generate summary
        summary = _generate_summary(stdout_text or stderr_text, instruction)

        if process.returncode == 0:
            logger.info(f"Claude Code succeeded in {execution_time:.1f}s")

            result = {
                "status": "success",
                "message": summary,
                "output": stdout_text,
                "execution_time_seconds": round(execution_time, 1)
            }
            state_manager.complete_task(task.id, result)
            return result

        else:
            logger.warning(f"Claude Code failed with exit code {process.returncode}")

            error_output = stderr_text or stdout_text or f"Exit code {process.returncode}"
            result = {
                "status": "failed",
                "message": f"Claude Code had issues: {summary}",
                "output": stdout_text,
                "error": error_output[:500],
                "execution_time_seconds": round(execution_time, 1)
            }
            state_manager.fail_task(task.id, error_output[:100])
            return result

    except Exception as e:
        logger.exception(f"Claude Code execution error: {e}")
        state_manager.fail_task(task.id, str(e))
        return {
            "status": "error",
            "message": f"Something went wrong: {str(e)[:50]}",
            "output": "",
            "error": str(e),
            "execution_time_seconds": 0
        }


async def ask_claude_code(
    question: str,
    project: Optional[str] = None,
) -> dict[str, Any]:
    """
    Ask Claude Code a question about the codebase.

    This is a simpler wrapper for questions that don't require code changes.

    Args:
        question: Question about the code (e.g., "how does authentication work?")
        project: Optional project name or path

    Returns:
        Dictionary with answer and metadata
    """
    # Prefix the question to ensure Claude treats it as a read-only query
    instruction = f"Answer this question about the code (don't make any changes): {question}"
    return await execute_claude_code(instruction, project, timeout_seconds=120)


async def check_claude_cli_available() -> bool:
    """Check if the Claude CLI is installed and available."""
    try:
        process = await asyncio.create_subprocess_exec(
            "claude", "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await process.communicate()
        return process.returncode == 0
    except FileNotFoundError:
        return False
    except Exception:
        return False


async def list_available_projects() -> list[str]:
    """List projects that can be used with Claude Code."""
    projects = []

    common_locations = [
        "~/Documents/GitHub",
        "~/Projects",
        "~/Code",
    ]

    for base in common_locations:
        base_expanded = os.path.expanduser(base)
        if not os.path.isdir(base_expanded):
            continue

        try:
            for item in os.listdir(base_expanded):
                item_path = os.path.join(base_expanded, item)
                if os.path.isdir(item_path):
                    # Check if it's a git repo
                    if os.path.isdir(os.path.join(item_path, ".git")):
                        projects.append(item)
        except Exception:
            continue

    return sorted(set(projects))
