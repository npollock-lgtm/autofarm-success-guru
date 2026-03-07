"""
Full pipeline test — generate one video per brand.

Runs: Script → TTS → B-roll → Captions → Video Assembly → Telegram notification
for all 6 brands. B-roll clips are fetched with varied search terms and
concatenated into a seamless background to avoid repetition.
"""

import asyncio
import json
import os
import subprocess
import sys
import time

# Ensure env is loaded before any imports that need it
from dotenv import load_dotenv
load_dotenv("/app/.env")

sys.path.insert(0, "/app")

from database.db import Database
from modules.ai_brain.llm_router import LLMRouter
from modules.ai_brain.script_writer import ScriptWriter
from modules.content_forge.tts_engine import TTSEngine
from modules.content_forge.broll_fetcher import BRollFetcher
from modules.content_forge.caption_generator import CaptionGenerator
from modules.content_forge.video_assembler import VideoAssembler

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

with open("/app/config/brands.json") as f:
    brands_data = json.load(f)
BRANDS = brands_data.get("brands", brands_data)

TOPICS = {
    "human_success_guru": "Why most people self-sabotage their success without realizing it",
    "wealth_success_guru": "The compound effect: how small daily investments create millionaires",
    "zen_success_guru": "Marcus Aurelius on letting go of what you cannot control",
    "social_success_guru": "How to read body language and instantly know what people are thinking",
    "habits_success_guru": "The 2-minute rule that can transform your entire morning routine",
    "relationships_success_guru": "Why emotionally intelligent people never say these 3 phrases",
}

# Multiple varied search terms per brand for diverse b-roll
BROLL_SEARCHES = {
    "human_success_guru": ["dark motivation", "person thinking alone", "chess strategy", "city night lights", "man walking determined"],
    "wealth_success_guru": ["money growth", "stock market", "luxury lifestyle", "business meeting", "gold coins"],
    "zen_success_guru": ["meditation peaceful", "zen garden stones", "mountain sunrise", "calm ocean waves", "forest path"],
    "social_success_guru": ["people talking", "body language", "confident speaker", "crowd gathering", "eye contact"],
    "habits_success_guru": ["morning routine", "workout discipline", "writing journal", "alarm clock sunrise", "healthy breakfast"],
    "relationships_success_guru": ["couple conversation", "emotional connection", "family together", "holding hands", "deep conversation"],
}

# Stub rate limiter for b-roll fetcher
class StubRateLimiter:
    async def acquire(self, *a, **kw):
        return True
    async def check_limit(self, *a, **kw):
        return True

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def concatenate_broll(clip_paths: list, output_path: str, target_duration: float) -> str:
    """Concatenate multiple b-roll clips into one seamless background video.

    Scales all clips to 1080x1920 portrait, concatenates them, and trims
    to target_duration. If clips are shorter than needed, the last clip
    is looped to fill the gap.
    """
    if not clip_paths:
        return ""

    # Create a temp file listing all clips for FFmpeg concat
    list_dir = os.path.dirname(output_path)
    os.makedirs(list_dir, exist_ok=True)
    list_file = os.path.join(list_dir, "concat_list.txt")

    # First, scale each clip to 1080x1920 and re-encode for compatibility
    scaled_clips = []
    for i, clip in enumerate(clip_paths):
        scaled = os.path.join(list_dir, f"scaled_{i}.mp4")
        cmd = [
            "ffmpeg", "-y", "-i", clip,
            "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,"
                   "pad=1080:1920:(ow-iw)/2:(oh-ih)/2,setsar=1",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-r", "30", "-an", "-t", "15",
            scaled,
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=60)
            if os.path.exists(scaled) and os.path.getsize(scaled) > 0:
                scaled_clips.append(scaled)
        except Exception as e:
            print(f"    [WARN] Failed to scale clip {i}: {e}")

    if not scaled_clips:
        return ""

    # Write concat list — repeat clips if needed to fill duration
    total_clip_duration = len(scaled_clips) * 10  # estimate ~10s each
    repeats = max(int(target_duration / max(total_clip_duration, 1)) + 1, 1)

    with open(list_file, "w") as f:
        for _ in range(repeats):
            for sc in scaled_clips:
                f.write(f"file '{sc}'\n")

    # Concatenate and trim to target duration
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", list_file,
        "-t", str(target_duration),
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-an", output_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=180)
        return output_path
    except Exception as e:
        print(f"    [WARN] Concatenation failed: {e}")
        return scaled_clips[0] if scaled_clips else ""


def send_to_telegram(video_path: str, brand_id: str, script_text: str):
    """Send completed video to Telegram."""
    import requests

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_REVIEW_CHAT_ID", "")
    if not bot_token or not chat_id:
        print(f"  [!] Telegram not configured, skipping send")
        return False

    caption = f"Brand: {brand_id}\n\n{script_text[:800]}"
    url = f"https://api.telegram.org/bot{bot_token}/sendVideo"

    with open(video_path, "rb") as vf:
        resp = requests.post(
            url,
            data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
            files={"video": (os.path.basename(video_path), vf, "video/mp4")},
            timeout=120,
        )
    if resp.status_code == 200:
        print(f"  [OK] Sent to Telegram")
        return True
    else:
        print(f"  [!] Telegram error: {resp.status_code} {resp.text[:200]}")
        return False

# ---------------------------------------------------------------------------
# Main pipeline (async)
# ---------------------------------------------------------------------------

async def run_brand_pipeline(brand_id: str, topic: str):
    """Run the full content pipeline for one brand."""
    print(f"\n{'='*60}")
    print(f"  BRAND: {brand_id}")
    print(f"  TOPIC: {topic}")
    print(f"{'='*60}")

    brand_config = BRANDS.get(brand_id, {})
    start = time.time()

    # --- Step 1: Script Generation (sync) ---
    print(f"\n  [1/5] Generating script...")
    t0 = time.time()
    db = Database()
    llm = LLMRouter()
    writer = ScriptWriter()
    result = writer.generate_script(
        brand_id=brand_id,
        topic=topic,
        brand_config=brand_config,
        platform="tiktok",
    )
    if not result or not result.get("script_text"):
        print(f"  [FAIL] Script generation failed for {brand_id}")
        return False
    script_text = result["script_text"]
    print(f"  [OK] Script: {len(script_text)} chars, {result.get('word_count', '?')} words ({time.time()-t0:.1f}s)")

    # --- Step 2: TTS / Voiceover (async) ---
    print(f"\n  [2/5] Generating voiceover (Kokoro TTS)...")
    t0 = time.time()
    tts = TTSEngine()
    os.makedirs(f"/app/media/{brand_id}/audio", exist_ok=True)
    tts_result = await tts.generate_voiceover(
        script_text=script_text,
        brand_id=brand_id,
    )
    if not tts_result or not tts_result.audio_path:
        print(f"  [FAIL] TTS failed for {brand_id}")
        return False
    audio_path = tts_result.audio_path
    word_timestamps = tts_result.word_timestamps or []
    vo_duration = tts_result.duration_seconds
    print(f"  [OK] Audio: {audio_path} ({vo_duration:.1f}s) ({time.time()-t0:.1f}s)")

    # --- Step 3: B-Roll — fetch multiple clips with varied searches ---
    print(f"\n  [3/5] Fetching b-roll clips (varied searches)...")
    t0 = time.time()
    broll_fetcher = BRollFetcher(
        db=db,
        rate_limiter=StubRateLimiter(),
        pexels_api_key=os.getenv("PEXELS_API_KEY", ""),
        pixabay_api_key=os.getenv("PIXABAY_API_KEY", ""),
    )

    all_broll_paths = []
    search_terms = BROLL_SEARCHES.get(brand_id, [topic[:30]])
    for search_term in search_terms:
        try:
            clips = await broll_fetcher.fetch_broll(
                brand_id=brand_id,
                theme=search_term,
                duration_seconds=12.0,
                count=1,
            )
            if isinstance(clips, list):
                for p in clips:
                    if p and isinstance(p, str) and os.path.exists(p):
                        all_broll_paths.append(p)
        except Exception as e:
            print(f"    [WARN] B-roll '{search_term}': {e}")

    print(f"  [OK] B-roll: {len(all_broll_paths)} unique clips ({time.time()-t0:.1f}s)")

    # --- Concatenate b-roll into seamless background ---
    target_duration = max(vo_duration + 2.0, 30.0)
    bg_dir = f"/app/media/{brand_id}/temp"
    os.makedirs(bg_dir, exist_ok=True)
    bg_path = os.path.join(bg_dir, "bg_concat.mp4")

    if all_broll_paths:
        print(f"  [3b] Concatenating {len(all_broll_paths)} clips into background...")
        bg_path = concatenate_broll(all_broll_paths, bg_path, target_duration)
        if not bg_path:
            print(f"  [WARN] Concat failed, using solid color fallback")
            bg_path = os.path.join(bg_dir, "bg_solid.mp4")
            subprocess.run([
                "ffmpeg", "-y", "-f", "lavfi",
                "-i", "color=c=0x1a1a2e:s=1080x1920:d=60",
                "-c:v", "libx264", "-t", "60", "-pix_fmt", "yuv420p",
                bg_path
            ], capture_output=True, timeout=30)
    else:
        print(f"  [WARN] No b-roll clips, using solid color background")
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", "color=c=0x1a1a2e:s=1080x1920:d=60",
            "-c:v", "libx264", "-t", "60", "-pix_fmt", "yuv420p",
            bg_path
        ], capture_output=True, timeout=30)

    # --- Step 4: Captions / SRT (sync) ---
    print(f"\n  [4/5] Generating subtitles...")
    t0 = time.time()
    captions_srt = None
    if word_timestamps:
        cap_gen = CaptionGenerator(llm_router=llm)
        cap_result = cap_gen.generate_subtitles(
            word_timestamps=word_timestamps,
            brand_id=brand_id,
        )
        if cap_result and cap_result.get("srt"):
            captions_srt = cap_result["srt"]
            print(f"  [OK] Subtitles: {captions_srt} ({time.time()-t0:.1f}s)")
        else:
            print(f"  [WARN] Caption generation returned no SRT")
    else:
        print(f"  [WARN] No word timestamps from TTS, skipping captions")

    # --- Step 5: Video Assembly (async) ---
    print(f"\n  [5/5] Assembling video...")
    t0 = time.time()
    assembler = VideoAssembler()
    os.makedirs(f"/app/media/{brand_id}/videos", exist_ok=True)

    video_result = await assembler.assemble_video(
        brand_id=brand_id,
        background_path=bg_path,
        voiceover_path=audio_path,
        captions_srt=captions_srt,
    )
    if not video_result or not video_result.get("video_path"):
        print(f"  [FAIL] Video assembly failed for {brand_id}")
        return False

    video_path = video_result["video_path"]
    duration = video_result.get("duration_seconds", "?")
    size_mb = video_result.get("file_size_mb", 0)
    print(f"  [OK] Video: {video_path} ({duration}s, {size_mb:.1f}MB) ({time.time()-t0:.1f}s)")

    # --- Send to Telegram ---
    print(f"\n  Sending to Telegram...")
    send_to_telegram(video_path, brand_id, script_text)

    total = time.time() - start
    print(f"\n  TOTAL TIME: {total:.1f}s")
    print(f"  {'='*60}")
    return True


# ---------------------------------------------------------------------------
# Run all brands
# ---------------------------------------------------------------------------

async def main():
    print("\n" + "#"*60)
    print("  AUTOFARM V6 — FULL PIPELINE TEST (ALL 6 BRANDS)")
    print("#"*60)

    results = {}
    for brand_id, topic in TOPICS.items():
        try:
            success = await run_brand_pipeline(brand_id, topic)
            results[brand_id] = "PASS" if success else "FAIL"
        except Exception as e:
            print(f"\n  [ERROR] {brand_id}: {e}")
            import traceback
            traceback.print_exc()
            results[brand_id] = f"ERROR: {e}"

    # --- Summary ---
    print("\n\n" + "#"*60)
    print("  RESULTS SUMMARY")
    print("#"*60)
    for brand_id, status in results.items():
        icon = "pass" if status == "PASS" else "FAIL"
        print(f"  [{icon}] {brand_id}: {status}")

    passed = sum(1 for s in results.values() if s == "PASS")
    print(f"\n  {passed}/6 brands completed successfully")
    print("#"*60)


if __name__ == "__main__":
    asyncio.run(main())
