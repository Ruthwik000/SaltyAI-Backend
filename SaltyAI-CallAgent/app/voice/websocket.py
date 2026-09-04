"""
Exotel AgentStream Bidirectional WebSocket Gateway.
Handles real-time audio streaming, speech endpointing, barge-in / clear events,
and connects the voice pipeline (Exotel -> STT -> Conversation -> AI Backend -> TTS -> Exotel).
Features strict per-call TTS ownership, latency instrumentation, and safe lifecycle termination.
"""

import time
import asyncio
import logging
from typing import Optional, Dict
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.config import settings
from app.models.schemas import (
    ExotelStartEvent,
    ExotelMediaEvent,
    ExotelStopEvent,
    ExotelDTMFEvent,
    ExotelMarkEvent,
)
from app.voice.exotel import (
    ExotelEventTypes,
    build_media_message,
    build_mark_message,
    build_clear_message,
    parse_exotel_message,
)
from app.voice.session import StreamSession
from app.voice.vad import VADStatus
from app.speech.audio_utils import b64_to_pcm, pcm_to_b64, chunk_pcm_audio, calculate_rms_energy
from app.speech.stt import stt_client

from app.speech.tts import tts_client
from app.conversation.manager import conversation_manager, needs_location_context, location_from_reply
from app.ai.backend_client import ai_backend_client
from app.api.emergency import emergency_detector

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Voice Stream"])

# English-only opening prompt for every call.
GREETING_MESSAGE = "Hello! This is SALTY AI. How can I help you today?"


async def prewarm_greetings():
    """Pre-synthesize the English greeting to reduce pickup latency."""
    try:
        if settings.SARVAM_API_KEY or settings.VOICE_PROVIDER.lower() == "local":
            logger.info("Pre-warming English greeting audio cache...")
            await tts_client.synthesize(
                GREETING_MESSAGE,
                language_code="en-IN",
                sample_rate=settings.AUDIO_SAMPLE_RATE,
            )
            logger.info("Greeting audio cache pre-warmed successfully.")
    except Exception as e:
        logger.warning(f"Greeting pre-warming skipped: {e}")


async def play_tts_audio_to_exotel(
    websocket: WebSocket,
    stream_session: StreamSession,
    text: str,
    language_code: str,
    mark_name: str = "response_end",
    target_generation_id: int = 0,
    t_speech_end: Optional[float] = None,
) -> None:
    """
    Synthesize text using Sarvam Bulbul and stream chunked PCM audio to Exotel.
    Guaranteed per-call TTS ownership with generation ID validation before every frame.
    """
    if not text or not text.strip():
        return

    # Check validity before starting expensive operations
    if stream_session.is_closed or not stream_session.is_connected:
        return
    if stream_session.generation_id != target_generation_id:
        logger.debug(f"Aborting TTS playback: Stale generation ({target_generation_id} vs current {stream_session.generation_id})")
        return

    t_tts_start = time.perf_counter()

    try:
        # Step 1: Synthesize text with Sarvam Bulbul TTS
        tts_res = await tts_client.synthesize(
            text=text,
            language_code=language_code,
            sample_rate=settings.AUDIO_SAMPLE_RATE,
        )

        t_tts_end = time.perf_counter()
        t_tts_ms = (t_tts_end - t_tts_start) * 1000

        # Re-verify generation and connection after network request
        if stream_session.is_closed or not stream_session.is_connected:
            return
        if stream_session.generation_id != target_generation_id:
            logger.debug(f"TTS completed but generation became stale ({target_generation_id} != {stream_session.generation_id}) | stream={stream_session.stream_sid}")
            return

        if not tts_res.pcm_audio:
            logger.warning("TTS synthesized empty audio, skipping playback")
            return

        # Step 2: Set is_playing_tts to True ONLY when audio streaming begins
        stream_session.is_playing_tts = True
        stream_session.playback_started_at = time.perf_counter()
        stream_session.playback_chunks_sent = 0
        stream_session.barge_in_speech_duration_ms = 0.0
        stream_session.barge_in_speech_chunks = 0

        # Step 3: Chunk audio into Exotel-compliant frames (3.2KB minimum, multiple of 320)
        chunk_size = max(3200, settings.AUDIO_CHUNK_BYTES)
        chunks = chunk_pcm_audio(tts_res.pcm_audio, chunk_size_bytes=chunk_size, min_chunk_bytes=3200)

        # Telephony pacing interval calculation
        bytes_per_sec = settings.AUDIO_SAMPLE_RATE * settings.AUDIO_SAMPLE_WIDTH
        chunk_duration_sec = chunk_size / bytes_per_sec if bytes_per_sec > 0 else 0.2
        chunk_interval = max(0.04, chunk_duration_sec * 0.90)  # Smooth real-time continuous pacing

        first_chunk_sent = False

        for idx, chunk in enumerate(chunks):
            # Guard against cancellation, barge-in, or session close before EVERY chunk
            if stream_session.is_closed or not stream_session.is_connected:
                return
            if stream_session.generation_id != target_generation_id or not stream_session.is_playing_tts:
                logger.info(f"Playback cancelled midway on chunk {idx + 1}/{len(chunks)} for stream {stream_session.stream_sid}")
                return

            b64_chunk = pcm_to_b64(chunk)
            media_msg = build_media_message(stream_session.stream_sid, b64_chunk)
            sent = await stream_session.safe_send_text(websocket, media_msg)
            if not sent:
                return

            stream_session.total_bytes_sent += len(chunk)
            stream_session.playback_chunks_sent += 1


            if not first_chunk_sent:
                first_chunk_sent = True
                t_first_chunk = time.perf_counter()
                if t_speech_end:
                    total_voice_latency_ms = (t_first_chunk - t_speech_end) * 1000
                    logger.info(
                        f"[VOICE LATENCY] Call {stream_session.call_id} | Speech-End to First Audio: {total_voice_latency_ms:.1f}ms "
                        f"| (TTS Synthesis: {t_tts_ms:.1f}ms)"
                    )

            logger.info(
                f"[EXOTEL OUTBOUND] media chunk {idx + 1}/{len(chunks)} ({len(chunk)} bytes) | "
                f"gen={target_generation_id} | stream_sid={stream_session.stream_sid}"
            )

            # Pacing sleep
            await asyncio.sleep(chunk_interval)

        # Step 4: Send mark event after all chunks successfully delivered
        if (
            not stream_session.is_closed
            and stream_session.is_connected
            and stream_session.generation_id == target_generation_id
            and stream_session.is_playing_tts
        ):
            mark_msg = build_mark_message(stream_session.stream_sid, mark_name)
            await stream_session.safe_send_text(websocket, mark_msg)
            logger.info(f"[EXOTEL OUTBOUND] mark frame sent: '{mark_name}' | stream_sid={stream_session.stream_sid}")

    except asyncio.CancelledError:
        logger.info(f"TTS playback task cancelled (Barge-In) on gen {target_generation_id} for stream {stream_session.stream_sid}")
    except Exception as e:
        logger.error(f"Error streaming audio to Exotel: {e}", exc_info=True)
    finally:
        if stream_session.generation_id == target_generation_id:
            stream_session.is_playing_tts = False


async def handle_user_turn(
    websocket: WebSocket,
    stream_session: StreamSession,
    pcm_audio: bytes,
) -> None:
    """
    Handle a complete caller speech segment:
    STT -> Emergency Check -> AI Intelligence -> Bulbul TTS -> Exotel Outbound.
    Enforces one active AI request per call and guarantees strict turn ordering.
    """
    current_task = asyncio.current_task()
    stream_session.active_turn_task = current_task

    t_speech_end = time.perf_counter()
    call_id = stream_session.call_id
    turn_generation_id = stream_session.generation_id

    try:
        # Step 1: Transcribe caller speech with Sarvam Saaras STT
        t_stt_start = time.perf_counter()
        call_sess = conversation_manager.get_session(call_id)
        # Let Sarvam detect the caller's language on the first turn. Once
        # detected, keep using that language for faster and more consistent
        # multilingual turns.
        stt_language = (
            "unknown"
            if call_sess and call_sess.turn_count == 0
            else stream_session.language
        )
        stt_res = await stt_client.transcribe(
            pcm_audio=pcm_audio,
            sample_rate=settings.AUDIO_SAMPLE_RATE,
            language_code=stt_language,
        )
        t_stt_end = time.perf_counter()
        t_stt_ms = (t_stt_end - t_stt_start) * 1000

        # Check if caller spoke again or disconnected while STT was running
        if stream_session.is_closed or stream_session.generation_id != turn_generation_id:
            logger.debug(f"Discarding turn {turn_generation_id} as generation is now {stream_session.generation_id}")
            return

        transcript = stt_res.transcript.strip()
        if not transcript or stt_res.is_empty:
            logger.debug(f"Empty transcript for call {call_id}, waiting for caller speech")
            return

        # Update language if detected
        if stt_res.language_code and stt_res.language_code != "unknown":
            stream_session.language = stt_res.language_code

        # Step 2: Layered Emergency Detection
        is_emergency, emergency_reason = emergency_detector.detect(transcript)
        # Location gate: marine questions must establish a caller location
        # before the reasoning model is allowed to answer them.
        if call_sess and (call_sess.awaiting_location or (needs_location_context(transcript) and not call_sess.has_location())):
            if call_sess.awaiting_location:
                caller_location = location_from_reply(transcript)
                if caller_location:
                    conversation_manager.update_location(call_id, caller_location)
                    call_sess.awaiting_location = False
                    transcript_for_ai = (
                        f"The caller's location is {caller_location.name}. "
                        "Use this location to answer the caller's most recent marine question."
                    )
                else:
                    location_prompt = "Please tell me your coastal city, village, or fishing location so I can answer you."
                    call_sess.add_user_message(transcript, detected_language=stream_session.language)
                    call_sess.add_assistant_message(location_prompt)
                    await stream_session.cancel_active_tts()
                    generation = stream_session.generation_id
                    task = asyncio.create_task(play_tts_audio_to_exotel(
                        websocket, stream_session, location_prompt, "en-IN",
                        mark_name="location_question", target_generation_id=generation,
                    ))
                    stream_session.active_tts_task = task
                    return
            else:
                call_sess.awaiting_location = True
                location_prompt = "Which coastal city, village, or fishing location are you calling from?"
                call_sess.add_user_message(transcript, detected_language=stream_session.language)
                call_sess.add_assistant_message(location_prompt)
                await stream_session.cancel_active_tts()
                generation = stream_session.generation_id
                task = asyncio.create_task(play_tts_audio_to_exotel(
                    websocket, stream_session, location_prompt, "en-IN",
                    mark_name="location_question", target_generation_id=generation,
                ))
                stream_session.active_tts_task = task
                return
        else:
            transcript_for_ai = transcript

        if is_emergency:
            logger.warning(
                f"EMERGENCY DETECTED on call {call_id}: reason='{emergency_reason}', "
                f"transcript='{transcript}'"
            )
            if call_sess:
                call_sess.emergency_state = True

            emergency_detector.trigger_async_dispatch(
                call_id=call_id,
                phone_number=stream_session.phone_number,
                transcript=transcript,
                language=stream_session.language,
                location=call_sess.location if call_sess else None,
            )

        # Step 3: Query AI Intelligence Backend (Grok / LangGraph)
        if stream_session.is_closed or stream_session.generation_id != turn_generation_id:
            logger.debug(f"Discarding turn {turn_generation_id} before AI query (superseded by {stream_session.generation_id})")
            return

        history_payload = call_sess.get_history_payload() if call_sess else []
        location = call_sess.location if call_sess else None

        t_ai_start = time.perf_counter()
        ai_resp = await ai_backend_client.query(
            call_id=call_id,
            phone_number=stream_session.phone_number,
            message=transcript_for_ai,
            language=stream_session.language,
            conversation_history=history_payload,
            location=location,
        )
        t_ai_end = time.perf_counter()
        t_ai_ms = (t_ai_end - t_ai_start) * 1000

        # Check if caller interrupted while AI was reasoning (AI completed, check if superseded)
        if stream_session.is_closed or stream_session.generation_id != turn_generation_id:
            logger.debug(f"Discarding turn {turn_generation_id} after AI reasoning (superseded by generation {stream_session.generation_id})")
            return


        response_text = ai_resp.response
        response_lang = ai_resp.language or stream_session.language

        # Step 4: Record confirmed turn in Conversation Manager in strict sequential order
        if call_sess:
            call_sess.add_user_message(transcript, detected_language=stream_session.language)
            call_sess.add_assistant_message(response_text)

        logger.info(
            f"[TURN PROCESSED] Call {call_id} | STT: {t_stt_ms:.1f}ms | AI: {t_ai_ms:.1f}ms | "
            f"User: '{transcript}' | AI: '{response_text[:70]}...' | Lang: {response_lang}"
        )
        logger.info(
            "[CALL TRANSCRIPT] call_id=%s | language=%s | location=%s | caller=%r | assistant=%r",
            call_id,
            response_lang,
            call_sess.location.name if call_sess and call_sess.location else "unknown",
            transcript,
            response_text,
        )

        # Step 5: Start exclusive TTS playback task
        await stream_session.cancel_active_tts()
        current_gen = stream_session.generation_id

        tts_task = asyncio.create_task(
            play_tts_audio_to_exotel(
                websocket=websocket,
                stream_session=stream_session,
                text=response_text,
                language_code=response_lang,
                mark_name=f"turn_{call_sess.turn_count if call_sess else 1}_end",
                target_generation_id=current_gen,
                t_speech_end=t_speech_end,
            )
        )
        stream_session.active_tts_task = tts_task

    except asyncio.CancelledError:
        logger.debug(f"Turn {turn_generation_id} task cancelled cleanly for call {call_id}")
    except Exception as e:
        logger.error(f"Error handling user turn for call {call_id}: {e}", exc_info=True)
    finally:
        if stream_session.active_turn_task is current_task:
            stream_session.active_turn_task = None


@router.websocket("/ws/exotel/stream")
@router.websocket("/ws/voice/stream")
async def exotel_agentstream_endpoint(websocket: WebSocket):
    """
    Bidirectional WebSocket endpoint for Exotel AgentStream / Voicebot Applet.
    Guarantees clean lifecycle management, barge-in protection, and graceful closure.
    """
    await websocket.accept()
    logger.info("Exotel AgentStream WebSocket connection established")

    stream_session: Optional[StreamSession] = None
    call_id: Optional[str] = None

    try:
        while True:
            raw_data = await websocket.receive_text()
            event_dict = parse_exotel_message(raw_data)
            if not event_dict:
                continue

            event_type = event_dict.get("event")

            # ------------------------------------------------------------------
            # 0. Event: CONNECTED (Handshake acknowledgement)
            # ------------------------------------------------------------------
            if event_type == ExotelEventTypes.CONNECTED:
                logger.info(f"[EXOTEL INBOUND] CONNECTED handshake received: {event_dict}")

            # ------------------------------------------------------------------
            # 1. Event: START (Stream session initialization)
            # ------------------------------------------------------------------
            elif event_type == ExotelEventTypes.START:
                start_data = event_dict.get("start", {})
                stream_sid = event_dict.get("stream_sid") or start_data.get("stream_sid") or ""
                call_sid = start_data.get("call_sid") or stream_sid
                call_id = call_sid
                from_number = start_data.get("from") or "UNKNOWN_CALLER"
                custom_params = start_data.get("custom_parameters") or {}
                selected_lang = custom_params.get("language") or settings.DEFAULT_FALLBACK_LANGUAGE

                logger.info(
                    f"[EXOTEL INBOUND] START event received | call_id={call_id} | stream_sid={stream_sid} | "
                    f"phone={from_number} | lang={selected_lang} | media_format={start_data.get('media_format')}"
                )

                # Create active stream session & conversational context
                stream_session = StreamSession(
                    call_id=call_id,
                    stream_sid=stream_sid,
                    phone_number=from_number,
                    language=selected_lang,
                )

                conversation_manager.create_session(
                    call_id=call_id,
                    stream_sid=stream_sid,
                    phone_number=from_number,
                    initial_language=selected_lang,
                )

                # Play initial spoken greeting with dedicated generation ID
                greeting_text = GREETING_MESSAGE
                greet_gen_id = stream_session.next_generation()
                greet_task = asyncio.create_task(
                    play_tts_audio_to_exotel(
                        websocket=websocket,
                        stream_session=stream_session,
                        text=greeting_text,
                        language_code="en-IN",
                        mark_name="greeting_end",
                        target_generation_id=greet_gen_id,
                    )
                )
                stream_session.active_tts_task = greet_task

            # ------------------------------------------------------------------
            # 2. Event: MEDIA (Incoming audio chunk from caller's phone)
            # ------------------------------------------------------------------
            elif event_type == ExotelEventTypes.MEDIA and stream_session:
                media_data = event_dict.get("media", {})
                b64_payload = media_data.get("payload", "")
                if not b64_payload:
                    continue

                pcm_chunk = b64_to_pcm(b64_payload)
                if not pcm_chunk:
                    continue

                # BARGE-IN CHECK: Is caller speaking while bot is actively outputting audio?
                if stream_session.is_playing_tts and stream_session.total_bytes_sent > 0:
                    # 1. Playback Echo Guard: Ignore initial line turnaround reflection during first 600ms or first 2 chunks
                    elapsed_playback_sec = time.perf_counter() - stream_session.playback_started_at
                    ECHO_GUARD_SECONDS = 0.60
                    MIN_PLAYBACK_CHUNKS = 2

                    if elapsed_playback_sec < ECHO_GUARD_SECONDS or stream_session.playback_chunks_sent < MIN_PLAYBACK_CHUNKS:
                        # Inside echo immunity window: discard echo leak
                        continue

                    # 2. Evaluate speech energy on this inbound chunk
                    chunk_rms = calculate_rms_energy(pcm_chunk, settings.AUDIO_SAMPLE_WIDTH)
                    speech_threshold = max(settings.VAD_RMS_THRESHOLD, 380)
                    chunk_samples = len(pcm_chunk) // settings.AUDIO_SAMPLE_WIDTH
                    chunk_duration_ms = (chunk_samples / settings.AUDIO_SAMPLE_RATE) * 1000.0 if settings.AUDIO_SAMPLE_RATE > 0 else 20.0

                    if chunk_rms >= speech_threshold:
                        stream_session.barge_in_speech_chunks += 1
                        stream_session.barge_in_speech_duration_ms += chunk_duration_ms
                        # Buffer candidate caller speech to preserve initial syllables
                        stream_session.append_audio(pcm_chunk)

                        # 3. Require a meaningful inbound speech segment (>= 280ms or 3 chunks of sustained energy)
                        # 3. Require a meaningful inbound speech segment (>= 280ms or 3 chunks of sustained energy)
                        MIN_BARGE_IN_SPEECH_MS = 280.0
                        if stream_session.barge_in_speech_duration_ms >= MIN_BARGE_IN_SPEECH_MS or stream_session.barge_in_speech_chunks >= 3:
                            logger.info(
                                f"[EXOTEL BARGE-IN] Genuine caller speech confirmed ({stream_session.barge_in_speech_duration_ms:.0f}ms, RMS={chunk_rms:.0f}) "
                                f"during playback on stream {stream_session.stream_sid}! Interrupting TTS."
                            )
                            # Cancel in-flight AI reasoning / TTS playback task & invalidate generation
                            await stream_session.cancel_active_turn()

                            # Send CLEAR frame to Exotel to stop telephony playback buffer immediately
                            clear_msg = build_clear_message(stream_session.stream_sid)
                            await stream_session.safe_send_text(websocket, clear_msg)
                            logger.info(f"[EXOTEL OUTBOUND] clear frame sent | stream_sid={stream_session.stream_sid}")

                            # Reset barge-in accumulators
                            stream_session.barge_in_speech_duration_ms = 0.0
                            stream_session.barge_in_speech_chunks = 0
                    else:
                        # Energy dropped (isolated blip / transient noise): reset counter and discard candidate buffer
                        if stream_session.barge_in_speech_chunks > 0 and stream_session.barge_in_speech_duration_ms < 280.0:
                            stream_session.clear_buffer()
                        stream_session.barge_in_speech_chunks = 0
                        stream_session.barge_in_speech_duration_ms = 0.0

                    continue


                # NORMAL LISTENING MODE:
                vad_status = stream_session.vad.process_chunk(pcm_chunk)
                stream_session.append_audio(pcm_chunk)

                if vad_status == VADStatus.SPEECH_ENDED:
                    # User completed speech utterance
                    speech_audio = stream_session.get_audio_and_reset()
                    if len(speech_audio) >= (settings.AUDIO_SAMPLE_RATE * settings.AUDIO_SAMPLE_WIDTH * 0.25):
                        logger.info(
                            f"[SPEECH DETECTED] End of speech segment ({len(speech_audio)} bytes) for call {call_id}. Dispatching STT turn."
                        )
                        # Cancel any previous in-flight AI / TTS task and allocate new generation
                        await stream_session.cancel_active_turn()
                        turn_task = asyncio.create_task(
                            handle_user_turn(websocket, stream_session, speech_audio)
                        )
                        stream_session.active_turn_task = turn_task

                # Safety buffer limit (auto-flush if talking > 15s continuously)
                max_bytes = int(settings.VAD_MAX_BUFFER_SECONDS * settings.AUDIO_SAMPLE_RATE * settings.AUDIO_SAMPLE_WIDTH)
                if len(stream_session.audio_buffer) > max_bytes:
                    logger.debug("Max audio buffer reached, flushing for transcription")
                    long_audio = stream_session.get_audio_and_reset()
                    await stream_session.cancel_active_turn()
                    turn_task = asyncio.create_task(
                        handle_user_turn(websocket, stream_session, long_audio)
                    )
                    stream_session.active_turn_task = turn_task

            # ------------------------------------------------------------------
            # 3. Event: MARK (Playback sync confirmation from Exotel)
            # ------------------------------------------------------------------
            elif event_type == ExotelEventTypes.MARK and stream_session:
                mark_name = event_dict.get("mark", {}).get("name")
                logger.info(f"[EXOTEL INBOUND] MARK event confirmed: '{mark_name}' | stream_sid={stream_session.stream_sid}")

            # ------------------------------------------------------------------
            # 4. Event: DTMF (Keypad press fallback)
            # ------------------------------------------------------------------
            elif event_type == ExotelEventTypes.DTMF and stream_session:
                digit = event_dict.get("dtmf", {}).get("digit")
                logger.info(f"[EXOTEL INBOUND] DTMF event: digit='{digit}' | call_id={stream_session.call_id}")
                dtmf_lang_map = {
                    "1": "te-IN",
                    "2": "en-IN",
                    "3": "ta-IN",
                    "4": "hi-IN",
                    "5": "ml-IN",
                }
                if digit in dtmf_lang_map:
                    stream_session.language = dtmf_lang_map[digit]
                    logger.info(f"DTMF switched language to {stream_session.language}")

            # ------------------------------------------------------------------
            # 5. Event: STOP (Call termination from Exotel)
            # ------------------------------------------------------------------
            elif event_type == ExotelEventTypes.STOP:
                stop_reason = event_dict.get("stop", {}).get("reason", "normal")
                logger.info(f"[EXOTEL INBOUND] STOP event received | call_id={call_id} | reason={stop_reason}")
                if stream_session:
                    stream_session.is_closed = True
                    stream_session.is_connected = False
                    await stream_session.cancel_active_turn()
                break

            else:
                logger.debug(f"[EXOTEL INBOUND] Unhandled event '{event_type}' received: {event_dict}")

    except WebSocketDisconnect:
        logger.info(f"Exotel WebSocket disconnected for call {call_id}")
        if stream_session:
            stream_session.is_closed = True
            stream_session.is_connected = False
            await stream_session.cancel_active_turn()
    except Exception as e:
        logger.error(f"WebSocket session exception for call {call_id}: {e}", exc_info=True)
    finally:
        if stream_session:
            stream_session.is_closed = True
            stream_session.is_connected = False
            await stream_session.cancel_active_turn()
        if call_id:
            conversation_manager.end_session(call_id)
        logger.info(f"Cleaned up call session for call_id={call_id}")
