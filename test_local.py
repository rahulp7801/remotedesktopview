#!/usr/bin/env python3
"""Quick local test for the desktop command system."""

import asyncio
import sys
sys.path.insert(0, '.')

from brain.tools.desktop_command import execute_desktop_command

async def test():
    print("\n" + "="*60)
    print("TEST: Search for confusion matrix png in Downloads folder")
    print("="*60 + "\n")
    
    result = await execute_desktop_command(
        "Search for confusion matrix png in Downloads folder",
        screenshot_after=False
    )
    
    print("\n" + "="*60)
    print("RESULT:")
    print(f"  Status: {result.get('status')}")
    print(f"  Message: {result.get('message')}")
    print(f"  Steps: {result.get('steps_executed')}")
    print("="*60)

if __name__ == "__main__":
    asyncio.run(test())
