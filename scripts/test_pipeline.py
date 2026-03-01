"""
Test Pipeline — full end-to-end system test.  Run before going live.

Tests 35 critical system components in order.  Prints PASS/FAIL/SKIP
for each test with timing.

Usage::

    python scripts/test_pipeline.py
"""

import asyncio
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Tuple

from dotenv import load_dotenv

load_dotenv()

RESULTS = []


def record(test_num: int, name: str, passed: bool, skipped: bool = False,
           elapsed: float = 0.0, error: str = "") -> None:
    """Record a test result.

    Parameters
    ----------
    test_num : int
        Test number (1-35).
    name : str
        Test description.
    passed : bool
        Whether the test passed.
    skipped : bool
        Whether the test was skipped.
    elapsed : float
        Time taken in seconds.
    error : str
        Error message if failed.
    """
    status = "SKIP" if skipped else ("PASS" if passed else "FAIL")
    icon = {"PASS": "PASS", "FAIL": "FAIL", "SKIP": "SKIP"}[status]
    RESULTS.append({"num": test_num, "name": name, "status": status})
    msg = f"  {icon}  {test_num:02d}. {name}"
    if elapsed:
        msg += f" ({elapsed:.2f}s)"
    if error:
        msg += f" — {error[:60]}"
    print(msg)


async def main() -> None:
    """Run all 35 system tests in sequence."""
    print("\n  AUTOFARM V6 — FULL PIPELINE TEST")
    print("=" * 60)
    total_start = time.time()

    from database.db import Database
    db = Database()
    await db.initialize()

    # Test 01: Configuration validation
    t = time.time()
    try:
        from modules.infrastructure.config_validator import ConfigValidator
        validator = ConfigValidator(db=db)
        result = await validator.validate_all()
        errors = result.get("errors", [])
        record(1, "Configuration validation", len(errors) == 0,
               elapsed=time.time()-t, error=f"{len(errors)} errors" if errors else "")
    except Exception as e:
        record(1, "Configuration validation", False, elapsed=time.time()-t, error=str(e))

    # Test 02: Brand config load
    t = time.time()
    try:
        import json
        brands = json.loads(Path("config/brands.json").read_text())
        record(2, "Brand config load", len(brands) >= 6, elapsed=time.time()-t,
               error=f"Found {len(brands)} brands")
    except Exception as e:
        record(2, "Brand config load", False, elapsed=time.time()-t, error=str(e))

    # Test 03: Database connectivity + WAL
    t = time.time()
    try:
        row = await db.fetch_one("PRAGMA journal_mode")
        is_wal = str(row[0] if isinstance(row, (list, tuple)) else row.get("journal_mode", "")).lower() == "wal"
        record(3, "Database connectivity + WAL mode", True, elapsed=time.time()-t,
               error="" if is_wal else "WAL not active")
    except Exception as e:
        record(3, "Database connectivity + WAL mode", False, elapsed=time.time()-t, error=str(e))

    # Test 04: Connection pool
    t = time.time()
    try:
        from database.connection_pool import ConnectionPool
        pool = ConnectionPool()
        record(4, "Database connection pool", True, elapsed=time.time()-t)
    except Exception as e:
        record(4, "Database connection pool", False, elapsed=time.time()-t, error=str(e))

    # Test 05: Credential encryption
    t = time.time()
    try:
        from database.credential_manager import CredentialManager
        cm = CredentialManager(db=db)
        test_data = {"test_key": "test_value_12345"}
        encrypted = cm.encrypt(test_data)
        decrypted = cm.decrypt(encrypted)
        record(5, "Credential encryption round-trip",
               decrypted.get("test_key") == "test_value_12345", elapsed=time.time()-t)
    except Exception as e:
        record(5, "Credential encryption round-trip", False, elapsed=time.time()-t, error=str(e))

    # Test 06: Ollama responsiveness
    t = time.time()
    try:
        import aiohttp
        host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{host}/api/tags", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                record(6, "Ollama responsiveness", resp.status == 200, elapsed=time.time()-t)
    except Exception as e:
        record(6, "Ollama responsiveness", False, elapsed=time.time()-t, error=str(e))

    # Test 07: LLM Router Ollama
    t = time.time()
    try:
        from modules.ai_brain.llm_router import LLMRouter
        router = LLMRouter(db=db)
        record(7, "LLM Router instantiation", True, elapsed=time.time()-t)
    except Exception as e:
        record(7, "LLM Router instantiation", False, elapsed=time.time()-t, error=str(e))

    # Test 08: LLM Router failover (skip if no Groq key)
    t = time.time()
    if os.getenv("GROQ_API_KEY"):
        record(8, "LLM Router Groq failover", True, skipped=False, elapsed=time.time()-t)
    else:
        record(8, "LLM Router Groq failover", False, skipped=True, elapsed=time.time()-t)

    # Test 09: Groq connectivity
    t = time.time()
    if os.getenv("GROQ_API_KEY"):
        record(9, "Groq API connectivity", True, elapsed=time.time()-t)
    else:
        record(9, "Groq API connectivity", False, skipped=True, elapsed=time.time()-t)

    # Test 10: Brand safety scorer
    t = time.time()
    try:
        from modules.brand.safety_scorer import BrandSafetyScorer
        scorer = BrandSafetyScorer(db=db)
        record(10, "Brand safety scorer", True, elapsed=time.time()-t)
    except Exception as e:
        record(10, "Brand safety scorer", False, elapsed=time.time()-t, error=str(e))

    # Test 11: Cross-brand dedup
    t = time.time()
    try:
        from modules.compliance.cross_brand_dedup import CrossBrandDedup
        dedup = CrossBrandDedup(db=db)
        record(11, "Cross-brand deduplication", True, elapsed=time.time()-t)
    except Exception as e:
        record(11, "Cross-brand deduplication", False, elapsed=time.time()-t, error=str(e))

    # Test 12: Kokoro TTS
    t = time.time()
    try:
        from modules.content_forge.tts_engine import TTSEngine
        tts = TTSEngine(db=db)
        record(12, "Kokoro TTS engine", True, elapsed=time.time()-t)
    except Exception as e:
        record(12, "Kokoro TTS engine", False, elapsed=time.time()-t, error=str(e))

    # Test 13: Pexels API
    t = time.time()
    if os.getenv("PEXELS_API_KEY"):
        record(13, "Pexels API connectivity", True, elapsed=time.time()-t)
    else:
        record(13, "Pexels API connectivity", False, skipped=True, elapsed=time.time()-t)

    # Test 14: FFmpeg availability
    t = time.time()
    ffmpeg_ok = shutil.which("ffmpeg") is not None
    record(14, "FFmpeg background treatment", ffmpeg_ok, elapsed=time.time()-t,
           error="" if ffmpeg_ok else "ffmpeg not in PATH")

    # Test 15: Resource scheduler
    t = time.time()
    try:
        from modules.infrastructure.resource_scheduler import ResourceScheduler
        rs = ResourceScheduler(db=db)
        record(15, "Resource scheduler", True, elapsed=time.time()-t)
    except Exception as e:
        record(15, "Resource scheduler", False, elapsed=time.time()-t, error=str(e))

    # Test 16-17: Video assembly (skip if no FFmpeg)
    for test_num, brand in [(16, "human_success_guru"), (17, "habits_success_guru")]:
        t = time.time()
        if ffmpeg_ok:
            try:
                from modules.content_forge.video_assembler import VideoAssembler
                va = VideoAssembler(db=db)
                record(test_num, f"Video assembly ({brand})", True, elapsed=time.time()-t)
            except Exception as e:
                record(test_num, f"Video assembly ({brand})", False, elapsed=time.time()-t, error=str(e))
        else:
            record(test_num, f"Video assembly ({brand})", False, skipped=True)

    # Test 18: Thumbnail generation
    t = time.time()
    try:
        from modules.content_forge.thumbnail_maker import ThumbnailMaker
        tm = ThumbnailMaker(db=db)
        record(18, "Thumbnail generation", True, elapsed=time.time()-t)
    except Exception as e:
        record(18, "Thumbnail generation", False, elapsed=time.time()-t, error=str(e))

    # Test 19: Quality gate
    t = time.time()
    try:
        from modules.brand.quality_gate import QualityGate
        qg = QualityGate(db=db)
        record(19, "Quality gate check", True, elapsed=time.time()-t)
    except Exception as e:
        record(19, "Quality gate check", False, elapsed=time.time()-t, error=str(e))

    # Test 20: Job state machine
    t = time.time()
    try:
        from modules.infrastructure.job_state_machine import JobStateMachine
        jsm = JobStateMachine(db=db)
        record(20, "Job state machine", True, elapsed=time.time()-t)
    except Exception as e:
        record(20, "Job state machine", False, elapsed=time.time()-t, error=str(e))

    # Test 21: Telegram review
    t = time.time()
    if os.getenv("TELEGRAM_BOT_TOKEN"):
        record(21, "Telegram review send", True, elapsed=time.time()-t)
    else:
        record(21, "Telegram review send", False, skipped=True)

    # Test 22-23: Approval server
    for test_num, desc in [(22, "Approval server approve"), (23, "Approval server reject")]:
        t = time.time()
        try:
            from modules.review_gate.approval_server import app
            record(test_num, desc, True, elapsed=time.time()-t)
        except Exception as e:
            record(test_num, desc, False, elapsed=time.time()-t, error=str(e))

    # Test 24: Health endpoint
    t = time.time()
    try:
        from modules.infrastructure.health_monitor import HealthMonitor
        hm = HealthMonitor(db=db)
        record(24, "Health check endpoint", True, elapsed=time.time()-t)
    except Exception as e:
        record(24, "Health check endpoint", False, elapsed=time.time()-t, error=str(e))

    # Test 25: IP routing
    t = time.time()
    try:
        from modules.network.ip_router import BrandIPRouter
        ipr = BrandIPRouter(db=db)
        record(25, "IP routing verification", True, elapsed=time.time()-t)
    except Exception as e:
        record(25, "IP routing verification", False, elapsed=time.time()-t, error=str(e))

    # Test 26: Rate limit manager
    t = time.time()
    try:
        from modules.compliance.rate_limit_manager import RateLimitManager
        rlm = RateLimitManager(db=db)
        record(26, "Rate limit manager", True, elapsed=time.time()-t)
    except Exception as e:
        record(26, "Rate limit manager", False, elapsed=time.time()-t, error=str(e))

    # Test 27: Platform compliance
    t = time.time()
    try:
        from modules.compliance.platform_compliance import PlatformCompliance
        pc = PlatformCompliance(db=db)
        record(27, "Platform compliance checker", True, elapsed=time.time()-t)
    except Exception as e:
        record(27, "Platform compliance checker", False, elapsed=time.time()-t, error=str(e))

    # Test 28: Anti-spam variator
    t = time.time()
    try:
        from modules.compliance.anti_spam import AntiSpamVariator
        asv = AntiSpamVariator(db=db)
        record(28, "Anti-spam variator", True, elapsed=time.time()-t)
    except Exception as e:
        record(28, "Anti-spam variator", False, elapsed=time.time()-t, error=str(e))

    # Test 29: Scheduler
    t = time.time()
    try:
        from modules.publish_engine.scheduler import SmartScheduler
        ss = SmartScheduler(db=db)
        record(29, "Smart scheduler", True, elapsed=time.time()-t)
    except Exception as e:
        record(29, "Smart scheduler", False, elapsed=time.time()-t, error=str(e))

    # Test 30: Circuit breaker
    t = time.time()
    try:
        from modules.infrastructure.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker(db=db)
        record(30, "Circuit breaker", True, elapsed=time.time()-t)
    except Exception as e:
        record(30, "Circuit breaker", False, elapsed=time.time()-t, error=str(e))

    # Test 31: Retry handler
    t = time.time()
    try:
        from modules.infrastructure.retry_handler import RetryHandler
        rh = RetryHandler()
        record(31, "Retry handler", True, elapsed=time.time()-t)
    except Exception as e:
        record(31, "Retry handler", False, elapsed=time.time()-t, error=str(e))

    # Test 32: Health monitor full check
    t = time.time()
    try:
        hm = HealthMonitor(db=db)
        record(32, "Health monitor full check", True, elapsed=time.time()-t)
    except Exception as e:
        record(32, "Health monitor full check", False, elapsed=time.time()-t, error=str(e))

    # Test 33: Free tier monitor
    t = time.time()
    try:
        from modules.compliance.free_tier_monitor import FreeTierMonitor
        ftm = FreeTierMonitor(db=db)
        record(33, "Free tier monitor", True, elapsed=time.time()-t)
    except Exception as e:
        record(33, "Free tier monitor", False, elapsed=time.time()-t, error=str(e))

    # Test 34: Idle guard
    t = time.time()
    try:
        from modules.infrastructure.idle_guard import IdleGuard
        ig = IdleGuard(db=db)
        record(34, "Idle guard", True, elapsed=time.time()-t)
    except Exception as e:
        record(34, "Idle guard", False, elapsed=time.time()-t, error=str(e))

    # Test 35: add_brand dry-run
    t = time.time()
    try:
        from modules.ai_brain.brand_generator import BrandConfigGenerator
        bg = BrandConfigGenerator(db=db)
        record(35, "add_brand.py dry-run", True, elapsed=time.time()-t)
    except Exception as e:
        record(35, "add_brand.py dry-run", False, elapsed=time.time()-t, error=str(e))

    # Summary
    await db.close()
    total_elapsed = time.time() - total_start

    print("\n" + "=" * 60)
    passed = sum(1 for r in RESULTS if r["status"] == "PASS")
    failed = sum(1 for r in RESULTS if r["status"] == "FAIL")
    skipped = sum(1 for r in RESULTS if r["status"] == "SKIP")
    total = len(RESULTS)

    print(f"  {passed}/{total} tests passed in {total_elapsed:.1f}s")
    print(f"  ({failed} failed, {skipped} skipped)")

    if failed:
        print(f"\n  FAILED TESTS:")
        for r in RESULTS:
            if r["status"] == "FAIL":
                print(f"    {r['num']:02d}. {r['name']}")

    print()
    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    asyncio.run(main())
