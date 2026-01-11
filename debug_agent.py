#!/usr/bin/env python3
"""Debug script to understand why Agent-S planner returns empty subtasks."""

import os
import sys

# Load .env file manually
env_path = os.path.join(os.path.dirname(__file__), '.env')
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ[key] = value

from gui_agents.core.AgentS import GraphSearchAgent
from gui_agents.aci.MacOSACI import MacOSACI, UIElement
from ApplicationServices import AXUIElementCreateSystemWide
import subprocess
import tempfile
import logging

# Enable logging
logging.basicConfig(level=logging.INFO, format='%(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

print("=" * 60)
print("AGENT-S DEBUG SCRIPT")
print("=" * 60)
print()

# Check API keys
openai_key = os.getenv('OPENAI_API_KEY', '')
anthropic_key = os.getenv('ANTHROPIC_API_KEY', '')
print(f"OPENAI_API_KEY: {'SET' if openai_key else 'NOT SET'}")
print(f"ANTHROPIC_API_KEY: {'SET' if anthropic_key else 'NOT SET'}")
print()

if not anthropic_key:
    print("ERROR: ANTHROPIC_API_KEY not set!")
    sys.exit(1)

print("=== Initializing Agent ===")

# Initialize
grounding_agent = MacOSACI(top_app_only=True, ocr=False)

engine_params = {
    "engine_type": "anthropic",
    "model": "claude-sonnet-4-5-20250929",
    "max_tokens": 2000,
    "temperature": 0.1,
    "api_key": anthropic_key,
}

agent = GraphSearchAgent(
    engine_params=engine_params,
    grounding_agent=grounding_agent,
    platform="macos",
    action_space="aci",
    observation_type="a11y_tree",
)

print("Agent initialized!")
print(f"  Platform: {agent.platform}")
print(f"  Action space: {agent.action_space}")
print(f"  Observation type: {agent.observation_type}")
print()

# Create observation
print("=== Creating Observation ===")
ax_system = AXUIElementCreateSystemWide()

# Capture screenshot
with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp_file:
    tmp_path = tmp_file.name
subprocess.run(["screencapture", "-x", tmp_path], capture_output=True, timeout=5)
with open(tmp_path, 'rb') as f:
    screenshot_bytes = f.read()
os.unlink(tmp_path)

observation = {
    "accessibility_tree": UIElement(ax_system),
    "screenshot": screenshot_bytes
}
print(f"Screenshot size: {len(screenshot_bytes)} bytes")
print(f"Accessibility tree type: {type(observation['accessibility_tree'])}")
print()

# Try predict() directly
print("=== Testing predict() ===")
try:
    # Reset agent state first
    agent.reset()
    
    print(f"Agent state before predict:")
    print(f"  requires_replan: {agent.requires_replan}")
    print(f"  needs_next_subtask: {agent.needs_next_subtask}")
    print(f"  subtasks: {agent.subtasks}")
    print(f"  should_send_action: {agent.should_send_action}")
    print()
    
    print("Calling agent.predict('Open Safari', observation)...")
    print()
    
    info, actions = agent.predict("Type hello world", observation)
    
    print()
    print("=== PREDICT RESULT ===")
    print(f"Actions: {actions}")
    print(f"Info keys: {list(info.keys()) if info else 'None'}")
    
    if info:
        important_keys = ['subtask', 'subtask_info', 'subtask_status', 'executor_plan', 'plan_code', 'reflection']
        for key in important_keys:
            if key in info:
                value = info[key]
                if isinstance(value, str) and len(value) > 200:
                    print(f"  {key}: {value[:200]}...")
                else:
                    print(f"  {key}: {value}")
                
except IndexError as e:
    print()
    print("=== INDEX ERROR ===")
    print(f"Error: {e}")
    print(f"This means subtasks.pop(0) failed - empty subtask list")
    print()
    print(f"Agent state after failure:")
    print(f"  requires_replan: {agent.requires_replan}")
    print(f"  subtasks: {agent.subtasks}")
    print(f"  needs_next_subtask: {agent.needs_next_subtask}")
    
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {e}")
    import traceback
    traceback.print_exc()

print()
print("=" * 60)
print("DEBUG COMPLETE")
print("=" * 60)
