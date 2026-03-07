"""
Pre-build branded background videos for all 6 brands.

Creates 10 themed backgrounds per brand, each concatenated from 5 unique
Pexels clips. Stores metadata (theme, keywords) alongside each background
for intelligent matching against script content.

Run once to populate the cache. Pexels usage: ~300 API calls total
(10 backgrounds × 5 clips × 6 brands = 300, spread across time to stay
within 200/hour free limit).

Usage:
    PYTHONPATH=/app python scripts/prebuild_backgrounds.py
    PYTHONPATH=/app python scripts/prebuild_backgrounds.py --brand zen_success_guru
    PYTHONPATH=/app python scripts/prebuild_backgrounds.py --brand zen_success_guru --index 3
"""

import json
import os
import random
import subprocess
import sys
import time
import argparse

from dotenv import load_dotenv
load_dotenv("/app/.env")

sys.path.insert(0, "/app")

# ---------------------------------------------------------------------------
# Theme definitions — 10 themes per brand, each with 5 search terms
# ---------------------------------------------------------------------------

BRAND_THEMES = {
    "human_success_guru": [
        {"name": "dark_city", "keywords": ["ambition", "power", "drive", "hustle"], "searches": ["dark city night", "neon city lights", "urban night walk", "city rain reflection", "skyscraper night"]},
        {"name": "chess_strategy", "keywords": ["strategy", "intelligence", "mind", "thinking"], "searches": ["chess pieces close", "chess game dramatic", "strategy board", "thinking man", "puzzle solving"]},
        {"name": "solitary_path", "keywords": ["alone", "journey", "self", "path"], "searches": ["person walking alone", "empty road ahead", "fog path morning", "lone figure sunset", "desert walk"]},
        {"name": "storm_power", "keywords": ["storm", "power", "strength", "overcome"], "searches": ["storm clouds dramatic", "lightning sky", "ocean storm waves", "powerful waterfall", "tornado sky"]},
        {"name": "dark_motivation", "keywords": ["motivation", "grind", "success", "work"], "searches": ["gym workout intense", "boxing training", "running athlete", "weight lifting", "discipline training"]},
        {"name": "shadow_mind", "keywords": ["psychology", "mind", "dark", "subconscious"], "searches": ["shadow silhouette", "dark corridor", "mirror reflection face", "abstract dark art", "smoke mystery"]},
        {"name": "fire_ambition", "keywords": ["fire", "passion", "burn", "desire"], "searches": ["fire flames burning", "campfire night", "candle flame dark", "sparks flying metal", "volcano lava"]},
        {"name": "library_wisdom", "keywords": ["knowledge", "learn", "wisdom", "read"], "searches": ["library books old", "reading focus", "ancient books", "writing desk", "wisdom scroll"]},
        {"name": "mountain_peak", "keywords": ["peak", "summit", "achievement", "top"], "searches": ["mountain peak clouds", "cliff edge view", "summit sunrise", "rock climbing", "mountain fog"]},
        {"name": "time_clock", "keywords": ["time", "wasted", "urgency", "now"], "searches": ["clock time passing", "hourglass sand", "sunrise timelapse", "calendar flipping", "watch closeup"]},
    ],
    "wealth_success_guru": [
        {"name": "stock_market", "keywords": ["stocks", "investing", "market", "trading"], "searches": ["stock market screen", "trading charts", "wall street", "financial data", "stock exchange"]},
        {"name": "gold_wealth", "keywords": ["gold", "money", "wealth", "rich"], "searches": ["gold coins stack", "gold bars vault", "treasure chest", "golden luxury", "money cash pile"]},
        {"name": "luxury_life", "keywords": ["luxury", "lifestyle", "success", "premium"], "searches": ["luxury car driving", "mansion estate", "yacht ocean", "private jet", "penthouse view"]},
        {"name": "business_office", "keywords": ["business", "corporate", "meeting", "deal"], "searches": ["business meeting room", "office skyline view", "handshake deal", "conference table", "corporate tower"]},
        {"name": "real_estate", "keywords": ["property", "real estate", "investment", "building"], "searches": ["modern architecture", "real estate luxury", "building construction", "city development", "house interior design"]},
        {"name": "compound_growth", "keywords": ["compound", "growth", "compound effect", "small"], "searches": ["plant growing timelapse", "seed sprouting", "tree growth rings", "stacking blocks", "snowball rolling"]},
        {"name": "entrepreneur", "keywords": ["entrepreneur", "startup", "hustle", "grind"], "searches": ["entrepreneur working late", "startup office", "coding laptop night", "brainstorming whiteboard", "coffee morning work"]},
        {"name": "global_finance", "keywords": ["global", "economy", "world", "international"], "searches": ["world map digital", "globe spinning", "international city", "airplane flying", "cargo ship port"]},
        {"name": "savings_invest", "keywords": ["savings", "invest", "budget", "plan"], "searches": ["piggy bank savings", "calculator budget", "jar coins saving", "notebook financial", "bank vault"]},
        {"name": "diamond_value", "keywords": ["value", "diamond", "premium", "quality"], "searches": ["diamond closeup", "jewelry luxury", "watch expensive", "suit tailored", "leather briefcase"]},
    ],
    "zen_success_guru": [
        {"name": "meditation", "keywords": ["meditation", "peace", "calm", "mindful"], "searches": ["meditation peaceful room", "zen meditation garden", "candle meditation", "peaceful morning light", "yoga meditation pose"]},
        {"name": "zen_garden", "keywords": ["zen", "balance", "harmony", "garden"], "searches": ["zen garden stones", "rock garden raking", "bonsai tree", "bamboo forest", "japanese garden"]},
        {"name": "mountain_peace", "keywords": ["mountain", "nature", "serenity", "stillness"], "searches": ["mountain sunrise peaceful", "misty mountains", "alpine lake reflection", "snow mountain calm", "hilltop meditation"]},
        {"name": "ocean_calm", "keywords": ["ocean", "waves", "water", "flow"], "searches": ["calm ocean sunset", "gentle waves beach", "underwater peaceful", "lake still morning", "river flowing gentle"]},
        {"name": "forest_path", "keywords": ["forest", "path", "nature", "walk"], "searches": ["forest path sunlight", "autumn leaves falling", "woodland trail", "moss covered trees", "rain forest green"]},
        {"name": "sunrise_new", "keywords": ["sunrise", "morning", "new", "begin"], "searches": ["sunrise golden hour", "dawn sky colors", "morning dew drops", "first light horizon", "sun rays through clouds"]},
        {"name": "stoic_marble", "keywords": ["stoic", "philosophy", "ancient", "wisdom"], "searches": ["marble statue greek", "ancient columns ruins", "old book pages", "philosophy library", "candle writing desk"]},
        {"name": "rain_peace", "keywords": ["rain", "peace", "cleanse", "renew"], "searches": ["rain window peaceful", "raindrop leaves", "rain puddle reflection", "storm passing clear", "rainbow after rain"]},
        {"name": "stars_cosmos", "keywords": ["stars", "universe", "cosmos", "perspective"], "searches": ["night sky stars", "milky way galaxy", "moon peaceful night", "stargazing person", "aurora borealis"]},
        {"name": "tea_ceremony", "keywords": ["tea", "ritual", "presence", "moment"], "searches": ["tea ceremony japanese", "herbal tea pouring", "coffee morning ritual", "warm drink hands", "steaming cup close"]},
    ],
    "social_success_guru": [
        {"name": "conversation", "keywords": ["conversation", "talk", "communicate", "speak"], "searches": ["people talking cafe", "deep conversation", "friends laughing", "dinner party talk", "coffee chat"]},
        {"name": "body_language", "keywords": ["body language", "posture", "gestures", "nonverbal"], "searches": ["confident body language", "hand gestures speaker", "eye contact close", "posture standing tall", "handshake firm"]},
        {"name": "public_speaking", "keywords": ["speaking", "presentation", "audience", "stage"], "searches": ["public speaker stage", "ted talk audience", "microphone speaker", "conference presentation", "crowd listening"]},
        {"name": "networking", "keywords": ["networking", "social", "connect", "people"], "searches": ["networking event", "business cards exchange", "social gathering", "party mingling", "group discussion"]},
        {"name": "leadership", "keywords": ["leadership", "lead", "influence", "guide"], "searches": ["team leader meeting", "pointing direction", "mentor teaching", "coaching session", "guiding team"]},
        {"name": "crowd_energy", "keywords": ["crowd", "energy", "social proof", "viral"], "searches": ["crowd cheering", "concert audience", "sports fans", "street festival", "celebration people"]},
        {"name": "mirror_practice", "keywords": ["mirror", "practice", "self", "confidence"], "searches": ["mirror reflection person", "practice speech", "rehearsal room", "actor preparing", "getting ready confident"]},
        {"name": "city_social", "keywords": ["city", "urban", "social", "dynamic"], "searches": ["busy city sidewalk", "cafe street people", "urban lifestyle", "subway commute", "city park gathering"]},
        {"name": "interview", "keywords": ["interview", "impression", "first", "professional"], "searches": ["job interview room", "professional meeting", "boardroom discussion", "negotiation table", "office handshake"]},
        {"name": "eye_contact", "keywords": ["eyes", "contact", "read", "understand"], "searches": ["eyes closeup intense", "face expressions", "emotional face", "portrait dramatic", "contemplative look"]},
    ],
    "habits_success_guru": [
        {"name": "morning_routine", "keywords": ["morning", "routine", "wake", "start"], "searches": ["morning sunrise bedroom", "alarm clock wakeup", "stretching morning", "journaling morning", "breakfast preparation"]},
        {"name": "workout", "keywords": ["exercise", "workout", "gym", "fitness"], "searches": ["gym workout routine", "running outdoors", "pushups exercise", "yoga stretch", "jump rope training"]},
        {"name": "journaling", "keywords": ["journal", "write", "plan", "reflect"], "searches": ["writing journal notebook", "pen paper closeup", "planner daily", "gratitude writing", "desk organized"]},
        {"name": "healthy_food", "keywords": ["food", "healthy", "nutrition", "diet"], "searches": ["healthy meal prep", "fruit vegetables fresh", "smoothie blending", "cooking healthy", "salad preparation"]},
        {"name": "reading_habit", "keywords": ["reading", "books", "learn", "study"], "searches": ["reading book morning", "library study", "bookshelf organized", "kindle reading", "study desk lamp"]},
        {"name": "cold_shower", "keywords": ["discipline", "cold", "discomfort", "growth"], "searches": ["cold water splash", "shower water drops", "ice bath", "waterfall cold", "rain face"]},
        {"name": "sleep_rest", "keywords": ["sleep", "rest", "recovery", "night"], "searches": ["bedroom night peaceful", "sleeping calm", "moon night window", "pillow bed cozy", "dimmed lights evening"]},
        {"name": "productivity", "keywords": ["productive", "focus", "deep work", "flow"], "searches": ["laptop focus work", "timer pomodoro", "clean desk minimal", "typing keyboard", "deep focus coding"]},
        {"name": "nature_walk", "keywords": ["walk", "nature", "fresh", "outside"], "searches": ["walking nature trail", "park morning walk", "beach barefoot walk", "countryside path", "hiking boots trail"]},
        {"name": "meditation_habit", "keywords": ["meditate", "breathe", "mindful", "calm"], "searches": ["breathing exercise", "meditation cushion", "peaceful sitting", "incense burning", "calm room minimal"]},
    ],
    "relationships_success_guru": [
        {"name": "couple_talk", "keywords": ["couple", "communication", "partner", "love"], "searches": ["couple talking sunset", "holding hands walk", "couple coffee date", "romantic dinner", "couple laughing"]},
        {"name": "emotional", "keywords": ["emotional", "feelings", "vulnerable", "connect"], "searches": ["emotional moment", "tears joy", "hugging comfort", "empathy listening", "deep look eyes"]},
        {"name": "family_bond", "keywords": ["family", "bond", "together", "support"], "searches": ["family dinner table", "parent child moment", "family walk park", "generations together", "home cozy family"]},
        {"name": "friendship", "keywords": ["friend", "trust", "loyalty", "social"], "searches": ["friends laughing together", "group friends outdoor", "best friends cafe", "friendship support", "high five celebration"]},
        {"name": "deep_convo", "keywords": ["deep", "conversation", "understand", "listen"], "searches": ["deep conversation cafe", "listening intently", "two people talking", "bench park talking", "fireplace discussion"]},
        {"name": "heartbreak", "keywords": ["heartbreak", "pain", "loss", "heal"], "searches": ["rain window alone", "empty chair", "walking away sunset", "lonely bench park", "torn photo"]},
        {"name": "wedding_love", "keywords": ["wedding", "commitment", "forever", "vow"], "searches": ["wedding rings close", "wedding dance", "flower bouquet romantic", "champagne celebration", "sunset couple beach"]},
        {"name": "self_love", "keywords": ["self", "worth", "boundaries", "respect"], "searches": ["mirror self care", "spa relaxation", "journaling self", "walking confident alone", "peaceful solo morning"]},
        {"name": "conflict", "keywords": ["conflict", "argument", "resolve", "fight"], "searches": ["arms crossed angry", "looking away couple", "separated distance", "tension dramatic", "storm approaching"]},
        {"name": "growth_together", "keywords": ["growth", "together", "evolve", "team"], "searches": ["planting garden couple", "cooking together", "travel adventure couple", "building project", "sunrise together"]},
    ],
}

# ---------------------------------------------------------------------------
# Pexels fetcher
# ---------------------------------------------------------------------------

def fetch_pexels_clip(query: str, out_path: str) -> bool:
    """Fetch a single clip from Pexels. Returns True if successful."""
    import requests as req

    api_key = os.getenv("PEXELS_API_KEY", "")
    if not api_key:
        print(f"    [ERROR] No PEXELS_API_KEY set")
        return False

    headers = {"Authorization": api_key}
    params = {"query": query, "orientation": "portrait", "per_page": 5}

    try:
        resp = req.get("https://api.pexels.com/videos/search",
                       headers=headers, params=params, timeout=15)
        if resp.status_code != 200:
            print(f"    [WARN] Pexels API {resp.status_code} for '{query}'")
            return False

        videos = resp.json().get("videos", [])
        if not videos:
            print(f"    [WARN] No results for '{query}'")
            return False

        video = random.choice(videos)
        video_files = video.get("video_files", [])

        # Prefer portrait HD
        best = None
        for vf in video_files:
            if vf.get("height", 0) > vf.get("width", 0):
                if best is None or vf.get("height", 0) > best.get("height", 0):
                    best = vf
        if not best and video_files:
            best = video_files[0]
        if not best or not best.get("link"):
            return False

        dl_resp = req.get(best["link"], timeout=60, stream=True)
        if dl_resp.status_code == 200:
            with open(out_path, "wb") as f:
                for chunk in dl_resp.iter_content(8192):
                    f.write(chunk)
            return True
        return False

    except Exception as e:
        print(f"    [WARN] Pexels error for '{query}': {e}")
        return False


def scale_clip(input_path: str, output_path: str) -> bool:
    """Scale a clip to 1080x1920 portrait (crop-to-fill)."""
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,"
               "crop=1080:1920,setsar=1",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-r", "30", "-an", "-t", "12",
        output_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=180)
        return os.path.exists(output_path) and os.path.getsize(output_path) > 0
    except Exception as e:
        print(f"    [WARN] Scale failed: {e}")
        return False


def concatenate_clips(clip_paths: list, output_path: str, target_duration: float = 65.0) -> bool:
    """Concatenate scaled clips into one background video."""
    list_dir = os.path.dirname(output_path)
    list_file = os.path.join(list_dir, "concat_list.txt")

    with open(list_file, "w") as f:
        for clip in clip_paths:
            f.write(f"file '{clip}'\n")

    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", list_file,
        "-t", str(target_duration),
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-an", output_path,
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=180)
        return os.path.exists(output_path)
    except Exception as e:
        print(f"    [WARN] Concat failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_background(brand_id: str, theme_index: int, theme: dict) -> bool:
    """Build one themed background for a brand."""
    bg_dir = f"/app/media/{brand_id}/backgrounds"
    raw_dir = f"/app/media/{brand_id}/backgrounds/raw"
    os.makedirs(bg_dir, exist_ok=True)
    os.makedirs(raw_dir, exist_ok=True)

    output_path = os.path.join(bg_dir, f"bg_{theme_index:02d}_{theme['name']}.mp4")
    meta_path = os.path.join(bg_dir, f"bg_{theme_index:02d}_{theme['name']}.json")

    # Skip if already built
    if os.path.exists(output_path) and os.path.exists(meta_path):
        print(f"  [SKIP] {brand_id} bg_{theme_index:02d}_{theme['name']} already exists")
        return True

    print(f"\n  Building: {brand_id} / {theme['name']} ({theme_index+1}/10)")

    # Step 1: Download 5 clips
    raw_clips = []
    for i, search in enumerate(theme["searches"]):
        raw_path = os.path.join(raw_dir, f"{theme['name']}_{i}.mp4")
        if os.path.exists(raw_path):
            raw_clips.append(raw_path)
            print(f"    [cached] {search}")
            continue

        print(f"    Fetching: '{search}'...")
        if fetch_pexels_clip(search, raw_path):
            raw_clips.append(raw_path)
        else:
            print(f"    [MISS] Couldn't fetch '{search}'")

        # Rate limit: stay within 200/hour (1 every 18s to be safe)
        time.sleep(1)

    if len(raw_clips) < 3:
        print(f"  [FAIL] Only got {len(raw_clips)}/5 clips, need at least 3")
        return False

    # Step 2: Scale each clip
    scaled_clips = []
    for i, clip in enumerate(raw_clips):
        scaled = os.path.join(raw_dir, f"{theme['name']}_scaled_{i}.mp4")
        if os.path.exists(scaled) and os.path.getsize(scaled) > 0:
            scaled_clips.append(scaled)
            continue
        print(f"    Scaling clip {i+1}/{len(raw_clips)}...")
        if scale_clip(clip, scaled):
            scaled_clips.append(scaled)

    if not scaled_clips:
        print(f"  [FAIL] No clips scaled successfully")
        return False

    # Step 3: Concatenate
    print(f"    Concatenating {len(scaled_clips)} clips...")
    if not concatenate_clips(scaled_clips, output_path):
        return False

    # Step 4: Save metadata
    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    meta = {
        "brand_id": brand_id,
        "theme": theme["name"],
        "keywords": theme["keywords"],
        "searches": theme["searches"],
        "clip_count": len(scaled_clips),
        "size_mb": round(size_mb, 1),
        "index": theme_index,
    }
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)

    print(f"  [OK] {output_path} ({size_mb:.1f}MB)")
    return True


def build_brand(brand_id: str, single_index: int = None):
    """Build all backgrounds for one brand."""
    themes = BRAND_THEMES.get(brand_id, [])
    if not themes:
        print(f"[ERROR] No themes defined for {brand_id}")
        return

    print(f"\n{'='*60}")
    print(f"  BUILDING BACKGROUNDS: {brand_id}")
    print(f"{'='*60}")

    success = 0
    for i, theme in enumerate(themes):
        if single_index is not None and i != single_index:
            continue
        if build_background(brand_id, i, theme):
            success += 1

    total = 1 if single_index is not None else len(themes)
    print(f"\n  {brand_id}: {success}/{total} backgrounds built")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pre-build branded backgrounds")
    parser.add_argument("--brand", help="Build for specific brand only")
    parser.add_argument("--index", type=int, help="Build specific theme index only (0-9)")
    args = parser.parse_args()

    print("\n" + "#" * 60)
    print("  AUTOFARM — BACKGROUND PRE-BUILDER")
    print("#" * 60)

    if args.brand:
        build_brand(args.brand, args.index)
    else:
        for brand_id in BRAND_THEMES:
            build_brand(brand_id)

    print("\n" + "#" * 60)
    print("  DONE")
    print("#" * 60)
