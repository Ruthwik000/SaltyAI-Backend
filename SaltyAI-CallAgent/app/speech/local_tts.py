"""Offline text-to-speech using Piper."""

import asyncio
import io
import logging
import os
import wave
import sys
import uuid
from app.config import settings
from app.models.schemas import TTSResponse

logger = logging.getLogger(__name__)


class LocalPiperTTSClient:
    async def synthesize(self, text: str, language_code: str = "en-IN",
                         sample_rate: int = 8000, timeout_seconds: float = 20.0) -> TTSResponse:
        model_path = settings.LOCAL_TTS_MODEL_PATH
        if not os.path.isabs(model_path):
            model_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), model_path)
        if not os.path.exists(model_path):
            logger.error("Local Piper model not found: %s", model_path)
            return TTSResponse(pcm_audio=b"", sample_rate=sample_rate, duration_seconds=0.0)
        config_path = settings.LOCAL_TTS_CONFIG_PATH
        if settings.LOCAL_TTS_CONFIG_PATH:
            if not os.path.isabs(config_path):
                config_path = os.path.join(os.path.dirname(model_path), os.path.basename(config_path))
        piper_executable = settings.LOCAL_TTS_EXECUTABLE
        if not os.path.isabs(piper_executable):
            candidate = os.path.join(os.path.dirname(sys.executable), piper_executable)
            piper_executable = candidate if os.path.exists(candidate) else piper_executable
        # Startup prewarming synthesizes multiple greetings concurrently. A
        # PID-only filename lets concurrent Piper calls delete each other's WAV.
        output_path = os.path.join(
            os.path.dirname(model_path),
            f".salty-tts-{os.getpid()}-{uuid.uuid4().hex}.wav",
        )
        command = [piper_executable, "--model", model_path, "--output_file", output_path]
        if config_path:
            command += ["--config", config_path]
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=os.path.dirname(model_path),
            )
            _, stderr = await asyncio.wait_for(
                process.communicate((text.strip() + "\n").encode("utf-8")),
                timeout=timeout_seconds,
            )
            if process.returncode != 0:
                raise RuntimeError(stderr.decode(errors="replace")[-1000:])
            with wave.open(output_path, "rb") as wav:
                pcm = wav.readframes(wav.getnframes())
                rate = wav.getframerate()
            if rate != sample_rate:
                from app.speech.audio_utils import wav_to_pcm
                # Piper output is already WAV; rebuild a minimal WAV for the
                # shared converter when Exotel's 8 kHz rate is requested.
                buf = io.BytesIO()
                with wave.open(buf, "wb") as out:
                    out.setnchannels(1); out.setsampwidth(2); out.setframerate(rate); out.writeframes(pcm)
                pcm, rate = wav_to_pcm(buf.getvalue(), target_sample_rate=sample_rate)
            return TTSResponse(pcm_audio=pcm, sample_rate=rate,
                               duration_seconds=len(pcm) / (rate * 2) if rate else 0.0)
        except Exception as exc:
            logger.error("Local Piper TTS failed: %s", exc, exc_info=True)
            return TTSResponse(pcm_audio=b"", sample_rate=sample_rate, duration_seconds=0.0)
        finally:
            try:
                os.unlink(output_path)
            except FileNotFoundError:
                pass


tts_client = LocalPiperTTSClient()
