"""
Install Kokoro TTS — downloads voice models for all 6 brand personas.

Kokoro requires ``espeak-ng`` to be installed first (via apt).
Voice models are stored in the Kokoro default cache directory.

Usage::

    python scripts/install_kokoro.py
"""

import sys


def main() -> None:
    """Download and verify Kokoro TTS voice models.

    Side Effects
    ------------
    Downloads voice model files to Kokoro's cache directory.
    """
    print("\n  INSTALLING KOKORO TTS VOICES")
    print("=" * 50)

    # Brand voice model IDs (Kokoro voice names)
    BRAND_VOICES = {
        "human_success_guru": "af_heart",
        "wealth_success_guru": "am_adam",
        "zen_success_guru": "af_sky",
        "social_success_guru": "af_bella",
        "habits_success_guru": "am_michael",
        "relationships_success_guru": "af_sarah",
    }

    try:
        import kokoro
        print(f"  PASS  Kokoro {kokoro.__version__} installed")
    except ImportError:
        print("  FAIL  Kokoro not installed")
        print("  Run: pip install kokoro soundfile")
        sys.exit(1)

    # Verify espeak-ng
    import shutil
    if not shutil.which("espeak-ng"):
        print("  FAIL  espeak-ng not found")
        print("  Run: sudo apt install espeak-ng")
        sys.exit(1)
    print(f"  PASS  espeak-ng available")

    # Test each voice
    for brand_id, voice_name in BRAND_VOICES.items():
        try:
            # Attempt to load the voice model (triggers download)
            from kokoro import KPipeline
            pipeline = KPipeline(lang_code="a", voice=voice_name)

            # Generate a short test
            test_text = "Test voice generation."
            results = list(pipeline(test_text))

            if results:
                print(f"  PASS  {brand_id} -> {voice_name}")
            else:
                print(f"  WARN  {brand_id} -> {voice_name} (no output)")

        except Exception as exc:
            print(f"  FAIL  {brand_id} -> {voice_name}: {exc}")

    print("\nKokoro TTS setup complete.")


if __name__ == "__main__":
    main()
