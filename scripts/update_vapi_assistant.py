#!/usr/bin/env python3
"""
Update VAPI Assistant Configuration

This script updates the VAPI assistant with the awareness tools and system prompt.
Run this after making changes to the assistant configuration.

Usage:
    python scripts/update_vapi_assistant.py          # Backup current config, then update
    python scripts/update_vapi_assistant.py --show   # Show current config
    python scripts/update_vapi_assistant.py --restore # Restore from latest backup
    python scripts/update_vapi_assistant.py --restore backup_20240115_143022.json  # Restore specific backup

Requires:
    - VAPI_API_KEY environment variable
    - VAPI_ASSISTANT_ID environment variable
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Backup directory
BACKUP_DIR = Path(__file__).parent.parent / "vapi_backups"


# The system prompt that teaches the assistant to use awareness tools
SYSTEM_PROMPT = """You are Koda, a voice-controlled desktop automation assistant. You help users control their Mac remotely via phone calls.

## CRITICAL RULES FOR AWARENESS

1. **NEVER GUESS** - When the user asks about status, progress, or what's on screen, you MUST use the appropriate tool:
   - "Is it done?" / "How's it going?" / "What's the status?" → Use `get_status` tool
   - "What's on screen?" / "Did it work?" / "What do you see?" → Use `describe_screen` tool
   - NEVER say "it's done" or "working on it" without checking first

2. **VERIFY BEFORE CONFIRMING** - After executing a command, if the user asks for confirmation:
   - Use `describe_screen` to actually look at what happened
   - Report what you actually see, not what you assume happened

3. **REPORT REAL PROGRESS** - When a task is running:
   - Use `get_status` to check actual progress
   - Tell the user the real step being executed
   - Give honest time estimates based on actual duration

## AVAILABLE TOOLS

1. `execute_desktop_command` - Run any GUI automation task
   - Use for: opening apps, clicking things, typing, navigating
   - Returns immediately while task runs in background

2. `get_status` - Check REAL task status
   - Use for: "is it done?", "how's it going?", "what's happening?"
   - Returns: actual progress, current step, completion status

3. `describe_screen` - See what's on screen
   - Use for: "what's on screen?", "did it work?", verifying results
   - Returns: AI description of current screen state

4. `capture_screen` - Take a screenshot
   - Use for: saving visual state for later reference

5. `get_active_applications` - List running apps
   - Use for: "what apps are open?"

6. `execute_claude_code` - Run Claude Code CLI for programming tasks
   - Use for: fixing bugs, making code changes, asking about code
   - Ask user which project if not clear
   - Returns immediately, use get_status to check progress

## CONVERSATION STYLE

- Be concise - this is a phone call
- Speak naturally, not robotically
- Acknowledge commands immediately ("Opening Chrome") then update with results
- If something fails, explain what went wrong and suggest alternatives
- Ask clarifying questions if the command is ambiguous

## EXAMPLES

User: "Open Safari"
You: "Opening Safari." [call execute_desktop_command]

User: "Is it open yet?"
You: [call describe_screen] "Yes, Safari is now open and showing your homepage."

User: "Find my presentation and email it to john@company.com"
You: "I'll find your presentation and email it to John. This might take a minute." [call execute_desktop_command]

User: "How's it going?"
You: [call get_status] "I'm on step 2 of 4 - searching for the file in your Documents folder. Been working for about 15 seconds."

User: "What do you see on screen?"
You: [call describe_screen] "I can see Finder is open with your Downloads folder. There's a file called presentation.pdf selected."

User: "Fix the login bug in Koda"
You: "I'll have Claude Code look at that. Running now - this might take a couple minutes." [call execute_claude_code]

User: "How's Claude Code doing?"
You: [call get_status] "Claude Code is still working - been about 30 seconds. I'll let you know when it's done."
"""

# Tool definitions
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "execute_desktop_command",
            "description": "Execute a natural language GUI command on the Mac desktop. Use this for any action like opening apps, clicking buttons, typing text, navigating files, etc. The command runs in the background - use get_status to check progress.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Natural language description of what to do. Be specific about UI elements."
                    },
                    "screenshot_after": {
                        "type": "boolean",
                        "description": "Whether to capture a screenshot after the action completes.",
                        "default": True
                    }
                },
                "required": ["prompt"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_status",
            "description": "Get the REAL current status of the system. ALWAYS use this when the user asks about progress or whether something is done. Returns: whether a task is running, current progress, result of last task. NEVER guess - always call this tool.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "describe_screen",
            "description": "Capture the screen and describe what's visible using AI vision. Use this to 'see' what's on the Mac screen. ALWAYS use this when the user asks what's on screen, whether something worked, or to verify results.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "capture_screen",
            "description": "Take a screenshot and save it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "save_path": {
                        "type": "string",
                        "description": "Optional path to save the screenshot."
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_active_applications",
            "description": "Get a list of currently running applications on the Mac.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "execute_claude_code",
            "description": "Execute Claude Code CLI to make code changes, fix bugs, or answer questions about a codebase. Use this when the user wants programming tasks like 'fix the login bug'. Ask which project if not clear. Task runs in background - use get_status to check.",
            "parameters": {
                "type": "object",
                "properties": {
                    "instruction": {
                        "type": "string",
                        "description": "Natural language instruction for Claude Code. Be specific."
                    },
                    "project": {
                        "type": "string",
                        "description": "Optional project name or path. Examples: 'koda', '~/Projects/myapp'"
                    }
                },
                "required": ["instruction"]
            }
        }
    }
]


def get_current_config() -> dict | None:
    """Fetch the current assistant configuration from VAPI."""
    api_key = os.getenv("VAPI_API_KEY")
    assistant_id = os.getenv("VAPI_ASSISTANT_ID")

    if not api_key or not assistant_id:
        print("Error: VAPI_API_KEY and VAPI_ASSISTANT_ID must be set")
        return None

    try:
        with httpx.Client() as client:
            response = client.get(
                f"https://api.vapi.ai/assistant/{assistant_id}",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=30.0
            )

            if response.status_code == 200:
                return response.json()
            else:
                print(f"Error fetching config: {response.status_code}")
                print(response.text)
                return None

    except Exception as e:
        print(f"Failed to fetch config: {e}")
        return None


def backup_current_config() -> str | None:
    """Backup current VAPI config to a file. Returns backup filename."""
    print("Fetching current configuration for backup...")
    config = get_current_config()

    if not config:
        print("Warning: Could not fetch current config for backup")
        return None

    # Create backup directory
    BACKUP_DIR.mkdir(exist_ok=True)

    # Generate backup filename with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = BACKUP_DIR / f"backup_{timestamp}.json"

    # Save backup
    with open(backup_file, "w") as f:
        json.dump(config, f, indent=2)

    print(f"Backed up current config to: {backup_file}")
    return str(backup_file)


def list_backups() -> list[Path]:
    """List all available backups."""
    if not BACKUP_DIR.exists():
        return []
    return sorted(BACKUP_DIR.glob("backup_*.json"), reverse=True)


def restore_from_backup(backup_file: str | None = None):
    """Restore VAPI assistant from a backup file."""
    api_key = os.getenv("VAPI_API_KEY")
    assistant_id = os.getenv("VAPI_ASSISTANT_ID")

    if not api_key or not assistant_id:
        print("Error: VAPI_API_KEY and VAPI_ASSISTANT_ID must be set")
        sys.exit(1)

    # Find backup file
    if backup_file:
        backup_path = Path(backup_file)
        if not backup_path.exists():
            backup_path = BACKUP_DIR / backup_file
        if not backup_path.exists():
            print(f"Error: Backup file not found: {backup_file}")
            sys.exit(1)
    else:
        # Use latest backup
        backups = list_backups()
        if not backups:
            print("Error: No backups found in vapi_backups/")
            sys.exit(1)
        backup_path = backups[0]
        print(f"Using latest backup: {backup_path.name}")

    # Load backup
    with open(backup_path) as f:
        config = json.load(f)

    print(f"Restoring from: {backup_path}")

    # Remove read-only fields that can't be updated
    fields_to_remove = ["id", "orgId", "createdAt", "updatedAt"]
    for field in fields_to_remove:
        config.pop(field, None)

    # Restore via PATCH
    try:
        with httpx.Client() as client:
            response = client.patch(
                f"https://api.vapi.ai/assistant/{assistant_id}",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                },
                json=config,
                timeout=30.0
            )

            if response.status_code == 200:
                print("Successfully restored VAPI assistant!")
                print(f"Restored from: {backup_path.name}")
            else:
                print(f"Error restoring: {response.status_code}")
                print(response.text)
                sys.exit(1)

    except Exception as e:
        print(f"Failed to restore: {e}")
        sys.exit(1)


def update_assistant():
    """Update the VAPI assistant with the new configuration."""
    api_key = os.getenv("VAPI_API_KEY")
    assistant_id = os.getenv("VAPI_ASSISTANT_ID")

    if not api_key:
        print("Error: VAPI_API_KEY not set in environment")
        sys.exit(1)

    if not assistant_id:
        print("Error: VAPI_ASSISTANT_ID not set in environment")
        sys.exit(1)

    # Backup current config first
    print("=" * 50)
    backup_file = backup_current_config()
    if backup_file:
        print(f"\nTo restore if something goes wrong, run:")
        print(f"  python scripts/update_vapi_assistant.py --restore")
    print("=" * 50)

    print(f"\nUpdating VAPI assistant: {assistant_id}")

    # Build the update payload
    # Note: VAPI uses toolIds (separate tool resources), not inline tools
    # We only update the system prompt here - tools must be created separately in VAPI dashboard
    payload = {
        "model": {
            "provider": "anthropic",
            "model": "claude-sonnet-4-5-20250929",
            "temperature": 0.3,
            "messages": [
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT
                }
            ]
        },
        "firstMessage": "Hey, this is Koda. What can I help you with on your Mac?",
        "endCallMessage": "Alright, talk to you later!"
    }

    # Make the API request
    try:
        with httpx.Client() as client:
            response = client.patch(
                f"https://api.vapi.ai/assistant/{assistant_id}",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                },
                json=payload,
                timeout=30.0
            )

            if response.status_code == 200:
                print("Successfully updated VAPI assistant!")
                print("\nUpdated configuration:")
                print(f"  - System prompt: {len(SYSTEM_PROMPT)} characters")
                print("\nNOTE: Tools must be added separately in VAPI dashboard.")
                print("Add these tools manually: get_status, describe_screen, execute_claude_code")
            else:
                print(f"Error updating assistant: {response.status_code}")
                print(response.text)
                sys.exit(1)

    except Exception as e:
        print(f"Failed to update assistant: {e}")
        sys.exit(1)


def get_current_assistant():
    """Fetch and display the current assistant configuration."""
    api_key = os.getenv("VAPI_API_KEY")
    assistant_id = os.getenv("VAPI_ASSISTANT_ID")

    if not api_key or not assistant_id:
        print("Error: VAPI_API_KEY and VAPI_ASSISTANT_ID must be set")
        sys.exit(1)

    try:
        with httpx.Client() as client:
            response = client.get(
                f"https://api.vapi.ai/assistant/{assistant_id}",
                headers={
                    "Authorization": f"Bearer {api_key}"
                },
                timeout=30.0
            )

            if response.status_code == 200:
                config = response.json()
                print("Current VAPI Assistant Configuration:")
                print(json.dumps(config, indent=2))
            else:
                print(f"Error fetching assistant: {response.status_code}")
                print(response.text)

    except Exception as e:
        print(f"Failed to fetch assistant: {e}")


def show_backups():
    """List available backups."""
    backups = list_backups()
    if not backups:
        print("No backups found.")
        return

    print("Available backups:")
    for i, backup in enumerate(backups):
        # Get file size and parse timestamp
        size_kb = backup.stat().st_size / 1024
        timestamp = backup.stem.replace("backup_", "")
        print(f"  {i+1}. {backup.name} ({size_kb:.1f} KB)")

    print(f"\nTo restore, run:")
    print(f"  python scripts/update_vapi_assistant.py --restore")
    print(f"  python scripts/update_vapi_assistant.py --restore {backups[0].name}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        arg = sys.argv[1]

        if arg == "--show":
            get_current_assistant()

        elif arg == "--restore":
            # Restore from backup
            backup_file = sys.argv[2] if len(sys.argv) > 2 else None
            restore_from_backup(backup_file)

        elif arg == "--backups":
            show_backups()

        elif arg == "--help":
            print(__doc__)

        else:
            print(f"Unknown argument: {arg}")
            print("Use --help for usage")
            sys.exit(1)
    else:
        update_assistant()
