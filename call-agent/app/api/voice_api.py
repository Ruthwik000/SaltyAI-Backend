"""
Browser voice for the SALTY web console.

The console uses the same Sarvam speech engines as the phone line, so a
question can be asked in any supported Indian language without picking one
first, and an answer is read in the language it was written in - including
languages the visitor's browser has no voice for.

    POST /api/voice/transcribe   audio (any browser format) -> transcript + language
    POST /api/voice/speak        text (+ optional language)  -> audio/wav
"""

import asyncio
import logging
import re
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.ai.voice_text import detect_text_language, sanitize_speech_output
from app.speech.audio_utils import pcm_to_wav
from app.speech.stt import SarvamSTTClient
from app.speech.tts import SarvamTTSClient

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/voice", tags=["Browser voice"])

stt = SarvamSTTClient()
tts = SarvamTTSClient()

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
STT_SAMPLE_RATE = 16000
TTS_SAMPLE_RATE = 22050
MAX_CHUNK_CHARS = 450

# Languages Sarvam speaks, and the browser codes that mean the same thing.
SUPPORTED = {"bn-IN", "en-IN", "gu-IN", "hi-IN", "kn-IN", "ml-IN", "mr-IN", "od-IN", "pa-IN", "ta-IN", "te-IN"}
ALIASES = {"or-IN": "od-IN", "or": "od-IN", "en": "en-IN", "en-US": "en-IN", "en-GB": "en-IN"}

# Script ranges detect_text_language does not cover.
EXTRA_SCRIPTS = [
    (re.compile(r"[઀-૿]"), "gu-IN"),
    (re.compile(r"[଀-୿]"), "od-IN"),
    (re.compile(r"[਀-੿]"), "pa-IN"),
]


def spoken_language(text: str, requested: Optional[str]) -> str:
    """The language to speak `text` in: the caller's choice if valid, else its script."""
    if requested:
        code = ALIASES.get(requested, requested)
        if code in SUPPORTED:
            return code
    for pattern, code in EXTRA_SCRIPTS:
        if pattern.search(text):
            return code
    return detect_text_language(text, default_language="en-IN")


def speakable(text: str) -> str:
    """Strip what is written for a screen: links, dataset files, markdown tables."""
    cleaned = re.sub(r"https?://\S+", " ", text or "")
    cleaned = re.sub(r"\b[\w-]+/[\w.-]+\.nc\b", " ", cleaned)
    cleaned = re.sub(r"(^|\s)[-—:|]{3,}(?=\s|$)", " ", cleaned)
    cleaned = cleaned.replace("|", ", ")
    return sanitize_speech_output(cleaned)


def chunks(text: str, limit: int = MAX_CHUNK_CHARS) -> list[str]:
    """Sentence-sized pieces Sarvam accepts, breaking at full stops and dandas."""
    out, current = [], ""
    for sentence in re.split(r"(?<=[.!?।॥])\s+", text):
        sentence = sentence.strip()
        if not sentence:
            continue
        while len(sentence) > limit:
            cut = sentence.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            out.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if len(current) + len(sentence) + 1 > limit and current:
            out.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        out.append(current)
    return out


async def to_pcm(audio: bytes) -> bytes:
    """Any browser recording (webm, ogg, mp4, wav) to 16 kHz mono 16-bit PCM."""
    process = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0",
        "-ac", "1", "-ar", str(STT_SAMPLE_RATE), "-f", "s16le", "pipe:1",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        pcm, error = await asyncio.wait_for(process.communicate(audio), timeout=20)
    except asyncio.TimeoutError:
        process.kill()
        raise HTTPException(status_code=422, detail="Audio could not be decoded in time")
    if process.returncode != 0:
        logger.warning("ffmpeg could not decode upload: %s", error.decode(errors="replace")[:200])
        raise HTTPException(status_code=422, detail="Audio format not recognised")
    return pcm


@router.post("/transcribe")
async def transcribe(file: UploadFile = File(...), language: str = Form("unknown")):
    audio = await file.read()
    if not audio:
        raise HTTPException(status_code=422, detail="Empty recording")
    if len(audio) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Recording too long")

    pcm = await to_pcm(audio)
    if len(pcm) < STT_SAMPLE_RATE * 2 * 0.3:  # under 0.3 s
        raise HTTPException(status_code=422, detail="Recording too short")

    hint = ALIASES.get(language, language)
    result = await stt.transcribe(
        pcm,
        sample_rate=STT_SAMPLE_RATE,
        language_code=hint if hint in SUPPORTED else "unknown",
        timeout_seconds=30,
    )
    if result.is_empty:
        raise HTTPException(status_code=422, detail="No speech was recognised")
    return {"transcript": result.transcript, "language": result.language_code}


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=6000)
    language: Optional[str] = None


@router.post("/speak")
async def speak(request: SpeakRequest):
    text = speakable(request.text)
    if not text:
        raise HTTPException(status_code=422, detail="Nothing to speak")
    language = spoken_language(text, request.language)

    pcm = bytearray()
    rate = TTS_SAMPLE_RATE
    for piece in chunks(text):
        audio = await tts.synthesize(piece, language_code=language, sample_rate=TTS_SAMPLE_RATE, timeout_seconds=30)
        if not audio.pcm_audio:
            raise HTTPException(status_code=502, detail="Speech synthesis failed")
        rate = audio.sample_rate or rate
        pcm.extend(audio.pcm_audio)

    return Response(
        content=pcm_to_wav(bytes(pcm), sample_rate=rate),
        media_type="audio/wav",
        headers={"X-Speech-Language": language, "Cache-Control": "no-store", "Access-Control-Expose-Headers": "X-Speech-Language"},
    )
