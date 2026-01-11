# ⚡ Koda: Agentic Remote Desktop

> **Your Desktop, Unchained.** > A voice-native interface that allows you to control your computer via phone call using Vapi, Deepgram, and Agent-S.

![Python](https://img.shields.io/badge/Python-3.10%2B-blue)
![Vapi](https://img.shields.io/badge/Orchestration-Vapi-purple)
![Deepgram](https://img.shields.io/badge/Voice-Deepgram_Nova_3-red)
![Agent-S](https://img.shields.io/badge/Agent-Agent_S-green)

## 📖 About
**Koda** bridges the gap between mobile telephony and desktop automation. Instead of using clumsy remote desktop apps on a small screen, Koda allows you to dial a phone number and issue natural language commands to your computer.

It uses **Agent-S** (a multimodal GUI agent) to "see" your screen and control the mouse/keyboard, while **Vapi** handles the low-latency voice infrastructure.

## 🏗️ Architecture
The system functions as a high-speed Voice-to-Action pipeline:

1.  **Input:** User calls the provided phone number (Twilio/Vapi).
2.  **Transcription:** Audio is streamed to **Deepgram Nova-3** for sub-300ms transcription.
3.  **Reasoning:** **Claude 3.5 Sonnet** analyzes the intent and generates a tool call.
4.  **Transport:** The instruction travels via **ngrok** to a local Python server.
5.  **Execution:** **Agent-S** grounds the command in the local UI (Mac/Windows) and executes the click/type action.
6.  **Feedback:** The system speaks back the result via **Deepgram Aura**.

## 🚀 Prerequisites

### Software
* **Python 3.10+**
* **ngrok** (for tunneling)
* **Tesseract OCR** (Required for Windows users to read screen text)

### API Keys
You will need accounts and keys for:
* [Deepgram](https://deepgram.com) (STT/TTS)
* [Anthropic](https://anthropic.com) (LLM Intelligence)
* [Vapi.ai](https://vapi.ai) (Voice Orchestration)

## 🛠️ Installation

1.  **Clone the repository**
    ```bash
    git clone [https://github.com/rahulp7801/remotedesktopview.git](https://github.com/rahulp7801/remotedesktopview.git)
    cd remotedesktopview
    ```

2.  **Install Dependencies**
    ```bash
    pip install -r requirements.txt
    # Note: If on Windows, ensure you install: pip install gui-agents[windows]
    ```

3.  **Configure Environment**
    Create a `.env` file in the root directory:
    ```ini
    ANTHROPIC_API_KEY=sk-ant-...
    DEEPGRAM_API_KEY=...
    ```

4.  **Start ngrok Tunnel**
    Expose your local server to the internet:
    ```bash
    ngrok http 8000
    ```
    *Copy the `https` forwarding URL.*

## ⚙️ Vapi Configuration

1.  Create a new Assistant in the **Vapi Dashboard**.
2.  **Transcriber:** Set to Deepgram (Model: Nova-3).
3.  **Voice:** Set to Deepgram Aura (e.g., Asteria or Orion).
4.  **Model:** Set to Claude 3.5 Sonnet.
5.  **Tools:**
    * Create a new tool (Type: MCP/Function).
    * **Server URL:** `YOUR_NGROK_URL/mcp` (e.g., `https://a1b2.ngrok-free.app/mcp`)
    * **Prompt/Instruction:** "Use the `execute_gui_task` tool to perform actions on the computer."

## 🏃 Usage

1.  **Run the Local Server**
    Open a terminal (Run as Administrator on Windows) and start the listener:
    ```bash
    python main.py
    ```

2.  **Call Koda**
    Dial the phone number assigned in your Vapi dashboard.

3.  **Speak Commands**
    * "Open Chrome and go to YouTube."
    * "Open Notepad and type 'Hello World'."
    * "Find the PDF in my downloads folder."

## 🐛 Troubleshooting

* **Antivirus Blocking:** If `ngrok` fails to run, check your antivirus (e.g., McAfee). You may need to add the project folder to the exclusion list.
* **Latency:** Ensure you are using Deepgram **Nova-3** in Vapi settings. Older models will introduce 2+ seconds of lag.
* **Permissions:** On macOS, you must grant "Accessibility" and "Screen Recording" permissions to your Terminal/IDE. On Windows, run the terminal as Administrator.

## 🔮 Future Roadmap
* [ ] Full Cross-Platform support (Linux).
* [ ] Voice ID Authentication (Security).
* [ ] "Proactive" mode (Koda notifies you of alerts).

## 📄 License
MIT License.
