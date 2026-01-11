"""
Task State Manager

Provides real-time awareness of what's happening on the Mac.
This is the "brain" that tracks:
- Active tasks and their progress
- Completed tasks and results
- Current screen state
- Any pending questions or decisions

VAPI tools can query this state to give REAL answers instead of canned responses.
"""

import asyncio
import base64
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Callable
from collections import deque

from loguru import logger


class TaskStatus(Enum):
    """Status of a task."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class TaskProgress:
    """Progress update for a task."""
    message: str
    timestamp: float = field(default_factory=time.time)
    details: Optional[dict] = None


@dataclass
class Task:
    """Represents a task being executed."""
    id: str
    prompt: str
    status: TaskStatus = TaskStatus.PENDING
    started_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    result: Optional[dict] = None
    error: Optional[str] = None
    progress_updates: list[TaskProgress] = field(default_factory=list)
    screenshot_path: Optional[str] = None

    def add_progress(self, message: str, details: Optional[dict] = None):
        """Add a progress update."""
        self.progress_updates.append(TaskProgress(
            message=message,
            timestamp=time.time(),
            details=details
        ))
        logger.info(f"Task {self.id} progress: {message}")

    def complete(self, result: dict):
        """Mark task as completed."""
        self.status = TaskStatus.COMPLETED
        self.completed_at = time.time()
        self.result = result
        logger.info(f"Task {self.id} completed in {self.completed_at - self.started_at:.2f}s")

    def fail(self, error: str):
        """Mark task as failed."""
        self.status = TaskStatus.FAILED
        self.completed_at = time.time()
        self.error = error
        logger.warning(f"Task {self.id} failed: {error}")

    @property
    def duration_seconds(self) -> float:
        """Get task duration."""
        end_time = self.completed_at or time.time()
        return end_time - self.started_at

    @property
    def latest_progress(self) -> Optional[str]:
        """Get the most recent progress message."""
        if self.progress_updates:
            return self.progress_updates[-1].message
        return None

    def to_summary(self) -> dict:
        """Convert to a summary dict for API responses."""
        return {
            "id": self.id,
            "prompt": self.prompt[:100],
            "status": self.status.value,
            "duration_seconds": round(self.duration_seconds, 1),
            "latest_progress": self.latest_progress,
            "result_preview": str(self.result)[:200] if self.result else None,
            "error": self.error
        }


class TaskStateManager:
    """
    Manages task state and provides real-time awareness.

    This is a singleton that tracks all tasks and their progress,
    allowing VAPI to query actual state instead of guessing.
    """

    _instance: Optional["TaskStateManager"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._tasks: dict[str, Task] = {}
        self._task_history: deque[Task] = deque(maxlen=50)  # Keep last 50 tasks
        self._current_task_id: Optional[str] = None
        self._last_screenshot_path: Optional[str] = None
        self._last_screenshot_time: float = 0
        self._progress_callbacks: list[Callable[[Task, str], None]] = []
        self._task_counter = 0
        self._initialized = True

        logger.info("TaskStateManager initialized")

    def _generate_task_id(self) -> str:
        """Generate a unique task ID."""
        self._task_counter += 1
        return f"task_{self._task_counter}_{int(time.time())}"

    def create_task(self, prompt: str) -> Task:
        """Create and register a new task."""
        task_id = self._generate_task_id()
        task = Task(id=task_id, prompt=prompt, status=TaskStatus.PENDING)
        self._tasks[task_id] = task
        self._current_task_id = task_id
        logger.info(f"Created task {task_id}: {prompt[:80]}...")
        return task

    def start_task(self, task_id: str) -> Optional[Task]:
        """Mark a task as in progress."""
        task = self._tasks.get(task_id)
        if task:
            task.status = TaskStatus.IN_PROGRESS
            task.add_progress("Starting task execution")
            self._current_task_id = task_id
        return task

    def update_progress(self, task_id: str, message: str, details: Optional[dict] = None):
        """Update task progress."""
        task = self._tasks.get(task_id)
        if task:
            task.add_progress(message, details)
            # Notify callbacks
            for callback in self._progress_callbacks:
                try:
                    callback(task, message)
                except Exception as e:
                    logger.error(f"Progress callback error: {e}")

    def _restore_previous_context(self, finished_task_id: str):
        """
        If the finished task was the current one, restore context to another active task.
        This handles cases where a short task (like checking status) interrupts a long one.
        """
        if self._current_task_id == finished_task_id:
            # Look for other in-progress tasks
            active_tasks = [
                t for t in self._tasks.values() 
                if t.status == TaskStatus.IN_PROGRESS and t.id != finished_task_id
            ]
            
            if active_tasks:
                # Restore the most recently started active task
                # (Or the one that was running before this one)
                active_tasks.sort(key=lambda t: t.started_at, reverse=True)
                restored = active_tasks[0]
                self._current_task_id = restored.id
                logger.info(f"Restored context to active task {restored.id}: {restored.prompt[:50]}...")
            else:
                self._current_task_id = None

    def complete_task(self, task_id: str, result: dict):
        """Mark a task as completed."""
        task = self._tasks.get(task_id)
        if task:
            task.complete(result)
            self._task_history.append(task)
            self._restore_previous_context(task_id)

    def fail_task(self, task_id: str, error: str):
        """Mark a task as failed."""
        task = self._tasks.get(task_id)
        if task:
            task.fail(error)
            self._task_history.append(task)
            self._restore_previous_context(task_id)

    def get_task(self, task_id: str) -> Optional[Task]:
        """Get a task by ID."""
        return self._tasks.get(task_id)

    def get_current_task(self) -> Optional[Task]:
        """Get the currently executing task."""
        if self._current_task_id:
            return self._tasks.get(self._current_task_id)
        return None

    def get_recent_tasks(self, limit: int = 5) -> list[Task]:
        """Get recent tasks (most recent first)."""
        return list(reversed(list(self._task_history)))[:limit]

    def set_screenshot(self, path: str):
        """Update the last screenshot path."""
        self._last_screenshot_path = path
        self._last_screenshot_time = time.time()

    def get_last_screenshot(self) -> Optional[str]:
        """Get the path to the most recent screenshot."""
        return self._last_screenshot_path

    def add_progress_callback(self, callback: Callable[[Task, str], None]):
        """Add a callback for progress updates (for real-time notifications)."""
        self._progress_callbacks.append(callback)

    def get_system_state(self) -> dict:
        """
        Get comprehensive system state for awareness.

        This is what VAPI should use to understand what's happening.
        """
        current_task = self.get_current_task()
        recent_tasks = self.get_recent_tasks(3)

        state = {
            "has_active_task": current_task is not None,
            "current_task": current_task.to_summary() if current_task else None,
            "recent_tasks": [t.to_summary() for t in recent_tasks],
            "last_screenshot_age_seconds": (
                round(time.time() - self._last_screenshot_time, 1)
                if self._last_screenshot_time else None
            ),
            "timestamp": datetime.now().isoformat()
        }

        return state


# Global instance
_state_manager: Optional[TaskStateManager] = None


def get_state_manager() -> TaskStateManager:
    """Get the global task state manager."""
    global _state_manager
    if _state_manager is None:
        _state_manager = TaskStateManager()
    return _state_manager


async def get_real_status() -> dict:
    """
    Get the REAL current status for VAPI to use.

    This replaces canned responses with actual awareness.

    Returns dict with:
    - is_busy: Whether a task is currently running
    - status_message: Human-readable status
    - current_task: Details of active task if any
    - last_completed: Details of most recent completed task
    """
    manager = get_state_manager()
    current = manager.get_current_task()
    recent = manager.get_recent_tasks(1)
    last_completed = recent[0] if recent else None

    if current:
        # Active task - report real progress
        progress = current.latest_progress or "Working on it"
        duration = current.duration_seconds

        return {
            "is_busy": True,
            "status_message": f"Currently working on: {current.prompt[:50]}... ({progress})",
            "current_task": current.to_summary(),
            "duration_so_far_seconds": round(duration, 1),
            "last_completed": last_completed.to_summary() if last_completed else None
        }

    elif last_completed:
        # No active task - report last result with specifics
        if last_completed.status == TaskStatus.COMPLETED:
            # Get the result message if available (more specific than just the prompt)
            result_message = None
            if last_completed.result:
                result_message = last_completed.result.get("message")

            if result_message:
                status_msg = f"Done. {result_message}"
            else:
                status_msg = f"Completed: {last_completed.prompt[:50]}..."

            return {
                "is_busy": False,
                "status_message": status_msg,
                "last_completed": last_completed.to_summary(),
                "time_since_completion_seconds": round(time.time() - (last_completed.completed_at or 0), 1)
            }
        else:
            return {
                "is_busy": False,
                "status_message": f"Last task failed: {last_completed.error or 'Unknown error'}",
                "last_completed": last_completed.to_summary()
            }

    else:
        # No tasks yet
        return {
            "is_busy": False,
            "status_message": "Ready for commands. No tasks have been executed yet.",
            "current_task": None,
            "last_completed": None
        }


async def describe_current_screen() -> dict:
    """
    Capture and describe what's currently on screen.

    This gives VAPI "eyes" to see what's actually happening.

    Returns dict with:
    - description: LLM-generated description of the screen
    - screenshot_path: Path to the captured screenshot
    - active_app: Currently focused application (if detectable)
    """
    import subprocess
    import sys

    manager = get_state_manager()

    # Capture screenshot
    timestamp = int(time.time())
    screenshot_path = f"cache/screen_{timestamp}.png"
    Path("cache").mkdir(exist_ok=True)

    try:
        if sys.platform == "darwin":
            subprocess.run(
                ["screencapture", "-x", screenshot_path],
                check=True,
                capture_output=True
            )
        else:
            return {
                "description": "Screen capture not supported on this platform",
                "screenshot_path": None,
                "error": "Not macOS"
            }
    except Exception as e:
        return {
            "description": "Failed to capture screen",
            "screenshot_path": None,
            "error": str(e)
        }

    manager.set_screenshot(screenshot_path)

    # Get active application
    active_app = None
    try:
        result = subprocess.run(
            ["osascript", "-e", 'tell application "System Events" to get name of first process whose frontmost is true'],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            active_app = result.stdout.strip()
    except Exception:
        pass

    # Use Claude to describe the screen
    description = await _analyze_screenshot(screenshot_path)

    return {
        "description": description,
        "screenshot_path": screenshot_path,
        "active_app": active_app,
        "timestamp": datetime.now().isoformat()
    }


async def _analyze_screenshot(screenshot_path: str) -> str:
    """Use Claude to analyze a screenshot and describe what's on screen."""
    import anthropic
    import io
    from PIL import Image

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return "Cannot analyze screen: ANTHROPIC_API_KEY not set"

    try:
        # Read and resize image to fit within Claude's 5MB limit
        # Retina screenshots are huge (3600x2338 = ~15MB), resize to 1280px width
        with Image.open(screenshot_path) as img:
            # Calculate new dimensions (maintain aspect ratio)
            max_width = 2560
            if img.width > max_width:
                ratio = max_width / img.width
                new_size = (max_width, int(img.height * ratio))
                img = img.resize(new_size, Image.LANCZOS)

            # Convert to JPEG for smaller size (PNG is lossless = huge)
            buffer = io.BytesIO()
            # Convert RGBA to RGB if needed (JPEG doesn't support alpha)
            if img.mode in ('RGBA', 'LA', 'P'):
                img = img.convert('RGB')
            img.save(buffer, format='JPEG', quality=85)
            buffer.seek(0)
            image_data = base64.b64encode(buffer.read()).decode("utf-8")

        logger.info(f"Screenshot resized for analysis: {len(image_data) // 1024}KB")

        response = client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=600,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": image_data
                        }
                    },
                    {
                        "type": "text",
                        "text": """Analyze this macOS screen and provide a factual description for a blind user.
                        
CRITICAL DETAILS TO EXTRACT:
1. Active Application: Exact name of the frontmost app.
2. Window Title: verification_screen.png for example.
3. Key Content: Read any visible filenames, huge text, or specific status messages.
4. Context: What is the user likely doing?

Format: "The user is in [App] [doing X]. The screen shows [details]. I see files named: [list specific filenames]."
Keep it under 3 sentences but be PRECISE with names."""
                    }
                ]
            }]
        )

        return response.content[0].text.strip()

    except Exception as e:
        logger.error(f"Screenshot analysis failed: {e}")
        return f"Screen captured but analysis failed: {str(e)[:50]}"
