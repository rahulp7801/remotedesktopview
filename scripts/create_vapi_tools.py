#!/usr/bin/env python3
"""
Create VAPI Tools for Koda

This script creates the awareness tools (get_status, describe_screen, execute_claude_code)
in VAPI and adds them to the assistant.

Usage:
    python scripts/create_vapi_tools.py          # Create tools and add to assistant
    python scripts/create_vapi_tools.py --list   # List existing tools

Requires:
    - VAPI_API_KEY environment variable
    - VAPI_ASSISTANT_ID environment variable
"""

import json
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()

VAPI_API_BASE = "https://api.vapi.ai"


def get_server_url() -> str:
    """Get the server URL from the assistant config."""
    assistant_id = os.getenv("VAPI_ASSISTANT_ID")
    api_key = os.getenv("VAPI_API_KEY")

    if not assistant_id or not api_key:
        # Fallback to a placeholder
        return "https://your-ngrok-url.ngrok-free.dev/vapi/webhook"

    with httpx.Client() as client:
        response = client.get(
            f"{VAPI_API_BASE}/assistant/{assistant_id}",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30.0
        )
        if response.status_code == 200:
            assistant = response.json()
            return assistant.get("server", {}).get("url", "")

    return ""


def get_tools_to_create(server_url: str) -> list[dict]:
    """Get tool definitions with the correct server URL."""
    return [
        {
            "type": "function",
            "function": {
                "name": "get_status",
                "description": "Get the REAL current status of the system. ALWAYS use this when the user asks about progress or whether something is done. Returns: whether a task is running, current progress, result of last task. NEVER guess - always call this tool to check actual state.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            },
            "server": {
                "url": server_url,
                "timeoutSeconds": 20
            },
            "async": False,  # This is synchronous - we want the actual status
            "messages": [{"type": "request-start", "blocking": False}]
        },
        {
            "type": "function",
            "function": {
                "name": "describe_screen",
                "description": "Capture the screen and describe what's visible using AI vision. Use this to 'see' what's on the Mac screen. ALWAYS use this when the user asks what's on screen, whether something worked, or to verify results. Returns a description of the current screen state.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            },
            "server": {
                "url": server_url,
                "timeoutSeconds": 30
            },
            "async": False,  # This is synchronous - we want to see the result
            "messages": [{"type": "request-start", "blocking": False}]
        },
        {
            "type": "function",
            "function": {
                "name": "execute_claude_code",
                "description": "Execute Claude Code CLI to make code changes, fix bugs, or answer questions about a codebase. Use this when the user wants programming tasks like 'fix the login bug' or 'add error handling'. Ask which project if not clear. Task runs in background - use get_status to check progress.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "instruction": {
                            "type": "string",
                            "description": "Natural language instruction for Claude Code. Be specific. Examples: 'Fix the login bug', 'Add input validation', 'How does authentication work?'"
                        },
                        "project": {
                            "type": "string",
                            "description": "Optional project name or path. Examples: 'koda', 'remotedesktopview', '~/Projects/myapp'. If not provided, will try to find a matching project."
                        }
                    },
                    "required": ["instruction"]
                }
            },
            "server": {
                "url": server_url,
                "timeoutSeconds": 20
            },
            "async": True,  # This is async - runs in background
            "messages": [{"type": "request-start", "blocking": False}]
        }
    ]


def get_headers():
    """Get API headers."""
    api_key = os.getenv("VAPI_API_KEY")
    if not api_key:
        print("Error: VAPI_API_KEY not set")
        sys.exit(1)
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }


def list_tools():
    """List all tools in the account."""
    print("Fetching existing tools...")

    with httpx.Client() as client:
        response = client.get(
            f"{VAPI_API_BASE}/tool",
            headers=get_headers(),
            timeout=30.0
        )

        if response.status_code == 200:
            tools = response.json()
            print(f"\nFound {len(tools)} tools:\n")
            for tool in tools:
                name = tool.get("function", {}).get("name", "unnamed")
                tool_id = tool.get("id", "no-id")
                print(f"  - {name}: {tool_id}")
            return tools
        else:
            print(f"Error listing tools: {response.status_code}")
            print(response.text)
            return []


def get_tool_by_name(name: str) -> dict | None:
    """Find a tool by name."""
    with httpx.Client() as client:
        response = client.get(
            f"{VAPI_API_BASE}/tool",
            headers=get_headers(),
            timeout=30.0
        )

        if response.status_code == 200:
            tools = response.json()
            for tool in tools:
                if tool.get("function", {}).get("name") == name:
                    return tool
    return None


def create_tool(tool_def: dict) -> str | None:
    """Create a tool and return its ID."""
    name = tool_def["function"]["name"]

    # Check if tool already exists
    existing = get_tool_by_name(name)
    if existing:
        print(f"  Tool '{name}' already exists (ID: {existing['id']})")
        return existing["id"]

    print(f"  Creating tool '{name}'...")

    with httpx.Client() as client:
        response = client.post(
            f"{VAPI_API_BASE}/tool",
            headers=get_headers(),
            json=tool_def,
            timeout=30.0
        )

        if response.status_code in (200, 201):
            tool = response.json()
            tool_id = tool.get("id")
            print(f"  Created '{name}' with ID: {tool_id}")
            return tool_id
        else:
            print(f"  Error creating '{name}': {response.status_code}")
            print(f"  {response.text}")
            return None


def get_assistant_tool_ids() -> list[str]:
    """Get current tool IDs from the assistant."""
    assistant_id = os.getenv("VAPI_ASSISTANT_ID")
    if not assistant_id:
        print("Error: VAPI_ASSISTANT_ID not set")
        sys.exit(1)

    with httpx.Client() as client:
        response = client.get(
            f"{VAPI_API_BASE}/assistant/{assistant_id}",
            headers=get_headers(),
            timeout=30.0
        )

        if response.status_code == 200:
            assistant = response.json()
            return assistant.get("model", {}).get("toolIds", [])
        else:
            print(f"Error fetching assistant: {response.status_code}")
            return []


def update_assistant_tools(tool_ids: list[str]):
    """Update the assistant with new tool IDs."""
    assistant_id = os.getenv("VAPI_ASSISTANT_ID")
    if not assistant_id:
        print("Error: VAPI_ASSISTANT_ID not set")
        sys.exit(1)

    print(f"\nUpdating assistant with {len(tool_ids)} tools...")

    # First, get the current model config to preserve other settings
    with httpx.Client() as client:
        response = client.get(
            f"{VAPI_API_BASE}/assistant/{assistant_id}",
            headers=get_headers(),
            timeout=30.0
        )

        if response.status_code != 200:
            print(f"Error fetching assistant: {response.status_code}")
            return False

        assistant = response.json()
        current_model = assistant.get("model", {})

        # Update toolIds while preserving other model settings
        current_model["toolIds"] = tool_ids

        payload = {
            "model": current_model
        }

        response = client.patch(
            f"{VAPI_API_BASE}/assistant/{assistant_id}",
            headers=get_headers(),
            json=payload,
            timeout=30.0
        )

        if response.status_code == 200:
            print("Successfully updated assistant tools!")
            return True
        else:
            print(f"Error updating assistant: {response.status_code}")
            print(response.text)
            return False


def create_all_tools():
    """Create all tools and add them to the assistant."""
    print("=" * 50)
    print("Creating VAPI Tools for Koda")
    print("=" * 50)

    # Get server URL from assistant
    print("\nFetching server URL from assistant config...")
    server_url = get_server_url()
    if not server_url:
        print("Error: Could not get server URL from assistant")
        print("Make sure VAPI_ASSISTANT_ID is set and assistant has a server URL configured")
        sys.exit(1)
    print(f"Using server URL: {server_url}")

    # Get tool definitions with the server URL
    tools_to_create = get_tools_to_create(server_url)

    # Get existing tool IDs from assistant
    existing_tool_ids = get_assistant_tool_ids()
    print(f"\nAssistant currently has {len(existing_tool_ids)} tools")

    # Create new tools
    print("\nCreating awareness tools:")
    new_tool_ids = []

    for tool_def in tools_to_create:
        tool_id = create_tool(tool_def)
        if tool_id:
            new_tool_ids.append(tool_id)

    if not new_tool_ids:
        print("\nNo new tools created.")
        return

    # Combine existing and new tool IDs (avoid duplicates)
    all_tool_ids = list(set(existing_tool_ids + new_tool_ids))

    print(f"\nTotal tools to assign: {len(all_tool_ids)}")
    print(f"  Existing: {len(existing_tool_ids)}")
    print(f"  New: {len(new_tool_ids)}")

    # Update assistant
    if update_assistant_tools(all_tool_ids):
        print("\n" + "=" * 50)
        print("Done! The assistant now has these new tools:")
        for tool_def in tools_to_create:
            print(f"  - {tool_def['function']['name']}")
        print("=" * 50)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--list":
        list_tools()
    else:
        create_all_tools()
