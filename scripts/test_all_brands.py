"""
Full pipeline test — generate one video per brand.

Runs: Script → TTS → B-roll → Captions → Video Assembly → Telegram notification
for all 6 brands. B-roll clips are fetched directly from Pexels with varied
search terms and concatenated into a seamless background.
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
    "wealth_success_guru": ["money growth", "stock market trading", "luxury car", "business office", "gold coins wealth"],
    "zen_success_guru": ["meditation peaceful", "zen garden stones", "mountain sunrise", "calm ocean waves", "forest path nature"],
    "social_success_guru": ["people conversation", "body language", "confident public speaker", "networking event", "eye contact close"],
    "habits_success_guru": ["morning routine workout", "writing in journal", "alarm clock sunrise", "healthy food prep", "running exercise"],
    "relationships_success_guru": ["couple talking sunset", "emotional connection", "family dinner together", "friends laughing", "deep conversation cafe"],
}


# ---------------------------------------------------------------------------
# Direct Pexels fetcher (bypasses cache for unique clips)
# ---------------------------------------------------------------------------

def fetch_pexels_clip(query: str, brand_id: str, clip_index: int) -> str:
    """Fetch a single clip directly from Pexels API. Returns local file path."""
    import requests

    api_key = os.getenv("PEXELS_API_KEY", "")
    if not api_key:
        return ""

    headers = {"Authorization": api_key}
    params = {
        "query": query,
        "orientation": "portrait",
        "per_page": 5,
        "page": 1,
    }

    try:
        resp = requests.get(
            "https://api.pexels.com/videos/search",
            headers=headers, params=params, timeout=15,
        )
        if resp.status_code != 200:
            print(f"    [WARN] Pexels API error {resp.status_code} for '{query}'")
            return ""

        data = resp.json()
        videos = data.get("videos", [])
        if not videos:
            print(f"    [WARN] No Pexels results for '{query}'")
            return ""

        # Pick a random video from results to add variety
        import random
        video = random.choice(videos)
        video_files = video.get("video_files", [])

        # Pick best quality portrait file (prefer HD)
        best = None
        for vf in video_files:
            w = vf.get("width", 0)
            h = vf.get("height", 0)
            if h > w:  # portrait
                if best is None or vf.get("height", 0) > best.get("height", 0):
                    best = vf
        if not best and video_files:
            best = video_files[0]

        if not best or not best.get("link"):
            return ""

        # Download
        dl_dir = f"/app/media/{brand_id}/broll_fresh"
        os.makedirs(dl_dir, exist_ok=True)
        out_path = os.path.join(dl_dir, f"clip_{clip_index}_{video['id']}.mp4")

        if os.path.exists(out_path):
            return out_path

        print(f"    Downloading: '{query}' → {video['id']} ({best.get('width')}x{best.get('height')})")
        dl_resp = requests.get(best["link"], timeout=60, stream=True)
        if dl_resp.status_code == 200:
            with open(out_path, "wb") as f:
                for chunk in dl_resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            return out_path
        return ""

    except Exception as e:
        print(f"    [WARN] Pexels fetch error for '{query}': {e}")
        return ""


def concatenate_broll(clip_paths: list, output_path: str, target_duration: float) -> str:
    """Concatenate multiple b-roll clips into one seamless background video."""
    if not clip_paths:
        return ""

    list_dir = os.path.dirname(output_path)
    os.makedirs(list_dir, exist_ok=True)

    # Scale each clip to 1080x1920 portrait
    scaled_clips = []
    for i, clip in enumerate(clip_paths):
        scaled = os.path.join(list_dir, f"scaled_{i}.mp4")
        cmd = [
            "ffmpeg", "-y", "-i", clip,
            "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,"
                   "crop=1080:1920,setsar=1",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-r", "30", "-an", "-t", "12",
            scaled,
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=180)
            if os.path.exists(scaled) and os.path.getsize(scaled) > 0:
                scaled_clips.append(scaled)
        except Exception as e:
            print(f"    [WARN] Failed to scale clip {i}: {e}")

    if not scaled_clips:
        return ""

    # Write concat list
    list_file = os.path.join(list_dir, "concat_list.txt")
    with open(list_file, "w") as f:
        for sc in scaled_clips:
            f.write(f"file '{sc}'\n")

    # Concatenate
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
    except subprocess.CalledProcessError as e:
        print(f"    [WARN] Concat failed: {e.stderr[:300] if e.stderr else e}")
        return scaled_clips[0] if scaled_clips else ""


def compress_for_telegram(video_path: str, max_size_mb: float = 45.0) -> str:
    """Compress video to fit Telegram's 50MB limit."""
    size_mb = os.path.getsize(video_path) / (1024 * 1024)
    if size_mb <= max_size_mb:
        return video_path

    print(f"  [compress] {size_mb:.0f}MB > {max_size_mb:.0f}MB, compressing...")
    compressed = video_path.replace(".mp4", "_tg.mp4")
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vf", "scale=720:-2",
        "-c:v", "libx264", "-crf", "30", "-preset", "fast",
        "-c:a", "aac", "-b:a", "96k",
        "-movflags", "+faststart",
        compressed,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)
        new_size = os.path.getsize(compressed) / (1024 * 1024)
        print(f"  [compress] Done: {new_size:.1f}MB")
        return compressed
    except Exception as e:
        print(f"  [WARN] Compression failed: {e}")
        return video_path


def send_to_telegram(video_path: str, brand_id: str, script_text: str):
    """Send completed video to Telegram."""
    import requests

    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_REVIEW_CHAT_ID", "")
    if not bot_token or not chat_id:
        print(f"  [!] Telegram not configured, skipping send")
        return False

    # Compress if over 45MB
    send_path = compress_for_telegram(video_path)

    caption = f"Brand: {brand_id}\n\n{script_text[:800]}"
    url = f"https://api.telegram.org/bot{bot_token}/sendVideo"

    try:
        with open(send_path, "rb") as vf:
            resp = requests.post(
                url,
                data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
                files={"video": (os.path.basename(send_path), vf, "video/mp4")},
                timeout=180,
            )
        if resp.status_code == 200:
            print(f"  [OK] Sent to Telegram")
            return True
        else:
            print(f"  [!] Telegram error: {resp.status_code} {resp.text[:200]}")
            return False
    except Exception as e:
        print(f"  [!] Telegram send failed: {e}")
        return False
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

    # --- Step 3: B-Roll — fetch directly from Pexels (no cache) ---
    print(f"\n  [3/5] Fetching b-roll clips (5 unique searches)...")
    t0 = time.time()

    # Clear previous fresh clips for this brand
    fresh_dir = f"/app/media/{brand_id}/broll_fresh"
    if os.path.exists(fresh_dir):
        for f in os.listdir(fresh_dir):
            if f.startswith("clip_") or f.startswith("scaled_"):
                os.remove(os.path.join(fresh_dir, f))

    all_broll_paths = []
    search_terms = BROLL_SEARCHES.get(brand_id, [topic[:30]])
    for i, search_term in enumerate(search_terms):
        path = fetch_pexels_clip(search_term, brand_id, i)
        if path and os.path.exists(path):
            all_broll_paths.append(path)

    print(f"  [OK] B-roll: {len(all_broll_paths)} unique clips ({time.time()-t0:.1f}s)")

    # --- Concatenate b-roll into seamless background ---
    target_duration = max(vo_duration + 2.0, 30.0)
    bg_dir = f"/app/media/{brand_id}/temp"
    os.makedirs(bg_dir, exist_ok=True)
    bg_path = os.path.join(bg_dir, "bg_concat.mp4")

    if all_broll_paths:
        print(f"  [3b] Concatenating {len(all_broll_paths)} clips → {target_duration:.0f}s background...")
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
        bg_path = os.path.join(bg_dir, "bg_solid.mp4")
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
