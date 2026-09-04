# SALTY AI - Voice Call Agent

> **Voice Interface for Marine Intelligence, Safety, and Rescue Platform**  
> Enables fishermen with basic/feature phones (PSTN/2G) to naturally converse in Indian regional languages via an Exotel phone number without requiring smartphones, apps, or mobile internet.

---

## 🌊 Architecture Overview

```
Fisherman's Basic Phone (PSTN/2G)
            │
            ▼
       Exotel Cloud
            │
            ▼ (Bidirectional WebSocket: AgentStream)
┌─────────────────────────────────────────────────────────────┐
│  SALTY AI Call Agent (FastAPI Voice Gateway)               │
│                                                             │
│  1. WebSocket Gateway (`app/voice/websocket.py`)            │
│     - Protocol: Exotel AgentStream (`start`, `media`, etc.) │
│     - Formats: 16-bit Linear PCM (8kHz mono, Base64)        │
│     - Voice Activity Detection (VAD) & Silence Endpointing  │
│     - Real-Time Barge-In (Instant `clear` event on speech)  │
│                                                             │
│  2. Speech-to-Text (`app/speech/stt.py`)                    │
│     - Real Sarvam Saaras API (`POST /speech-to-text`)       │
│     - Auto Language Detection + Code-Mixing (Tanglish, etc.)│
│                                                             │
│  3. Layered Emergency System (`app/api/emergency.py`)       │
│     - Layer 1: Fast Multilingual Distress Lexicon           │
│     - Layer 2: Semantic Distress & Marine Failure Intent   │
│     - Layer 3: Async Forwarding to Main Backend Rescue API  │
│                                                             │
│  4. Natural Conversation Manager (`app/conversation/`)      │
│     - Multi-turn context & Bounded session history          │
│     - Free-form dialogue (no rigid IVR forms or slot order) │
│                                                             │
│  5. Main AI Backend Connector (`app/ai/backend_client.py`)  │
│     - Async HTTPX with retries, backoff, and fallbacks      │
│     - Communicates with LangGraph Marine Reasoning Backend  │
│     - Supports Groq in Development Test Mode                │

│                                                             │
│  6. Text-to-Speech (`app/speech/tts.py`)                    │
│     - Real Sarvam Bulbul API (`POST /text-to-speech`)       │
│     - Telephony-optimized 8kHz PCM streaming back to Exotel │
└─────────────────────────────────────────────────────────────┘
            │
            ▼
Main SALTY AI Backend (`POST /api/ai/query` & `POST /api/emergency`)
(LangGraph + Marine / PFZ / Rescue Agents)
```

---

## 📁 Project Structure

```
call-agent/
├── app/
│   ├── __init__.py
│   ├── main.py                  # FastAPI application entrypoint & lifecycle
│   ├── config.py                # Environment settings via pydantic-settings
│   ├── api/
│   │   ├── __init__.py
│   │   ├── health.py            # Health, liveness, and readiness endpoints
│   │   └── emergency.py         # Layered distress detection & backend dispatch
│   ├── voice/
│   │   ├── __init__.py
│   │   ├── exotel.py            # Verified Exotel AgentStream protocol & frame builders
│   │   ├── session.py           # Active call audio buffers & barge-in tokens
│   │   ├── vad.py               # Energy/RMS Voice Activity Detector & endpointing
│   │   └── websocket.py         # Bidirectional WebSocket router (/ws/exotel/stream)
│   ├── speech/
│   │   ├── __init__.py
│   │   ├── audio_utils.py       # PCM chunking (320B), WAV framing, RMS calculation
│   │   ├── stt.py               # Real Sarvam Saaras STT client
│   │   └── tts.py               # Real Sarvam Bulbul TTS client
│   ├── ai/
│   │   ├── __init__.py
│   │   └── backend_client.py    # Main SALTY AI backend connector (HTTPX)
│   ├── conversation/
│   │   ├── __init__.py
│   │   └── manager.py           # Multi-turn context, memory window, language tracking
│   └── models/
│       ├── __init__.py
│       └── schemas.py           # Pydantic schemas for AI query, emergency, Exotel, STT/TTS
├── tests/                       # Complete pytest suite
├── .env.example                 # Environment configuration template
├── requirements.txt             # Python dependencies
├── Dockerfile                   # Production container definition
└── README.md                    # Documentation
```

---

## ⚙️ Environment Variables

Create a `.env` file in `call-agent/` (copy from `.env.example`):

| Variable | Default | Description |
| :--- | :--- | :--- |
| `HOST` | `0.0.0.0` | Server bind host |
| `PORT` | `8000` | Server bind port |
| `LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `EXOTEL_API_KEY` | - | Exotel API Key from Exotel Dashboard |
| `EXOTEL_API_TOKEN` | - | Exotel API Token |
| `EXOTEL_ACCOUNT_SID` | - | Exotel Account SID |
| `EXOTEL_SUB_DOMAIN` | `api.exotel.com` | Exotel API Subdomain |
| `SARVAM_API_KEY` | - | Sarvam AI API subscription key |
| `SARVAM_BASE_URL` | `https://api.sarvam.ai`| Sarvam API base URL |
| `SARVAM_STT_MODEL`| `saaras:v3` | Sarvam Saaras model version |
| `SARVAM_TTS_MODEL`| `bulbul:v3` | Sarvam Bulbul model version |
| `SARVAM_DEFAULT_LANGUAGE_CODE` | `ta-IN` | Default regional language (Tamil) |
| `SARVAM_DEFAULT_SPEAKER` | `shubh` | Default voice speaker |
| `AI_BACKEND_URL` | `http://127.0.0.1:8010`| Main SALTY AI backend URL (`POST /api/ai/query`) |
| `AI_BACKEND_TIMEOUT_SECONDS` | `10.0` | HTTP timeout for AI backend queries |
| `AI_BACKEND_MAX_RETRIES` | `2` | Maximum retry attempts for AI backend |
| `AUDIO_SAMPLE_RATE` | `8000` | Telephony audio sample rate (Hz) |
| `VAD_RMS_THRESHOLD` | `350` | RMS threshold for speech detection (marine noise) |
| `VAD_MIN_SPEECH_MS` | `250` | Minimum speech duration before speech start |
| `VAD_SILENCE_MS` | `800` | Silence duration to trigger endpointing |
| `MAX_CONVERSATION_HISTORY_TURNS` | `10` | Bounded multi-turn memory window |

---

## 🔌 API Contracts

### 1. Main AI Backend Query (`POST {AI_BACKEND_URL}/api/ai/query`)
**Request:**
```json
{
  "call_id": "call_abc123",
  "phone_number": "+919876543210",
  "language": "ta-IN",
  "message": "Tomorrow morning sea condition epdi?",
  "conversation_history": [
    {"role": "user", "content": "Can I go fishing tomorrow?"},
    {"role": "assistant", "content": "Tomorrow morning is moderate."}
  ],
  "location": {
    "name": "Chennai Coast",
    "latitude": 13.0827,
    "longitude": 80.2707
  }
}
```
**Response:**
```json
{
  "response": "நாளை காலை கடல் மிதமாக இருக்கும். மதியத்திற்கு பிறகு அலைகள் அதிகமாகும் என்பதால் எச்சரிக்கையாக இருங்கள்.",
  "language": "ta-IN",
  "priority": "normal"
}
```

### 2. Emergency Forwarding (`POST {AI_BACKEND_URL}/api/emergency`)
**Request:**
```json
{
  "call_id": "call_abc123",
  "phone_number": "+919876543210",
  "language": "ta-IN",
  "transcript": "படகு மூழ்குது காப்பாத்துங்க",
  "location": null
}
```
**Response:**
```json
{
  "status": "acknowledged",
  "rescue_id": "RESCUE-2026-001",
  "message": "Coastal Rescue Team dispatched"
}
```

---

## 📞 Exotel AgentStream Integration Setup

1. **Exotel Dashboard Configuration**:
   - Navigate to **Exotel App Bazaar** -> Create **Voicebot Applet**.
   - Under Voicebot Stream Settings:
     - **Stream URL (WSS)**: `wss://<your-domain>/ws/exotel/stream` (or `/ws/voice/stream`)
     - **Audio Format**: `16-bit Linear PCM (s16le)`
     - **Sample Rate**: `8000 Hz` (Mono)
     - **Bidirectional Streaming**: Enabled
   - Assign the Applet to your Exotel Virtual Number (VN).

2. **Protocol Features**:
   - **Barge-In**: When the caller speaks while the assistant is talking, the Call Agent detects speech via VAD and immediately sends a `{"event": "clear", "stream_sid": "<stream_sid>"}` frame to flush Exotel's playback buffer and cancels ongoing TTS.
   - **Chunking**: Outgoing audio is streamed in 3200-byte (3.2KB / 200ms at 8kHz) frames aligned to 320-byte boundaries with dynamic telephony pacing.

---

## 🚀 How to Run Locally

### 1. Install Dependencies
```bash
cd call-agent
pip install -r requirements.txt
```

### 2. Start the Server
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 3. Run Test Suite
```bash
pytest tests/ -v
```

---

## 🐳 Docker Deployment

### Build Container
```bash
docker build -t salty-call-agent:latest .
```

### Run Container
```bash
docker run -d -p 8000:8000 --env-file .env --name salty-call-agent salty-call-agent:latest
```
