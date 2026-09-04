"""
Configuration module for SALTY AI Call Agent.
Loads settings from environment variables and provides centralized access.
"""

from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings with environment variable bindings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Server Configuration
    HOST: str = Field(default="0.0.0.0", description="Server host address")
    PORT: int = Field(default=8000, description="Server port")
    LOG_LEVEL: str = Field(default="INFO", description="Logging level")
    CALL_AGENT_LOG_FILE: str = Field(default="logs/call-agent.log", description="Local call transcript log file")
    DEBUG: bool = Field(default=False, description="Debug mode flag")
    PLAY_GREETING: bool = Field(default=True, description="Play the automatic call-opening greeting")
    ENABLE_BARGE_IN: bool = Field(default=False, description="Interrupt TTS when inbound speech is detected")

    # Exotel Configuration
    EXOTEL_API_KEY: str = Field(default="", description="Exotel API key")
    EXOTEL_API_TOKEN: str = Field(default="", description="Exotel API token")
    EXOTEL_ACCOUNT_SID: str = Field(default="", description="Exotel Account SID")
    EXOTEL_SUB_DOMAIN: str = Field(default="api.exotel.com", description="Exotel Subdomain")
    EXOTEL_CALLER_ID: str = Field(default="", description="Exotel virtual number used as caller ID")
    EXOTEL_STREAM_URL: str = Field(default="", description="Public WSS URL for the Exotel AgentStream endpoint")
    EXOTEL_CALL_TIMEOUT_SECONDS: int = Field(default=30, description="Seconds to let the destination phone ring")
    EXOTEL_CALL_TIME_LIMIT_SECONDS: int = Field(default=3600, description="Maximum connected call duration")

    # Local voice pipeline. Ollama is the default brain; local STT/TTS keep
    # phone audio off third-party speech services.
    AI_PROVIDER: str = Field(default="ollama", description="Voice reasoning provider: ollama or backend")
    VOICE_PROVIDER: str = Field(default="sarvam", description="Speech provider: sarvam or local")
    OLLAMA_URL: str = Field(default="http://127.0.0.1:11434", description="Local Ollama URL")
    OLLAMA_MODEL: str = Field(default="gemma3:4b", description="Ollama model used for voice reasoning")
    OLLAMA_TIMEOUT_SECONDS: float = Field(default=30.0, description="Ollama request timeout")
    OLLAMA_MAX_TOKENS: int = Field(default=100, description="Maximum spoken response tokens")
    LOCAL_STT_MODEL: str = Field(default="base", description="faster-whisper model name or local path")
    LOCAL_STT_DEVICE: str = Field(default="cpu", description="faster-whisper device")
    LOCAL_STT_COMPUTE_TYPE: str = Field(default="int8", description="faster-whisper compute type")
    LOCAL_TTS_MODEL_PATH: str = Field(default="models/en_US-lessac-medium.onnx", description="Piper voice model path")
    LOCAL_TTS_CONFIG_PATH: str = Field(default="", description="Optional Piper .onnx.json path")
    LOCAL_TTS_EXECUTABLE: str = Field(default="piper", description="Piper executable")

    # Development Test Mode Flag & Groq AI Configuration
    CALL_AGENT_TEST_MODE: bool = Field(default=False, description="Development test mode flag for testing voice pipeline with fast LLM intelligence")
    GROQ_API_KEY: str = Field(default="", description="Groq API key for test mode intelligence")
    GROQ_MODEL: str = Field(default="openai/gpt-oss-20b", description="Groq model name for test mode")
    GROQ_BASE_URL: str = Field(default="https://api.groq.com/openai/v1", description="Groq API base URL")
    LLM_TIMEOUT_SECONDS: float = Field(default=8.0, description="Timeout for LLM API requests in seconds")
    LLM_MAX_RETRIES: int = Field(default=2, description="Maximum retry count for LLM API requests")




    # Sarvam AI Configuration
    SARVAM_API_KEY: str = Field(default="", description="Sarvam AI API key")

    SARVAM_BASE_URL: str = Field(default="https://api.sarvam.ai", description="Sarvam API base URL")
    SARVAM_STT_MODEL: str = Field(default="saaras:v3", description="Sarvam Saaras STT model")
    SARVAM_TTS_MODEL: str = Field(default="bulbul:v3", description="Sarvam Bulbul TTS model")
    SARVAM_DEFAULT_LANGUAGE_CODE: str = Field(default="te-IN", description="Default regional language code (Telugu)")
    SARVAM_DEFAULT_SPEAKER: str = Field(default="shubh", description="Default TTS speaker voice")

    # Main SALTY AI Backend Contract
    AI_BACKEND_URL: str = Field(default="http://127.0.0.1:8010", description="Main SALTY AI intelligence backend URL")
    AI_BACKEND_TIMEOUT_SECONDS: float = Field(default=10.0, description="HTTP timeout for AI backend calls")
    AI_BACKEND_MAX_RETRIES: int = Field(default=2, description="Maximum retry count for AI backend")
    AI_BACKEND_RETRY_DELAY_SECONDS: float = Field(default=0.5, description="Initial retry delay in seconds")

    # Audio & Voice Activity Detection (VAD) Settings
    AUDIO_SAMPLE_RATE: int = Field(default=8000, description="Telephony sample rate in Hz")
    AUDIO_CHANNELS: int = Field(default=1, description="Audio channels (mono)")
    AUDIO_SAMPLE_WIDTH: int = Field(default=2, description="Bytes per sample (16-bit PCM = 2)")
    AUDIO_CHUNK_BYTES: int = Field(default=3200, description="Chunk size in bytes (minimum 3.2KB / 3200 bytes for Exotel AgentStream, multiple of 320)")
    VAD_RMS_THRESHOLD: int = Field(default=350, description="RMS energy threshold to detect speech in noisy marine environment")
    VAD_MIN_SPEECH_MS: int = Field(default=250, description="Minimum duration of speech in ms to trigger turn processing")
    VAD_SILENCE_MS: int = Field(default=800, description="Duration of silence in ms to conclude caller utterance")
    VAD_MAX_BUFFER_SECONDS: float = Field(default=15.0, description="Maximum speech buffer duration in seconds before auto-flush")

    # Conversation Management Settings
    MAX_CONVERSATION_HISTORY_TURNS: int = Field(default=10, description="Maximum conversation turns to retain in session memory")
    DEFAULT_FALLBACK_LANGUAGE: str = Field(default="en-IN", description="Default fallback language code")



# Global singleton settings instance
settings = Settings()
