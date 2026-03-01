# CLAUDE.md — AutoFarm V6 Build Controller

## MASTER SPECIFICATION
Read `AUTOFARM_V6_IMPROVED.md` in this directory. It is the SOLE source of truth for the entire system — all code, architecture, config, and logic lives there. Every file you create must implement what that document specifies.

## YOUR TASK
Build the complete AutoFarm V6 codebase by following Part 21 (Final Build Order) exactly. This is a large build (~100+ files) that will span multiple sessions due to rate limits.

## CRITICAL: BEFORE WRITING ANY CODE
1. Read `PROGRESS.md` in this directory
2. Check what files already exist: `find . -type f | head -200`
3. Identify the NEXT uncompleted step from the build order below
4. Resume from that step — do NOT rebuild files that already exist
5. After completing each step, UPDATE `PROGRESS.md` immediately

## BUILD ORDER (Part 21 — do these in exact sequence)

```
Step 1:  pyproject.toml + pyproject_proxy.toml + .env.example + .gitignore
Step 2:  infrastructure/ (full_setup.sh, create_compartment.sh, setup_secondary_vnics.sh, setup_proxy_vm.sh, security_hardening.sh, network_diagram.md) — write files only, do NOT execute
Step 3:  config/brands.json + config/platforms.json + config/settings.py + config/youtube_projects.json + config/cached_responses/ (3 JSON template files)
Step 4:  database/schema.sql (ALL 26 tables) + database/db.py + database/connection_pool.py + database/credential_manager.py
Step 5:  modules/infrastructure/logging_config.py + modules/infrastructure/retry_handler.py + modules/infrastructure/job_state_machine.py
Step 6:  modules/network/ip_router.py + modules/network/ua_generator.py
Step 7:  modules/ai_brain/llm_router.py
Step 8:  modules/compliance/ (rate_limits.py → rate_limit_manager.py → platform_compliance.py → anti_spam.py → cross_brand_dedup.py → free_tier_monitor.py)
Step 9:  modules/storage/oci_storage.py
Step 10: modules/infrastructure/ (circuit_breaker.py → health_monitor.py → shutdown_handler.py → idle_guard.py → resource_scheduler.py → config_validator.py)
Step 11: account_manager/ (manager.py → account_setup.py → token_refresher.py)
Step 12: modules/trend_scanner/ (all scanners)
Step 13: modules/ai_brain/ (hook_engine.py → brand_generator.py → script_writer.py → classifier.py → duplicate_checker.py → hashtag_generator.py → brain.py)
Step 14: modules/brand/ (safety_scorer.py → quality_gate.py → voice_tracker.py → milestone_tracker.py)
Step 15: modules/content_forge/ (background_library.py → tts_engine.py → broll_fetcher.py → music_fetcher.py → caption_generator.py → video_assembler.py → thumbnail_maker.py → forge.py)
Step 16: modules/queue/content_queue.py
Step 17: modules/review_gate/ (approval_tracker.py → gate.py → telegram_reviewer.py → email_sender.py → approval_server.py → gdrive_uploader.py)
Step 18: modules/publish_engine/ (schedule_config.py → base.py → tiktok.py → instagram.py → facebook.py → youtube.py → snapchat.py → formatters.py → caption_writer.py → scheduler.py → publisher.py)
Step 19: modules/feedback_loop/ (scorer.py → analytics_puller.py → hook_optimizer.py → model_updater.py)
Step 20: modules/marketing/ (ab_testing.py → first_comment.py → utm_tracker.py → trending_audio.py → calendar_view.py)
Step 21: modules/dashboard/dashboard.py
Step 22: modules/notifications/ (email_notifier.py → telegram_bot.py)
Step 23: jobs/ (all 19 job files)
Step 24: scripts/ (all script files including keepalive_proxy.sh)
Step 25: tests/ (all 14 test files)
Step 26: config/supervisord_content.conf + config/supervisord_proxy.conf + config/logrotate.conf
Step 27: Makefile + README.md
```

## HOW TO UPDATE PROGRESS.md
After completing each step, append to PROGRESS.md:
```
## Step N — [description] — COMPLETED [timestamp]
Files created:
- path/to/file1.py
- path/to/file2.py
Next: Step N+1
```

## ON RESUME AFTER RATE LIMIT
When you are resumed after a rate limit pause:
1. Say: "Resuming build. Reading progress..."
2. Read PROGRESS.md
3. Run: `find . -type f -name "*.py" -o -name "*.json" -o -name "*.sql" -o -name "*.sh" -o -name "*.toml" -o -name "*.conf" | sort`
4. Compare against the build order to find where you left off
5. Continue from the next incomplete step
6. Do NOT re-read the full AUTOFARM_V6_IMPROVED.md unless you need specific detail for the current step — use targeted reads (e.g. read only the Part that covers the files you're building)

## CODE QUALITY RULES
- Every file must have module docstring explaining purpose
- All classes must have class docstring with description
- Every method must have docstring explaining parameters, returns, and side effects
- Use type hints on all function signatures
- Follow the non-negotiable implementation rules from Part 20:
  - All API calls go through RateLimitManager
  - All LLM calls go through LLMRouter
  - All publishing calls go through BrandIPRouter
  - All state transitions go through JobStateMachine
  - All heavy jobs check ResourceScheduler first
  - Cross-brand dedup on every new script
  - Quality gate before review gate
  - Telegram review is primary, email is fallback

## DIRECTORY CREATION
Before creating files in a new directory, create the directory and its __init__.py:
```bash
mkdir -p modules/compliance
touch modules/compliance/__init__.py
```

## DO NOT
- Do NOT ask me questions — read the spec
- Do NOT skip files or leave TODOs/placeholders — implement everything
- Do NOT run infrastructure scripts (they're for OCI, not local)
- Do NOT install packages (just write the code)
- Do NOT create files outside the project directory
- Do NOT modify AUTOFARM_V6_IMPROVED.md
