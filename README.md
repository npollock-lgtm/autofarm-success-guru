# AutoFarm Zero — Success Guru Network v6.0

Autonomous content creation and multi-account publishing network running entirely on OCI Always-Free Tier. Generates, reviews, and publishes short-form video content across 6 brands and 5 platforms — fully automated with human-in-the-loop review via Telegram.

## Architecture

| Component | Spec | Role |
|-----------|------|------|
| **Content VM** | A1 Flex — 3 OCPU, 20 GB RAM, 8 GB swap | Content generation, video assembly, scheduling |
| **Proxy VM** | A1 Flex — 1 OCPU, 4 GB RAM | 6 Squid proxies, approval server, Telegram bot |
| **Database** | SQLite WAL — 26 tables | Single-writer, daily backups to OCI Object Storage |
| **AI Stack** | Ollama (primary) + Groq (fallback) | LLM routing with cached response templates |
| **TTS** | Kokoro v0.9 | 6 unique voices, one per brand |

## Brands

| Brand ID | Niche | Voice |
|----------|-------|-------|
| `human_success_guru` | Dark Psychology & Motivation | af_sky (0.85x) |
| `wealth_success_guru` | Wealth & Financial Strategy | am_adam (0.90x) |
| `zen_success_guru` | Mindfulness & Inner Peace | af_bella (0.80x) |
| `social_success_guru` | Social Skills & Charisma | am_michael (0.95x) |
| `habits_success_guru` | Habits & Productivity | af_nicole (0.90x) |
| `relationships_success_guru` | Relationships & Connection | af_sarah (0.85x) |

## Platforms

TikTok, Instagram Reels, Facebook Reels, YouTube Shorts, Snapchat Spotlight — each brand publishes to all 5 platforms with platform-specific formatting.

## Quick Start

### Prerequisites

- OCI Always-Free account with 2 A1 Flex instances
- Python 3.11+
- FFmpeg with libx264
- Ollama with `llama3.1:8b-instruct-q5_K_M`
- API keys: Groq, Pexels, Pixabay, Telegram Bot, Gmail App Password

### Deployment Order

```bash
# 1. Provision OCI infrastructure
bash infrastructure/full_setup.sh

# 2. Set up proxy VM
ssh proxy-vm 'bash infrastructure/setup_proxy_vm.sh'

# 3. Set up content VM
ssh content-vm 'bash scripts/setup_content_vm.sh'

# 4. Configure environment
cp .env.example .env
# Edit .env with your API keys and credentials

# 5. Install dependencies
make install

# 6. Generate encryption key
make encrypt-key

# 7. Initialise database
make init-db

# 8. Create directories
make create-dirs

# 9. Download fonts and backgrounds
make download-fonts
make predownload-bg

# 10. Install Kokoro TTS voices
make install-kokoro

# 11. Install cron jobs
make install-cron

# 12. Validate configuration
make validate-config

# 13. Launch
make run

# 14. Switch to live publishing (default: dry_run)
make toggle-mode
```

### Local Development

```bash
# Install dependencies
make install-dev

# Run tests
make test

# Run the full pipeline test (35 tests)
make test-pipeline

# Lint and format
make lint
make format
```

## Content Pipeline

```
Trend Scanner ─→ Hook Engine ─→ Script Writer ─→ Safety Check
                                                      │
                                                      ▼
Quality Gate ←── Video Assembler ←── TTS Engine ←── Content Forge
      │
      ▼
Review Gate ──→ Telegram Review (primary)
      │              │
      │         Email (fallback)
      │              │
      ▼              ▼
Content Queue ──→ Smart Scheduler ──→ Publisher ──→ Platform APIs
                                                        │
                                                        ▼
                                              Analytics Puller
                                                        │
                                                        ▼
                                              Feedback Loop ──→ Hook Optimizer
```

## Non-Negotiable Rules (Part 20)

1. All API calls go through `RateLimitManager`
2. All LLM calls go through `LLMRouter` (Ollama primary, Groq fallback)
3. All publishing calls go through `BrandIPRouter` (unique IP per brand)
4. All state transitions go through `JobStateMachine`
5. All heavy jobs check `ResourceScheduler` first
6. Cross-brand dedup on every new script (>0.7 cosine similarity = reject)
7. `QualityGate.check()` runs BEFORE `ReviewGate.process()`
8. Telegram review is primary, email is fallback
9. No hardcoded credentials — all secrets via `.env` + `CredentialManager`
10. Every file has module docstring, class docstring, method docstrings, type hints

## Key Modules

| Module | Purpose |
|--------|---------|
| `modules/ai_brain/` | LLM routing, hook generation, script writing, classification |
| `modules/brand/` | Safety scoring, quality gate, voice tracking, milestones |
| `modules/compliance/` | Rate limits, platform compliance, anti-spam, cross-brand dedup |
| `modules/content_forge/` | TTS, video assembly, thumbnails, backgrounds, captions |
| `modules/dashboard/` | Pure Python HTML dashboard — 7 pages, dark theme |
| `modules/feedback_loop/` | Analytics scoring, hook optimization, model updates |
| `modules/infrastructure/` | Logging, retry, circuit breaker, health, idle guard |
| `modules/marketing/` | A/B testing, first comments, UTM tracking, trending audio |
| `modules/network/` | Brand IP routing, user agent generation |
| `modules/notifications/` | Telegram bot, email notifier |
| `modules/publish_engine/` | Platform publishers (5), scheduler, formatters |
| `modules/queue/` | Content queue management |
| `modules/review_gate/` | Approval server, Telegram review, email review |
| `modules/storage/` | OCI Object Storage integration |
| `modules/trend_scanner/` | Multi-source trend discovery |
| `account_manager/` | OAuth account management, token refresh |
| `jobs/` | 19 scheduled cron jobs |
| `scripts/` | Setup, maintenance, and utility scripts |

## Scheduled Jobs

| Job | Schedule | Purpose |
|-----|----------|---------|
| `scan_and_generate` | Every 2 hours | Trend scan → script → video → review queue |
| `publish_due` | Every 5 minutes | Publish scheduled content |
| `process_review_queue` | Every 15 minutes | Send pending reviews to Telegram |
| `check_auto_approvals` | Every 30 minutes | Auto-approve expired reviews |
| `pull_analytics` | Daily 03:00 | Pull platform analytics, score, optimise |
| `refresh_tokens` | Daily 04:45 | Refresh OAuth tokens |
| `backup_database` | Daily 02:30 | SQLite backup → gzip → OCI |
| `send_daily_digest` | Daily 08:00 | Telegram + email digest |
| `check_queue_depth` | Hourly | Alert if queue < 2 items |
| `check_storage` | Daily 06:00 | Monitor disk / GDrive / OCI storage |
| `reset_daily_counts` | Daily 00:00 | Reset daily rate limit counters |
| `reset_api_quotas` | Daily 00:01 | Reset API quota tracking |
| `maintain_backgrounds` | Weekly Mon 02:00 | Background library refresh |
| `reoptimise_schedule` | Weekly Mon 04:00 | Full model update cycle |
| `cleanup_orphans` | Daily 04:00 | Remove orphaned temp files |
| `cleanup_gdrive` | Daily 05:00 | Remove 14-day-old GDrive files |
| `validate_config` | Daily 05:30 | Config validation + alert |
| `refresh_user_agents` | Monthly 1st 03:00 | Regenerate UA strings |

## Testing

```bash
# Unit tests (14 test files)
make test

# End-to-end pipeline test (35 tests)
make test-pipeline

# Proxy routing verification
make test-proxy
```

## Configuration Files

| File | Purpose |
|------|---------|
| `.env` | API keys, tokens, database path, feature flags |
| `config/brands.json` | 6 brand definitions — niche, voice, visual identity |
| `config/platforms.json` | Platform-specific limits and format requirements |
| `config/settings.py` | Centralised settings loader |
| `config/youtube_projects.json` | GCP project rotation for YouTube quota |
| `config/supervisord_content.conf` | Content VM daemon management |
| `config/supervisord_proxy.conf` | Proxy VM daemon management |
| `config/logrotate.conf` | Log rotation — 7 days, 50 MB max |
| `database/schema.sql` | Full database schema (26 tables) |

## Makefile Targets

Run `make help` to see all available targets:

- `make install` — Install production dependencies
- `make init-db` — Initialise database
- `make run` — Launch the system
- `make test` — Run unit tests
- `make test-pipeline` — Run 35-test end-to-end pipeline test
- `make validate-config` — Validate all configuration
- `make clean` — Remove temp files and caches

## License

Proprietary. All rights reserved.
