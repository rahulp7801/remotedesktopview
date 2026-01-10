# Voice-Controlled Remote Desktop Agent

## Hackathon Challenge

**UCSB Hackathon Challenge: Make Your App Talk Back (Composite Agent Edition)**

Build a real-time voice experience that goes beyond buttons and text. For this challenge, you will build a composite voice agent by combining Deepgram streaming speech recognition (Flux) with your own orchestration logic, an LLM, and autonomous tools. The goal is to create an application that can listen, reason, and act in real time.

This challenge rewards teams that design voice experiences that feel fast, useful, and accessible.

### Core Requirements

Your project must:
1. ✅ Use Deepgram streaming STT via Flux
2. ✅ Use an LLM with function calling (Claude via MCP)
3. ✅ Implement at least two real functions that perform meaningful actions (we use Agent-S for desktop GUI automation + screenshot capture)

### Bonus Points

Additional points may be awarded for incorporating:
- [ ] Deepgram TTS for spoken responses
- [ ] Eager End-of-Turn (eager EOT) to begin reasoning before the user fully finishes speaking
- [ ] Accessibility features:
  - [ ] Live captions or readable transcript UI
  - [ ] Voice commands like "repeat," "slow down," or "summarize"
  - [ ] Screen-reader-friendly or keyboard-only interfaces
  - [ ] Multilingual support
  - [ ] Noise-robust or push-to-talk interaction modes

### Judging Criteria

Projects evaluated on:
- **Autonomy & usefulness (40%)** - How effectively does the agent use tools to complete tasks?
- **Real-time user experience (30%)** - Latency, turn-taking, interruptions, and responsiveness
- **Engineering quality (20%)** - Architecture, reliability, and error handling
- **Accessibility impact (10%)** - How well does the project expand access and usability?

### Submission Requirements

- [ ] A short demo video (30–90 seconds)
- [ ] A GitHub repository with setup instructions
- [ ] A brief description of the functions/tools used and what they enable

---

## What This App Does

This is a voice-controlled agent that lets you call your Mac from your phone and control it naturally through speech. Say "open my email" or "find that document from last week" and the agent will navigate your desktop, click through interfaces, and complete tasks—all while you're away from your computer.

**Why we built this:** Traditional remote desktop tools require you to manually navigate UIs on a tiny phone screen. This agent uses speech recognition, AI reasoning, and GUI automation to handle complex desktop tasks through simple voice commands.

**Core workflow:**
1. You speak a command into your phone
2. Deepgram transcribes it in real-time
3. Claude reasons about the steps needed
4. Agent-S executes GUI actions on your Mac
5. You get visual/audio confirmation

---

## Development Setup

### Prerequisites

**System Requirements:**
- macOS 12+ 
- Python 3.10+
- System Permissions (see below)

**Required System Permissions** (System Settings > Privacy & Security):
- ✅ **Accessibility** - Enable for Terminal and Python
- ✅ **Screen Recording** - Enable for Terminal and Python  
- ✅ **Full Disk Access** - Enable for Terminal

**Audio Routing:**
- Install [BlackHole 2ch](https://existential.audio/blackhole/) virtual audio driver
- Configure Multi-Output Device in Audio MIDI Setup
- Set System Input to "BlackHole 2ch"

### Installation
```bash
# Install core dependencies
pip install deepgram-sdk gui-agents mcp fastapi uvicorn websockets

# Install optional dev tools
pip install pytest black pylint
```

### Running the App
```bash
# Start the backend server (development mode with auto-reload)
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# Test Agent-S GUI control
python -m gui_agents.test_aci

# Test Deepgram connection
python scripts/test_deepgram.py

# Run full integration test
pytest tests/test_integration.py -v
```

### Adding New Packages
```bash
# Add to requirements
pip install <package-name>
pip freeze > requirements.txt

# For MCP tools, update mcp_server.py tool definitions
```

---

## MCP Server: Agent-S

### Overview

We use a single MCP server that wraps **Agent-S**, our GUI execution engine. Agent-S uses hierarchical planning and vision-based element detection (MacOSACI) to interact with macOS interfaces. Through MCP, Claude can orchestrate Agent-S to execute complex desktop automation tasks.

**Why Agent-S via MCP:** Instead of Claude generating raw Python code or hardcoded GUI coordinates, it reasons about high-level actions ("click the Chrome icon in the Dock") which the MCP server translates into Agent-S's vision-based GUI execution. Agent-S finds UI elements visually, making automation robust to different screen sizes, themes, and UI changes.

### How Agent-S MCP Works
```
Voice Input → Deepgram → Transcript
                          ↓
                    Claude (MCP Client)
                          ↓
              execute_desktop_command
                          ↓
                     MCP Server
                          ↓
                    Agent-S Engine
                          ↓
        ┌──────────────┴──────────────┐
        ↓                              ↓
Hierarchical Planning          MacOSACI Driver
        ↓                              ↓
   Break into steps         Vision-based detection
        ↓                              ↓
        └──────────────┬──────────────┘
                       ↓
           Actual GUI Actions (clicks, types, scrolls)
```

### MCP Tools Exposed

The MCP server (`mcp_server.py`) exposes these tools to Claude:

#### 1. `execute_desktop_command`
```python
{
    "name": "execute_desktop_command",
    "description": "Execute a natural language command on the macOS desktop using Agent-S",
    "parameters": {
        "prompt": "string - Natural language description of the GUI task",
        "screenshot_before": "boolean - Capture screen before action",
        "screenshot_after": "boolean - Capture screen after action"
    }
}
```

**When to use:** For any GUI automation task that can be expressed in natural language.

**Example calls:**
- `execute_desktop_command(prompt="Open Chrome and navigate to gmail.com")`
- `execute_desktop_command(prompt="Click the 'New Message' button", screenshot_after=True)`
- `execute_desktop_command(prompt="Find the Downloads folder in Finder and open it")`

#### 2. `capture_screen`
```python
{
    "name": "capture_screen",
    "description": "Take a screenshot of the current desktop state",
    "parameters": {
        "save_path": "string - Optional path to save screenshot"
    }
}
```

**When to use:** Before/after actions for verification, or when Claude needs visual context to plan next steps.

#### 3. `get_active_applications`
```python
{
    "name": "get_active_applications", 
    "description": "List all currently running applications",
    "parameters": {}
}
```

**When to use:** To check if required applications are already open before launching them, avoiding duplicate windows.

### Agent-S Integration Details

**Agent-S** uses two key components:

1. **Hierarchical Planning** - Breaks complex tasks ("send an email to John") into subtasks ("open Mail.app" → "click New Message" → "type recipient" → etc.)

2. **MacOSACI Driver** - Vision-based GUI element detection that finds buttons, text fields, and other UI elements using screenshot analysis

**Key Agent-S methods:**
```python
# Initialize Agent-S
from gui_agents import Agent

agent = Agent(
    platform="macos",
    use_screen_parsing=True,
    headless=False
)

# Execute command
result = agent.run("Click the Downloads folder in Finder")

# Result structure:
# {
#     "success": bool,
#     "screenshots": List[str],  # Paths to captured screenshots
#     "error_message": Optional[str],
#     "steps_executed": List[str]  # What Agent-S actually did
# }
```

### Agent-S Usage Guidelines

**✅ DO:**
- Always let Agent-S handle UI navigation—don't write raw `pyautogui` or `applescript` code
- Use descriptive prompts: "Click the blue 'Send' button in the Gmail compose window" vs "click send"
- Capture screenshots after critical actions for verification
- Break complex multi-step tasks into discrete commands
- Handle Agent-S failures gracefully—UI elements may not always be found

**❌ DON'T:**
- Don't bypass Agent-S for "simple" clicks—its vision model is more reliable than hardcoded coordinates
- Don't chain too many unrelated actions in one prompt (e.g., "open Chrome, check email, download file, and close window")
- Don't assume element positions or screen layout—let Agent-S find them visually
- Don't retry the exact same prompt if it fails—rephrase or add more context

### Error Handling Pattern
```python
# In mcp_server.py
async def execute_desktop_command(prompt: str, screenshot_after: bool = True):
    try:
        # Execute via Agent-S
        result = agent.run(prompt)
        
        if result.success:
            response = {
                "status": "success",
                "message": f"Completed: {prompt}",
                "steps": result.steps_executed
            }
            
            # Capture confirmation screenshot
            if screenshot_after:
                screenshot_path = capture_screen(f"cache/action_{timestamp}.png")
                response["screenshot"] = screenshot_path
            
            return response
            
        else:
            # Agent-S couldn't complete the task
            logger.warning(f"Agent-S failed: {result.error_message}")
            return {
                "status": "failed", 
                "error": result.error_message,
                "suggestion": "Try rephrasing with more specific UI element descriptions"
            }
            
    except Exception as e:
        logger.error(f"MCP tool error: {e}")
        return {
            "status": "error",
            "error": str(e),
            "suggestion": "Check logs/session.log for details"
        }
```

### Common Agent-S Patterns

**Opening applications:**
```python
# Good: Specific and clear
execute_desktop_command("Open Google Chrome from the Applications folder")

# Better: Let Agent-S find it visually
execute_desktop_command("Click the Chrome icon in the Dock")
```

**Navigating UI:**
```python
# Good: Describes visual appearance
execute_desktop_command("Click the gear icon in the top-right corner of Settings")

# Better: Includes context
execute_desktop_command("In System Settings, click the 'Privacy & Security' icon in the sidebar")
```

**Handling text input:**
```python
# Good: Clear field identification
execute_desktop_command("Type 'hello@example.com' into the email address field")

# Better: Includes visual context
execute_desktop_command("In the Gmail compose window, type 'hello@example.com' in the 'To' field")
```

---

## Project Structure
```
voice-desktop-agent/
├── main.py                 # FastAPI server, WebSocket handler
├── mcp_server.py          # MCP tool definitions, Agent-S integration
├── agent_logic.py         # Agent-S initialization, MacOSACI setup
├── audio_handler.py       # BlackHole → Deepgram audio pipeline
├── scripts/
│   ├── test_deepgram.py  # Test Deepgram connection
│   └── test_agent.py     # Test Agent-S execution
├── logs/
│   └── session.log       # All transcriptions and actions
├── cache/
│   └── latest_action.png # Most recent screenshot
└── requirements.txt
```

---

## Key Implementation Details

### Latency Management

**Problem:** High-latency voice recognition can cause lag between speech and action.

**Solution:**
```python
# In audio_handler.py
deepgram_config = {
    "interim_results": False,  # Only send final transcripts
    "punctuate": True,
    "smart_format": True,
    "endpointing": 500  # Wait 500ms before finalizing
}
```

This prevents triggering Agent-S on incomplete sentences like "open Chro—" instead of "open Chrome".

### Visual Feedback Loop

After every successful GUI action:
```python
# Capture confirmation
screencapture_path = f"cache/action_{timestamp}.png"
subprocess.run(["screencapture", "-x", screencapture_path])

# Optional: Send back to phone for visual confirmation
await websocket.send_json({
    "type": "action_complete",
    "screenshot": encode_image(screencapture_path)
})
```

### Emergency Kill Switch

If Agent-S gets stuck in a loop:
```bash
# Press in terminal
CMD + SHIFT + ALT + K
```

This triggers immediate process termination without waiting for current action to complete.

---

## Coding Standards

### Error Handling
```python
# Always wrap GUI actions
try:
    result = agent.run(command)
    if not result.success:
        logger.warning(f"Agent-S couldn't complete task: {result.error_message}")
        # Retry with more specific prompt or fallback action
except Exception as e:
    logger.error(f"Unexpected error: {e}")
    # Send error notification to phone
    await notify_phone({"error": str(e)})
```

### Logging
```python
# In all modules
import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Log format: timestamp | level | module | message
logging.basicConfig(
    format='%(asctime)s | %(levelname)s | %(name)s | %(message)s',
    handlers=[
        logging.FileHandler('logs/session.log'),
        logging.StreamHandler()
    ]
)

# Log all transcriptions and actions
logger.info(f"Transcription: {transcript}")
logger.info(f"Executing: {command}")
logger.info(f"Agent-S result: {result.status}")
```

### Naming Conventions
- Use descriptive file names: `email_automation.py` not `script1.py`
- Tool names should be verb phrases: `execute_desktop_command` not `desktop_tool`
- Variables should indicate type: `screenshot_path` not `ss`, `agent_result` not `res`

---

## Troubleshooting

### Microphone Not Working
**Symptom:** Deepgram receives no audio data

**Fix:**
1. Check System Input is set to "BlackHole 2ch" in Sound Settings
2. Verify Multi-Output Device is configured in Audio MIDI Setup
3. Test with: `python scripts/test_deepgram.py`
4. Check BlackHole is installed: `ls /Library/Audio/Plug-Ins/HAL/`

### Agent-S Can't Find UI Elements
**Symptom:** "UI element not found" errors

**Fix:**
1. Make prompts more visually descriptive ("the blue 'Send' button" vs "send button")
2. Ensure target application is in focus and visible
3. Check screen resolution matches Agent-S training (works best at standard resolutions)
4. Capture screenshot first to see what Agent-S sees: `capture_screen()`

### Agent-S Stuck/Looping
**Symptom:** Same action repeats indefinitely

**Fix:**
1. Press `CMD + SHIFT + ALT + K` in terminal for emergency stop
2. Check if UI element description is too ambiguous
3. Add explicit stopping conditions in prompts
4. Review `logs/session.log` to see what Agent-S is detecting

### Permissions Errors
**Symptom:** "Operation not permitted" when Agent-S tries to click

**Fix:**
1. Verify Accessibility is enabled for both Terminal AND Python in System Settings
2. Restart Terminal after granting permissions
3. Test permissions with: `python -m gui_agents.test_aci`
4. If using virtual environment, grant permissions to the Python binary in venv

### WebSocket Disconnects
**Symptom:** Phone loses connection to Mac frequently

**Fix:**
1. Check both devices are on same WiFi network
2. Increase WebSocket ping interval in `main.py`:
```python
   websocket_ping_interval = 30  # seconds
```
3. Add reconnection logic in mobile client
4. Check firewall isn't blocking port 8000

### Deepgram Connection Fails
**Symptom:** "Connection refused" or "Authentication failed"

**Fix:**
1. Verify Deepgram API key is set: `echo $DEEPGRAM_API_KEY`
2. Check API key has Live Agent API enabled in Deepgram console
3. Test connection: `python scripts/test_deepgram.py`
4. Ensure internet connection is stable

---

## Testing Strategy
```bash
# Unit tests for individual components
pytest tests/test_mcp_server.py -v
pytest tests/test_audio_handler.py -v
pytest tests/test_agent_logic.py -v

# Integration test (requires GUI access)
pytest tests/test_integration.py -v --capture=no

# Manual testing checklist:
# □ Voice command → successful Agent-S action
# □ Screenshot captured and returned to phone
# □ Error handling (Agent-S fails gracefully)
# □ Multiple commands in sequence work
# □ Emergency kill switch stops runaway actions
# □ Application focus handling works correctly
# □ Audio routing (BlackHole) captures voice clearly
```

### Example Test Cases

**Test 1: Basic Application Launch**
```
Voice: "Open Safari"
Expected: Safari launches or comes to front
Verify: Safari window is visible and active
```

**Test 2: UI Navigation**
```
Voice: "Click on System Settings"
Expected: System Settings opens
Verify: System Settings window in focus
```

**Test 3: Complex Task**
```
Voice: "Open Chrome and go to gmail.com"
Expected: Chrome opens, navigates to Gmail
Verify: Chrome shows Gmail login/inbox
```

**Test 4: Error Recovery**
```
Voice: "Click the nonexistent button"
Expected: Agent-S returns error gracefully
Verify: Error message sent to phone, no crash
```