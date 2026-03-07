"""
Full pipeline test — generate one video per brand.

Runs: Script → TTS → B-roll → Captions → Video Assembly → Telegram notification
for all 6 brands.
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

# Stub rate limiter for b-roll fetcher
class StubRateLimiter:
    async def acquire(self, *a, **kw):
        return True
    async def check_limit(self, *a, **kw):
        return True

# ---------------------------------------------------------------------------
# Telegram sender
# ---------------------------------------------------------------------------

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
    print(f"  [OK] Audio: {audio_path} ({time.time()-t0:.1f}s)")

    # --- Step 3: B-Roll (async) ---
    print(f"\n  [3/5] Fetching b-roll clips...")
    t0 = time.time()
    broll_fetcher = BRollFetcher(
        db=db,
        rate_limiter=StubRateLimiter(),
        pexels_api_key=os.getenv("PEXELS_API_KEY", ""),
        pixabay_api_key=os.getenv("PIXABAY_API_KEY", ""),
    )
    theme = topic.split(":")[0] if ":" in topic else topic[:40]
    try:
        broll_clips = await broll_fetcher.fetch_broll(
            brand_id=brand_id,
            theme=theme,
            duration_seconds=15.0,
            count=2,
        )
    except Exception as e:
        print(f"  [WARN] B-roll fetch error: {e}")
        broll_clips = []

    # broll_clips is a list of file path strings
    if isinstance(broll_clips, list):
        broll_paths = [p for p in broll_clips if p and isinstance(p, str) and os.path.exists(p)]
    else:
        broll_paths = []
    print(f"  [OK] B-roll: {len(broll_paths)} clips ({time.time()-t0:.1f}s)")

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

    # Create a solid color background as base if no b-roll
    bg_path = f"/app/media/{brand_id}/bg_temp.mp4"
    if not broll_paths:
        subprocess.run([
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", "color=c=0x1a1a2e:s=1080x1920:d=60",
            "-c:v", "libx264", "-t", "60", "-pix_fmt", "yuv420p",
            bg_path
        ], capture_output=True, timeout=30)
    else:
        bg_path = broll_paths[0]

    video_result = await assembler.assemble_video(
        brand_id=brand_id,
        background_path=bg_path,
        voiceover_path=audio_path,
        broll_clips=broll_paths if broll_paths else None,
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
