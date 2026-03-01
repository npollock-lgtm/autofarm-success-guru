"""
TTS Engine — generates voiceovers using Kokoro TTS with brand-specific voice models.

Each brand has a consistent voice persona.  The engine produces:
  * ``.wav`` audio files
  * Word-level timestamps for subtitle/caption generation

Resource requirements: ~200 MB RAM for voice models, 2 GB free at runtime.
Cannot run concurrently with video assembly (enforced by ResourceScheduler).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("autofarm.content_forge.tts_engine")

# ---------------------------------------------------------------------------
# Brand → voice model mapping
# ---------------------------------------------------------------------------

VOICE_PERSONAS: Dict[str, str] = {
    "human_success_guru": "af_heart",
    "wealth_success_guru": "am_adam",
    "zen_success_guru": "af_sky",
    "social_success_guru": "am_michael",
    "habits_success_guru": "af_bella",
    "relationships_success_guru": "af_sarah",
}

DEFAULT_VOICE = "af_heart"
SAMPLE_RATE = 24000
DEFAULT_SPEED = 1.0


# ---------------------------------------------------------------------------
# TTS result container
# ---------------------------------------------------------------------------


@dataclass
class TTSResult:
    """Outcome of a TTS generation."""

    audio_path: str
    duration_seconds: float
    word_timestamps: List[Dict[str, Any]] = field(default_factory=list)
    voice_model: str = ""
    sample_rate: int = SAMPLE_RATE

    def to_dict(self) -> Dict[str, Any]:
        """Serialise for pipeline handoff."""
        return {
            "audio_path": self.audio_path,
            "duration_seconds": self.duration_seconds,
            "word_timestamps": self.word_timestamps,
            "voice_model": self.voice_model,
            "sample_rate": self.sample_rate,
        }


# ---------------------------------------------------------------------------
# TTSEngine
# ---------------------------------------------------------------------------


class TTSEngine:
    """Generate voiceovers using Kokoro TTS.

    Parameters
    ----------
    media_root:
        Root directory for media output.
    resource_scheduler:
        ``ResourceScheduler`` for resource gating.
    """

    def __init__(
        self,
        media_root: str = "media",
        resource_scheduler: Optional[Any] = None,
    ) -> None:
        self.media_root = Path(media_root)
        self.resource_scheduler = resource_scheduler
        self._model_loaded = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def generate_voiceover(
        self,
        script_text: str,
        brand_id: str,
        output_path: Optional[str] = None,
        speed: float = DEFAULT_SPEED,
    ) -> TTSResult:
        """Generate a voiceover audio file with word-level timestamps.

        Parameters
        ----------
        script_text:
            The script body to convert to speech.
        brand_id:
            Brand identifier (selects voice model).
        output_path:
            Optional explicit output path; auto-generated if ``None``.
        speed:
            Speech speed multiplier (1.0 = normal).

        Returns
        -------
        TTSResult
            Contains audio path, duration, and word timestamps.

        Side Effects
        ------------
        * Creates a ``.wav`` file on disk.
        * Checks resource availability via ResourceScheduler.
        """
        # Resource check
        if self.resource_scheduler:
            can_run = await self.resource_scheduler.can_run_job("tts_generation")
            if not can_run:
                logger.warning("Insufficient resources for TTS — waiting")
                # Wait up to 60 s for resources
                for _ in range(12):
                    import asyncio
                    await asyncio.sleep(5)
                    if await self.resource_scheduler.can_run_job("tts_generation"):
                        break

        voice_model = self.get_brand_voice_model(brand_id)

        if output_path is None:
            out_dir = self.media_root / "voiceovers" / brand_id
            out_dir.mkdir(parents=True, exist_ok=True)
            ts = int(time.time())
            output_path = str(out_dir / f"vo_{brand_id}_{ts}.wav")

        # Attempt Kokoro TTS
        try:
            result = await self._kokoro_generate(
                script_text, voice_model, output_path, speed
            )
            return result
        except Exception as exc:
            logger.error("Kokoro TTS failed: %s — falling back to pyttsx3", exc)
            return await self._fallback_generate(
                script_text, brand_id, output_path, speed
            )

    def get_brand_voice_model(self, brand_id: str) -> str:
        """Return the Kokoro voice model identifier for *brand_id*.

        Parameters
        ----------
        brand_id:
            Brand identifier.

        Returns
        -------
        str
            Voice model name.
        """
        return VOICE_PERSONAS.get(brand_id, DEFAULT_VOICE)

    # ------------------------------------------------------------------
    # Kokoro TTS generation
    # ------------------------------------------------------------------

    async def _kokoro_generate(
        self,
        text: str,
        voice_model: str,
        output_path: str,
        speed: float,
    ) -> TTSResult:
        """Generate audio using Kokoro TTS library.

        Parameters
        ----------
        text:
            Input text.
        voice_model:
            Kokoro voice identifier.
        output_path:
            Destination ``.wav`` path.
        speed:
            Speed multiplier.

        Returns
        -------
        TTSResult
        """
        try:
            from kokoro import KPipeline

            pipeline = KPipeline(lang_code="a")  # American English
            word_timestamps: List[Dict[str, Any]] = []
            all_audio = []

            generator = pipeline(text, voice=voice_model, speed=speed)
            current_time = 0.0

            for i, (gs, ps, audio) in enumerate(generator):
                if audio is not None:
                    import numpy as np

                    duration = len(audio) / SAMPLE_RATE
                    # Record word-level timestamps from phoneme segments
                    words = gs.split() if gs else []
                    word_dur = duration / max(len(words), 1)
                    for j, word in enumerate(words):
                        word_timestamps.append({
                            "word": word,
                            "start": round(current_time + j * word_dur, 3),
                            "end": round(current_time + (j + 1) * word_dur, 3),
                        })
                    current_time += duration
                    all_audio.append(audio)

            if all_audio:
                import numpy as np
                import soundfile as sf

                combined = np.concatenate(all_audio)
                sf.write(output_path, combined, SAMPLE_RATE)
                total_duration = len(combined) / SAMPLE_RATE
            else:
                total_duration = 0.0

            return TTSResult(
                audio_path=output_path,
                duration_seconds=round(total_duration, 2),
                word_timestamps=word_timestamps,
                voice_model=voice_model,
            )
        except ImportError:
            raise RuntimeError("Kokoro TTS not installed")

    # ------------------------------------------------------------------
    # Fallback TTS
    # ------------------------------------------------------------------

    async def _fallback_generate(
        self,
        text: str,
        brand_id: str,
        output_path: str,
        speed: float,
    ) -> TTSResult:
        """Fallback TTS using pyttsx3 (offline, always available).

        Parameters
        ----------
        text:
            Input text.
        brand_id:
            Brand identifier.
        output_path:
            Output file path.
        speed:
            Speed multiplier.

        Returns
        -------
        TTSResult
        """
        try:
            import pyttsx3

            engine = pyttsx3.init()
            rate = int(engine.getProperty("rate") * speed)
            engine.setProperty("rate", rate)
            engine.save_to_file(text, output_path)
            engine.runAndWait()

            # Estimate duration from word count
            words = text.split()
            estimated_duration = len(words) / (2.5 * speed)  # ~150 wpm

            # Build estimated timestamps
            word_timestamps = []
            word_dur = estimated_duration / max(len(words), 1)
            for i, word in enumerate(words):
                word_timestamps.append({
                    "word": word,
                    "start": round(i * word_dur, 3),
                    "end": round((i + 1) * word_dur, 3),
                })

            return TTSResult(
                audio_path=output_path,
                duration_seconds=round(estimated_duration, 2),
                word_timestamps=word_timestamps,
                voice_model=f"pyttsx3_fallback_{brand_id}",
            )
        except Exception as exc:
            logger.error("Fallback TTS also failed: %s", exc)
            # Last resort — silent audio
            return self._generate_silent_audio(text, output_path)

    def _generate_silent_audio(
        self, text: str, output_path: str
    ) -> TTSResult:
        """Generate a silent .wav as absolute last-resort fallback.

        Parameters
        ----------
        text:
            Original text (for timestamp estimation).
        output_path:
            Output path.

        Returns
        -------
        TTSResult
        """
        duration = max(len(text.split()) / 2.5, 5.0)
        try:
            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", f"anullsrc=r={SAMPLE_RATE}:cl=mono",
                "-t", str(duration),
                output_path,
            ]
            subprocess.run(cmd, check=True, capture_output=True, timeout=30)
        except Exception:
            # Create an empty file
            Path(output_path).touch()

        words = text.split()
        word_dur = duration / max(len(words), 1)
        word_timestamps = [
            {
                "word": w,
                "start": round(i * word_dur, 3),
                "end": round((i + 1) * word_dur, 3),
            }
            for i, w in enumerate(words)
        ]

        return TTSResult(
            audio_path=output_path,
            duration_seconds=round(duration, 2),
            word_timestamps=word_timestamps,
            voice_model="silent_fallback",
        )
