# AUTOFARM ZERO — SUCCESS GURU NETWORK v6.0 IMPROVED
# Claude Code Master Build Prompt — Complete Production System
# Oracle Cloud Infrastructure Free Tier Deployment
# Upgraded from V5.1 with critical fixes, architectural improvements, and verified free tier limits
# Paste everything below this line into Claude Code

---

You are going to build the **complete production-ready system** called **AutoFarm Zero — Success Guru Network Edition v6.0**.

This is a fully self-contained, autonomous content creation and multi-account publishing network deployed entirely on Oracle Cloud Infrastructure Always Free tier. It manages 6 premium faceless brands across up to 30 social media accounts, with encrypted credential management, IP-isolated publishing via per-brand Squid proxy instances, platform compliance enforcement, intelligent scheduling, and a human review gate before any content is published.

**Read this entire document before writing a single line of code. Build everything in the Part 21 build order.**

---

## CHANGELOG: V5.1 → V6.0 IMPROVEMENTS

This section documents every change from V5.1 and the reasoning. Read this first.

### Critical Fixes (would have caused deployment failure)

1. **OCI Object Storage is 20GB, not 10GB.** V5.1 stated 10GB. Oracle's free tier provides 20GB of combined Standard, Infrequent Access, and Archive storage. Updated throughout.

2. **OCI Idle Instance Reclamation.** Oracle will stop Always Free instances that are "idle" for 7 days (CPU <20% at the 95th percentile, and for A1 shapes, memory <20%). V5.1 had no protection. Added `modules/infrastructure/idle_guard.py` — a lightweight daemon that ensures workload stays above thresholds. This is the single biggest operational risk on OCI free tier and MUST be addressed.

3. **Groq Free Tier Limits Drastically Different.** V5.1 listed 30 RPM / 14,400 RPD / 6,000 TPM. Actual current limits for `llama-3.3-70b-versatile`: 30 RPM / **1,000 RPD** / 12,000 TPM / **100,000 TPD**. The model name also changed from `llama-3.1-70b-versatile` to `llama-3.3-70b-versatile`. With 6 brands × ~10 scripts/day = 60 scripts needing ~500 tokens each output = ~30,000 TPD output alone. This was dangerously close to limits. **Fix:** Primary script generation now uses **local Ollama (LLaMA 3.1 8B)** for all routine work. Groq is a fallback for complex tasks only, staying well within limits. Added `modules/ai_brain/llm_router.py` to manage this.

4. **Whisper Was Unnecessary.** The system generates its own TTS audio — it never needs to transcribe external audio. Whisper base model consumes ~1GB RAM permanently. **Removed.** If subtitle timing is needed, Kokoro TTS already provides word-level timestamps. Saves 1GB RAM on content-vm.

5. **No Swap Configured.** 20GB RAM with Ollama (~5GB), Kokoro TTS (~1GB), FFmpeg (1-3GB per video), Python processes (~2GB), and OS overhead (~1GB) = ~12GB baseline, spiking to 16-18GB during concurrent video assembly. One spike = OOM killer. **Fix:** Added 8GB swap file in setup script. Not for regular use — purely OOM protection.

6. **6 GCP Projects Risk.** Google may flag multiple GCP projects created from the same Google account requesting YouTube Data API quota. **Fix:** Document that YouTube uploads should initially use a single GCP project with careful quota management (2 uploads/day across all brands = 3,200 units/day, well within 10,000). Only split into multiple projects IF quota becomes limiting. This reduces initial setup from 6 OAuth flows to 1.

7. **User Agent Strings Are Stale.** Chrome 122 from V5.1 is outdated and could trigger bot detection. **Fix:** User agents are now generated dynamically with current versions, rotated monthly, and pulled from a realistic pool per brand "persona."

### Architectural Improvements

8. **LLM Router with Graceful Degradation.** New `modules/ai_brain/llm_router.py` routes requests: Ollama (primary, free, unlimited) → Groq (fallback, rate-limited) → cached response (emergency). If Ollama is slow or down, Groq takes over. If both are down, the system uses cached/template responses for critical paths (e.g., caption variations) and pauses script generation until recovery. V5.1 had no fallback strategy.

9. **Telegram Review Bot (Primary) + Email (Fallback).** V5.1 used email-only review. Email is slow, unreliable (spam filters), and requires Google Drive upload. **New primary:** Telegram bot sends a compressed preview video (480p, 15s sample) + thumbnail + script text + approve/reject inline buttons. Reviewer taps a button, done in 5 seconds. Email kept as fallback for full video review. This eliminates the Google Drive dependency for routine reviews. Google Drive is now optional (for full-quality review only).

10. **SQLite Contention Prevention.** V5.1 had 15+ cron jobs all hitting the same SQLite file. WAL mode helps but doesn't prevent `SQLITE_BUSY` under heavy concurrent writes. **Fix:** Added `database/connection_pool.py` with a process-level write lock (using `fcntl.flock`), configurable busy timeout (30s), and WAL checkpoint management. All DB access goes through this pool.

11. **Exponential Backoff with Jitter.** V5.1 had circuit breaker only. Added `modules/infrastructure/retry_handler.py` — exponential backoff with jitter for all API calls. Sequence: 1s → 2s → 4s → 8s → 16s (max), with ±25% jitter. Circuit breaker still opens after 5 consecutive failures, but individual transient errors are retried first.

12. **Content Fingerprint Deduplication Across Brands.** V5.1's `anti_spam.py` varied videos per-platform but didn't track cross-brand similarity. Platforms can detect when the same "network" of accounts posts thematically identical content. **Fix:** `modules/compliance/cross_brand_dedup.py` maintains a rolling semantic fingerprint (TF-IDF vector) of recent scripts per brand and rejects scripts with >0.7 cosine similarity to any other brand's recent content. Each brand must produce genuinely distinct content.

13. **Structured Error Recovery.** V5.1 had no recovery for partially assembled videos. **Fix:** `modules/infrastructure/job_state_machine.py` tracks every content job through states: `TREND_FOUND → SCRIPT_DRAFT → SCRIPT_APPROVED → TTS_DONE → VIDEO_ASSEMBLED → QUALITY_PASSED → REVIEW_PENDING → REVIEW_APPROVED → SCHEDULED → PUBLISHED`. If a job fails at any state, it can be retried from that exact state without re-doing earlier work. Orphaned partial files are cleaned up by a daily job.

14. **Resource-Aware Job Scheduler.** V5.1 ran content generation on fixed 2-hour cron. If 3 brands all need content, they'd all generate simultaneously, causing RAM spikes. **Fix:** `modules/infrastructure/resource_scheduler.py` checks system resources (RAM, CPU, disk) before starting heavy jobs (video assembly, TTS). Jobs are serialized when resources are tight, parallelised when headroom exists. Maximum 1 concurrent video assembly job.

15. **Healthcheck Endpoint for External Monitoring.** Added `GET /health` on the approval server (proxy-vm port 8080) returning JSON system status. Can be polled by external uptime monitors (e.g., UptimeRobot free tier — 50 monitors, 5-min checks). V5.1 had monitoring but no external visibility.

16. **Configuration Validation on Startup.** New `modules/infrastructure/config_validator.py` runs on every system start. Validates: all .env vars present, all API keys valid (test calls), database schema matches expected version, disk space sufficient, Ollama responsive, proxy-vm reachable, cron jobs installed. Prevents the system from running in a broken state silently.

17. **Log Rotation and Structured Logging.** V5.1 mentioned logrotate but didn't define structured logging. **Fix:** All modules use Python `structlog` with JSON output. Logs include: timestamp, level, module, brand_id, job_id, duration_ms. Makes debugging and monitoring vastly easier. Logrotate config set to 7 days retention, 50MB max per file.

---

## PART 1 — SYSTEM PRINCIPLES

### Principle 1: Network Brand Identity
All 6 brands belong to the **Success Guru Network**. They share a family identity — premium, authoritative, psychology-rooted — while each occupying a distinct niche. The network effect means audiences cross-pollinate. The `brands.json` config must include a `network_name: "Success Guru Network"` field and each brand must include a `sister_brands` array for cross-promotion logic.

### Principle 2: Premium Over Volume
This system is built on **identity-first, not volume-first** content strategy. Quality signals must be enforced at every stage. A video that looks automated is a failure. A video that looks deliberate, calm, and authoritative is a success. Per-account daily limits are set conservatively to protect brand perception.

### Principle 3: One Dedicated Account Per Brand Per Platform
Each brand has its own dedicated account on every platform. Credentials are stored encrypted per brand per platform. One brand's API call never uses another brand's credentials. 6 brands × 5 platforms = up to 30 dedicated accounts.

### Principle 4: Review Gate Before Publish
**The system has two publishing modes controlled by a single config flag:**
- `PUBLISH_MODE=review` — Generated content is assembled, then emailed to the brand's designated review email address for human approval. **This is the mandatory default.** Nothing publishes without approval in this mode.
- `PUBLISH_MODE=auto` — Content publishes automatically without human review. Flipping this single flag — globally, per brand, or per platform — switches from supervised to full autopilot.

Each brand has its own review email address. The gate is configurable at three levels: global → per brand → per platform. Most specific setting wins.

### Principle 5: Self-Optimising Intelligence
The system tracks hook performance, content style effectiveness, posting time optimality, and retention signals per brand per platform. It adapts continuously. It learns what works for each brand's specific audience and biases future content accordingly.

### Principle 6: Zero Faces, Full Premium
All content is 100% faceless. Every visual, voice, caption, and audio decision must reinforce the specific brand's identity. Stoic Zen content must never look or sound like Relationship content.

### Principle 7: Platform Compliance First
The system must never violate the Terms of Service of any platform. API rate limits, daily post limits, content policies, and upload requirements are enforced at the code level — not just documented. A compliance violation that causes an account ban destroys months of content investment.

### Principle 8: IP Isolation Per Brand
Each brand publishes from a distinct source IP address. Platforms cross-reference IP addresses to detect coordinated inauthentic behaviour. This separation makes each brand appear as an independent operator. IP isolation is achieved via per-brand Squid proxy instances on the proxy-vm, each bound to a specific network interface IP.

### Principle 9: Organic Timing Behaviour
Publishing never occurs at predictable intervals. All scheduled times are randomised within a ±30-minute window. The system tracks performance by time-of-day and adapts posting windows toward high-performing slots over time.

### Principle 10: Content Pre-Production Pipeline
Content is generated in advance and queued. The pipeline produces content continuously and independently of the publishing schedule. Publishing reads from the queue — the two pipelines never block each other.

### Principle 11: Self-Contained OCI Isolation
The entire system lives within a dedicated OCI Compartment with its own VCN, subnets, and security lists. Future projects on the same OCI tenancy are deployed to separate compartments and cannot access this system's resources.

### Principle 12: Zero-Touch Expansion
Adding a new brand requires only: a niche description and platform account handles. The system generates all remaining configuration using AI, creates all database records, and integrates the new brand into the full pipeline with a single command.

### Principle 13: Graceful Degradation (NEW)
Every external dependency has a fallback. Groq down → Ollama. Ollama down → cached templates. Pexels down → Pixabay → FFmpeg fallback. Google Drive down → Telegram preview only. SMTP down → Telegram notification. No single external service failure should halt the entire pipeline. The system always makes forward progress.

### Principle 14: Resource Awareness (NEW)
The system monitors its own resource consumption (RAM, CPU, disk) and throttles work accordingly. Heavy jobs (video assembly, TTS, LLM inference) are serialised when resources are constrained. The system never OOM-kills itself. On OCI free tier, this is existential — if the VM is killed, it may not restart if ARM capacity is exhausted in the region.

---

## PART 2 — ORACLE CLOUD INFRASTRUCTURE ARCHITECTURE

### 2.1 OCI Free Tier Resources Used (CORRECTED)

| Resource | Free Tier Allowance | This System's Usage |
|----------|--------------------|--------------------|
| A1 Flex Compute | 4 OCPUs, 24 GB RAM total | content-vm: 3 OCPU 20GB · proxy-vm: 1 OCPU 4GB |
| Block Storage | 200 GB total across all volumes | content-vm: 150GB · proxy-vm: 50GB |
| Object Storage | **20 GB** (Standard + Infrequent + Archive) | Database backups, brand asset backups |
| VCN | 2 VCNs | 1 VCN with 2 subnets |
| Public IPs | 3 public IPs on proxy-vm | 1 primary + 2 secondary VNICs |
| Outbound Transfer | 10 TB/month | Estimated 500GB/month at full scale |
| Email Delivery | 3,000 emails/month (OCI service) | Not used — using Gmail SMTP instead |

**OCI Region: `uk-london-1` (London)** — Operator is based in Woking, England. Lowest latency for management. Content targeting UK + US audiences.

**CRITICAL WARNING — IDLE INSTANCE RECLAMATION:**
Oracle will stop Always Free instances deemed "idle" during any 7-day period where ALL of the following are true:
- CPU utilisation at the 95th percentile is less than 20%
- (For A1 shapes) Memory utilisation is less than 20%
- Network utilisation is less than 20%

The content-vm naturally exceeds these thresholds during content generation. But during quiet periods (e.g., queue is full, no content needed for days), it could dip below. The `idle_guard` daemon (Part 9) ensures this never happens.

**IMPORTANT: Upgrade to Pay-As-You-Go (PAYG) for reliability.** PAYG does NOT charge you if you stay within Always Free limits, but it removes capacity restrictions and prevents idle reclamation. This is strongly recommended.

### 2.2 VM Architecture

```
OCI Tenancy
└── Compartment: autofarm-success-guru
    └── VCN: autofarm-vcn (10.0.0.0/16)
        ├── Subnet: content-subnet (10.0.1.0/24) — private
        │   └── content-vm (A1 Flex: 3 OCPU, 20GB RAM, 8GB swap)
        │       ├── Ollama + LLaMA 3.1 8B (PRIMARY LLM — unlimited, free)
        │       ├── Kokoro TTS (6 voice models, ~200MB RAM)
        │       ├── FFmpeg video assembly
        │       ├── All Python content modules
        │       ├── SQLite database (WAL mode + write lock)
        │       ├── Trend scanning, script writing, video assembly
        │       ├── Review gate, analytics, scheduled jobs
        │       ├── Idle guard daemon
        │       ├── Resource-aware job scheduler
        │       └── Supervisord (content jobs)
        │
        ├── Subnet: proxy-subnet (10.0.2.0/24) — public
        │   └── proxy-vm (A1 Flex: 1 OCPU, 4GB RAM)
        │       ├── 6 independent Squid proxy instances (one per brand)
        │       │   Each bound to a distinct source IP via secondary VNICs
        │       ├── ALL outbound publishing API calls routed here
        │       ├── Token refresh daemon
        │       ├── Telegram review bot
        │       ├── Approval HTTP server (port 8080) + /health endpoint
        │       └── Supervisord (proxy + approval + telegram jobs)
        │
        ├── NAT Gateway (for content-vm outbound internet)
        └── Internet Gateway (for proxy-vm)
```

### 2.3 IP Separation via Per-Brand Squid Proxy Instances

*[This section is identical to V5.1 — Squid config, systemd services, proxy map, ip_router.py all unchanged.]*

**One change:** User agent strings are now dynamically generated:

```python
# modules/network/ua_generator.py
import random
from datetime import datetime

class UserAgentGenerator:
    """
    Generates realistic, current user agent strings per brand persona.
    Updated monthly via cron job. Each brand has a consistent "device persona"
    (e.g., brand A = Mac/Chrome, brand B = Windows/Chrome) but version numbers
    stay current to avoid bot detection from stale UAs.
    """

    BRAND_PERSONAS = {
        'human_success_guru':         {'os': 'mac', 'browser': 'chrome'},
        'wealth_success_guru':        {'os': 'windows', 'browser': 'chrome'},
        'zen_success_guru':           {'os': 'mac', 'browser': 'safari'},
        'social_success_guru':        {'os': 'windows', 'browser': 'firefox'},
        'habits_success_guru':        {'os': 'linux', 'browser': 'chrome'},
        'relationships_success_guru': {'os': 'iphone', 'browser': 'safari'},
    }

    def get_ua(self, brand_id: str) -> str:
        """Returns a current, realistic UA string for this brand's persona."""
        persona = self.BRAND_PERSONAS[brand_id]
        # Chrome version increments ~monthly. Base on current date.
        chrome_major = 120 + ((datetime.now().year - 2024) * 12 + datetime.now().month) // 1
        chrome_ver = f"{min(chrome_major, 135)}.0.0.0"  # Cap at realistic max

        if persona['os'] == 'mac' and persona['browser'] == 'chrome':
            return f'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome_ver} Safari/537.36'
        # ... similar for each persona combination
```

### 2.4 Review System: Telegram Bot (Primary) + Email/Google Drive (Fallback)

**V6.0 change: Telegram is now the primary review channel.**

Why Telegram beats email for review:
- Instant push notification (email may be delayed or spam-filtered)
- Inline approve/reject buttons (one tap vs. loading a webpage)
- Compressed preview video sent as Telegram video message (~2MB, 480p, 15s sample)
- Thumbnail as photo message
- Full script as text message
- No Google Drive dependency for routine reviews
- Reviewer can approve from phone in 5 seconds

Google Drive + email kept as:
- **Full-quality review:** When reviewer wants to see full resolution before approving
- **Fallback:** When Telegram bot is unavailable

```python
# modules/review_gate/telegram_reviewer.py
import os
import subprocess
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

class TelegramReviewer:
    """
    Sends review packages via Telegram for instant mobile approval.
    Primary review channel. Falls back to email if Telegram fails.
    """

    def __init__(self):
        self.bot = Bot(token=os.getenv('TELEGRAM_BOT_TOKEN'))
        self.chat_id = os.getenv('TELEGRAM_REVIEW_CHAT_ID')

    def send_review(self, review_id: int, brand_id: str,
                     video_path: str, thumbnail_path: str,
                     script_text: str, review_token: str,
                     metadata: dict) -> bool:
        """
        Sends review package:
        1. Compressed preview video (480p, 15s, <5MB)
        2. Thumbnail photo
        3. Script text with metadata
        4. Inline approve/reject buttons
        """
        try:
            # Compress video for Telegram (max 50MB, but aim for <5MB)
            preview_path = self._compress_for_telegram(video_path)

            # Build approval URL (points to approval server on proxy-vm)
            base_url = f"http://{os.getenv('PROXY_VM_PUBLIC_IP')}:8080"
            approve_url = f"{base_url}/approve/{review_token}"
            reject_url = f"{base_url}/reject/{review_token}"

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Approve", callback_data=f"approve:{review_token}"),
                    InlineKeyboardButton("❌ Reject", callback_data=f"reject:{review_token}"),
                ],
                [
                    InlineKeyboardButton("📺 Full Quality", url=f"{base_url}/review/{review_token}"),
                ]
            ])

            # Send thumbnail
            with open(thumbnail_path, 'rb') as f:
                self.bot.send_photo(
                    chat_id=self.chat_id,
                    photo=f,
                    caption=self._format_review_caption(brand_id, script_text, metadata)
                )

            # Send preview video
            with open(preview_path, 'rb') as f:
                self.bot.send_video(
                    chat_id=self.chat_id,
                    video=f,
                    caption=f"🎬 Preview for {brand_id}",
                    reply_markup=keyboard
                )

            return True

        except Exception as e:
            # Log and fall back to email
            from modules.review_gate.email_sender import ReviewEmailSender
            return ReviewEmailSender().send_review_email(review_id)

    def _compress_for_telegram(self, video_path: str) -> str:
        """Compresses to 480p, 15s sample, <5MB for Telegram."""
        preview_path = video_path.replace('.mp4', '_preview.mp4')
        subprocess.run([
            'ffmpeg', '-y', '-i', video_path,
            '-t', '15',  # First 15 seconds
            '-vf', 'scale=480:-2',
            '-c:v', 'libx264', '-crf', '28',
            '-c:a', 'aac', '-b:a', '64k',
            '-movflags', '+faststart',
            preview_path
        ], check=True, capture_output=True)
        return preview_path

    def _format_review_caption(self, brand_id: str, script_text: str,
                                 metadata: dict) -> str:
        """Formats readable review caption for Telegram."""
        return (
            f"📋 **Review: {brand_id}**\n"
            f"⏱ Duration: {metadata.get('duration_s', '?')}s\n"
            f"🎯 Hook: {metadata.get('hook_type', '?')}\n"
            f"📱 Platforms: {', '.join(metadata.get('platforms', []))}\n\n"
            f"📝 Script:\n{script_text[:1000]}"
        )
```

### 2.5 Google Drive (Optional — Full Quality Review Only)

Google Drive is now OPTIONAL. Used only when:
- Reviewer requests full-quality video via "Full Quality" button in Telegram
- Email-based review fallback is triggered
- Telegram bot is unavailable

*[GDriveVideoUploader code from V5.1 is retained but moved to optional dependency.]*

Storage monitor threshold stays at 12GB (80% of 15GB).

### 2.6 OCI Object Storage (Backups — 20GB)

```python
# modules/storage/oci_storage.py
class OCIObjectStorage:
    """
    Manages database backups to OCI Object Storage.
    Free tier: 20GB Standard + Infrequent + Archive combined.
    """
    BUCKET_NAME = "autofarm-backups"
    TOTAL_FREE_GB = 20
    ALERT_THRESHOLD_GB = 16  # 80% of 20GB

    def upload_backup(self, backup_path: str) -> str:
        """Uploads database backup file. Returns object name."""

    def list_backups(self) -> list[dict]:
        """Lists all backup objects in the bucket."""

    def delete_old_backups(self, keep_days: int = 14):
        """Deletes backups older than keep_days."""

    def get_storage_usage_gb(self) -> float:
        """Returns current usage. Alerts if approaching 16GB (80% of 20GB free)."""
```

### 2.7 Compartment Isolation Script

`infrastructure/create_compartment.sh` — Run once to create the isolated compartment:

```bash
#!/bin/bash
# Creates a fully isolated OCI compartment for AutoFarm
set -e

COMPARTMENT_NAME="autofarm-success-guru"
TENANCY_OCID=$(oci iam compartment list --all --query "data[0].\"compartment-id\"" --raw-output)

COMPARTMENT_OCID=$(oci iam compartment create \
  --compartment-id $TENANCY_OCID \
  --name $COMPARTMENT_NAME \
  --description "AutoFarm Zero Success Guru Network - isolated content farm" \
  --query "data.id" --raw-output)

echo "Compartment created: $COMPARTMENT_OCID"

VCN_OCID=$(oci network vcn create \
  --compartment-id $COMPARTMENT_OCID \
  --cidr-block "10.0.0.0/16" \
  --display-name "autofarm-vcn" \
  --query "data.id" --raw-output)

IGW_OCID=$(oci network internet-gateway create \
  --compartment-id $COMPARTMENT_OCID \
  --vcn-id $VCN_OCID \
  --is-enabled true \
  --display-name "autofarm-igw" \
  --query "data.id" --raw-output)

NAT_OCID=$(oci network nat-gateway create \
  --compartment-id $COMPARTMENT_OCID \
  --vcn-id $VCN_OCID \
  --display-name "autofarm-nat" \
  --query "data.id" --raw-output)

# Create content-subnet (private — routes via NAT)
# Create proxy-subnet (public — routes via IGW)
# Security lists defined in full_setup.sh

echo "Infrastructure created. Save these OCIDs to .env.infrastructure:"
echo "COMPARTMENT_OCID=$COMPARTMENT_OCID"
echo "VCN_OCID=$VCN_OCID"
```

---

## PART 3 — LLM ROUTING & AI STRATEGY (NEW)

### 3.1 The LLM Router — Ollama Primary, Groq Fallback

**Why this changed from V5.1:** Groq free tier limits are far tighter than V5.1 assumed. At 1,000 RPD and 100,000 TPD for the 70B model, and 14,400 RPD / 500,000 TPD for the 8B model, Groq cannot sustain 6 brands generating 10+ scripts/day without hitting limits. Local Ollama with LLaMA 3.1 8B is unlimited, free, and fast enough on 3 ARM cores.

```python
# modules/ai_brain/llm_router.py
import os
import time
import json
from datetime import datetime, timedelta
from enum import Enum

class LLMProvider(Enum):
    OLLAMA = "ollama"
    GROQ = "groq"
    CACHED = "cached"

class LLMRouter:
    """
    Routes LLM requests to the best available provider.
    Priority: Ollama (local, free, unlimited) → Groq (fast, rate-limited) → Cached (emergency)

    Task routing:
    - Script generation: Ollama (primary) — bulk work, no rate limit
    - Brand safety scoring: Ollama — needs nuance, acceptable at 8B
    - Caption variation: Ollama — simple rewording task
    - Hashtag generation: Ollama — pattern matching, 8B handles fine
    - Brand config generation: Groq 70B — complex, rare (1/month max)
    - Hook optimisation: Groq 70B — needs sophisticated analysis, rare
    - Emergency fallback: Cached templates + simple variations
    """

    GROQ_DAILY_LIMITS = {
        'llama-3.3-70b-versatile': {'rpd': 1000, 'tpd': 100000, 'tpm': 12000},
        'llama-3.1-8b-instant': {'rpd': 14400, 'tpd': 500000, 'tpm': 6000},
    }

    def __init__(self):
        self.groq_usage_today = {'requests': 0, 'tokens': 0}
        self.groq_last_reset = datetime.utcnow().date()
        self.ollama_healthy = True
        self.groq_healthy = True

    def generate(self, prompt: str, task_type: str,
                  max_tokens: int = 1000,
                  temperature: float = 0.7) -> dict:
        """
        Routes to best provider for this task.
        Returns {text, provider, tokens_used, latency_ms}
        """
        provider = self._select_provider(task_type, max_tokens)

        if provider == LLMProvider.OLLAMA:
            return self._call_ollama(prompt, max_tokens, temperature)
        elif provider == LLMProvider.GROQ:
            return self._call_groq(prompt, max_tokens, temperature)
        else:
            return self._get_cached_response(task_type)

    def _select_provider(self, task_type: str, max_tokens: int) -> LLMProvider:
        """
        Decision logic:
        1. Complex/rare tasks → Groq (if available and within limits)
        2. Everything else → Ollama (if healthy)
        3. Both down → Cached
        """
        complex_tasks = {'brand_config_generation', 'hook_optimisation', 'weekly_analysis'}

        if task_type in complex_tasks and self._groq_within_limits(max_tokens):
            return LLMProvider.GROQ

        if self.ollama_healthy:
            return LLMProvider.OLLAMA

        if self._groq_within_limits(max_tokens):
            return LLMProvider.GROQ

        return LLMProvider.CACHED

    def _groq_within_limits(self, estimated_tokens: int) -> bool:
        """Checks if Groq call would stay within free tier limits."""
        self._maybe_reset_daily_counters()
        limits = self.GROQ_DAILY_LIMITS['llama-3.3-70b-versatile']
        return (
            self.groq_healthy and
            self.groq_usage_today['requests'] < limits['rpd'] * 0.8 and  # 80% safety margin
            self.groq_usage_today['tokens'] + estimated_tokens < limits['tpd'] * 0.8
        )

    def _call_ollama(self, prompt: str, max_tokens: int,
                      temperature: float) -> dict:
        """Calls local Ollama instance. Marks unhealthy on timeout."""
        import requests
        try:
            start = time.time()
            response = requests.post(
                'http://localhost:11434/api/generate',
                json={
                    'model': 'llama3.1:8b',
                    'prompt': prompt,
                    'stream': False,
                    'options': {
                        'num_predict': max_tokens,
                        'temperature': temperature,
                    }
                },
                timeout=120  # 2 min timeout for 8B on ARM
            )
            response.raise_for_status()
            data = response.json()
            self.ollama_healthy = True
            return {
                'text': data['response'],
                'provider': 'ollama',
                'tokens_used': data.get('eval_count', 0),
                'latency_ms': int((time.time() - start) * 1000),
            }
        except Exception as e:
            self.ollama_healthy = False
            raise

    def _call_groq(self, prompt: str, max_tokens: int,
                    temperature: float) -> dict:
        """Calls Groq API with rate tracking."""
        import requests
        try:
            start = time.time()
            response = requests.post(
                'https://api.groq.com/openai/v1/chat/completions',
                headers={
                    'Authorization': f'Bearer {os.getenv("GROQ_API_KEY")}',
                    'Content-Type': 'application/json',
                },
                json={
                    'model': 'llama-3.3-70b-versatile',
                    'messages': [{'role': 'user', 'content': prompt}],
                    'max_tokens': max_tokens,
                    'temperature': temperature,
                },
                timeout=30
            )
            response.raise_for_status()
            data = response.json()
            tokens_used = data['usage']['total_tokens']
            self.groq_usage_today['requests'] += 1
            self.groq_usage_today['tokens'] += tokens_used
            self.groq_healthy = True
            return {
                'text': data['choices'][0]['message']['content'],
                'provider': 'groq',
                'tokens_used': tokens_used,
                'latency_ms': int((time.time() - start) * 1000),
            }
        except Exception as e:
            if hasattr(e, 'response') and e.response and e.response.status_code == 429:
                self.groq_healthy = False  # Rate limited — disable for this cycle
            raise

    def _get_cached_response(self, task_type: str) -> dict:
        """Emergency fallback: returns a template response."""
        # Templates stored in config/cached_responses/{task_type}.json
        # Contains pre-written scripts, captions, etc. for each brand
        pass

    def _maybe_reset_daily_counters(self):
        today = datetime.utcnow().date()
        if today > self.groq_last_reset:
            self.groq_usage_today = {'requests': 0, 'tokens': 0}
            self.groq_last_reset = today

    def get_status(self) -> dict:
        """Returns provider health and usage status."""
        return {
            'ollama': {'healthy': self.ollama_healthy},
            'groq': {
                'healthy': self.groq_healthy,
                'requests_today': self.groq_usage_today['requests'],
                'tokens_today': self.groq_usage_today['tokens'],
                'limits': self.GROQ_DAILY_LIMITS,
            }
        }
```

### 3.2 Ollama Configuration for ARM (Content-VM)

```bash
# Ollama ARM-specific optimisation
# LLaMA 3.1 8B Q4_K_M quantisation: ~5GB VRAM/RAM
# On 3 ARM cores, expect ~10-15 tokens/second (adequate for script generation)

# Environment variables for Ollama on content-vm
OLLAMA_HOST=127.0.0.1:11434
OLLAMA_NUM_PARALLEL=1          # Single request at a time (RAM constraint)
OLLAMA_MAX_LOADED_MODELS=1     # Only one model loaded (RAM constraint)
OLLAMA_KEEP_ALIVE=10m          # Unload model after 10min idle (free RAM for FFmpeg)
```

---

## PART 4 — PLATFORM API RATE LIMITS & COMPLIANCE ENGINE

### 4.1 Platform Rate Limits Reference

```python
# modules/compliance/rate_limits.py
# AUTHORITATIVE RATE LIMIT DEFINITIONS
# Sources: Official platform developer documentation

PLATFORM_LIMITS = {
    "youtube": {
        "quota_units_per_day": 10000,
        "cost_per_upload": 1600,
        "cost_per_metadata_update": 50,
        "cost_per_thumbnail_upload": 50,
        "cost_per_analytics_read": 1,
        "max_uploads_per_day_per_channel": 6,
        "max_channels_per_gcp_project": 1,
        "min_video_gap_minutes": 60,
        "shorts_max_duration_seconds": 60,
        "shorts_aspect_ratio": "9:16",
        "max_title_length": 100,
        "max_description_length": 5000,
        "max_tags": 500,
        "max_resolution": "1920x1080",
        "tos_key_points": [
            "Do not upload identical content to multiple channels",
            "Do not use automation to create misleading engagement",
            "Disclose AI-generated content where required",
            "No spam, deceptive practices, or misleading metadata",
            "Must comply with YouTube's Repetitive Content policy"
        ]
    },
    "instagram": {
        "graph_api_calls_per_hour": 200,
        "content_publishing_calls_per_hour": 25,
        "max_posts_per_24h_per_account": 25,
        "recommended_posts_per_day": 2,
        "reels_container_poll_interval_seconds": 15,
        "reels_container_max_wait_minutes": 10,
        "min_post_gap_minutes": 180,
        "max_caption_length": 2200,
        "max_hashtags": 30,
        "max_video_size_mb": 4096,
        "reels_max_duration_seconds": 90,
        "reels_aspect_ratio": "9:16",
        "tos_key_points": [
            "Authentic interactions only — no automated likes/comments",
            "Do not use third-party tools that violate platform terms",
            "Must have Instagram Business or Creator account",
            "AI content label recommended where applicable",
            "Do not post coordinated inauthentic content across accounts"
        ]
    },
    "facebook": {
        "graph_api_calls_per_hour": 200,
        "page_post_calls_per_hour": 25,
        "max_posts_per_day_per_page": 25,
        "recommended_posts_per_day": 1,
        "min_post_gap_minutes": 240,
        "video_max_size_gb": 10,
        "video_max_duration_minutes": 241,
        "reels_max_duration_seconds": 90,
        "max_message_length": 63206,
        "tos_key_points": [
            "No coordinated inauthentic behaviour",
            "Page must be authentic representation",
            "Video content must be original or properly licensed",
            "No artificial engagement",
            "Branded content policies apply for sponsored mentions"
        ]
    },
    "tiktok": {
        "content_posting_api_videos_per_day": 5,
        "recommended_videos_per_day": 2,
        "min_post_gap_minutes": 180,
        "oauth_token_expires_hours": 24,
        "refresh_token_expires_days": 365,
        "max_video_size_mb": 4096,
        "max_video_duration_seconds": 600,
        "short_video_max_seconds": 60,
        "min_video_duration_seconds": 3,
        "max_title_length": 2200,
        "max_hashtags_recommended": 5,
        "chunk_size_mb": 10,
        "tos_key_points": [
            "Must use official Content Posting API",
            "No automation that violates platform policies",
            "Synthetic or AI content must be labelled using TikTok's AI Content label",
            "No spam or coordinated inauthentic content",
            "Creator accounts must comply with Community Guidelines",
            "Do not use scrapers or unofficial APIs"
        ]
    },
    "snapchat": {
        "spotlight_max_per_day": 10,
        "recommended_per_day": 1,
        "max_video_size_mb": 32,
        "max_video_duration_seconds": 60,
        "min_video_duration_seconds": 5,
        "max_caption_length": 250,
        "aspect_ratio": "9:16",
        "min_resolution": "1080x1920",
        "tos_key_points": [
            "Content must meet Spotlight eligibility criteria",
            "No misleading or deceptive content",
            "Original content only",
            "Must comply with Snap's Community Guidelines",
            "AI-generated content should follow disclosure guidelines"
        ]
    }
}
```

### 4.2 YouTube Quota Management — SIMPLIFIED

**V6.0 change: Start with ONE GCP project, not six.**

With careful scheduling:
- 6 brands × 1 YouTube upload/day = 6 uploads = 9,600 quota units (6 × 1,600)
- This fits within a single project's 10,000 daily limit
- Only split into multiple projects IF you need >6 uploads/day

```python
# config/youtube_projects.json
{
  "youtube_projects": {
    "_default": {
      "project_id": "autofarm-success-guru-yt",
      "quota_units_per_day": 10000,
      "max_uploads_per_day": 6,
      "notes": "Single project for all brands. Split only if quota insufficient."
    }
  },
  "brand_assignment": {
    "human_success_guru": "_default",
    "wealth_success_guru": "_default",
    "zen_success_guru": "_default",
    "social_success_guru": "_default",
    "habits_success_guru": "_default",
    "relationships_success_guru": "_default"
  }
}
```

### 4.3 Rate Limit Manager

```python
# modules/compliance/rate_limit_manager.py
class RateLimitManager:
    """
    Central rate limit enforcement for all platform API calls.
    Every API call MUST go through this manager.
    Tracks calls per brand per platform per endpoint per time window.
    Raises RateLimitExceeded before making a call that would breach limits.
    """

    def check_and_increment(self, brand_id: str, platform: str,
                             endpoint: str, units: int = 1) -> bool:
        """
        Checks if this call is within rate limits.
        If yes: increments counter and returns True.
        If no: raises RateLimitExceeded with retry_after seconds.
        Thread-safe (uses SQLite WAL mode + application-level lock).
        """

    def get_remaining_quota(self, brand_id: str, platform: str) -> dict:
        """Returns {daily_remaining, hourly_remaining, next_reset_utc, can_upload: bool}"""

    def reset_hourly_counters(self):
        """Called every hour by cron."""

    def reset_daily_counters(self):
        """Called at midnight UTC. Resets daily quotas and YouTube units."""

    def get_network_wide_status(self) -> dict:
        """Returns quota status for all brands × platforms."""

    def estimate_next_available_slot(self, brand_id: str, platform: str) -> 'datetime':
        """Calculates when next upload is possible."""
```

### 4.4 Platform Compliance Checker


```python
# modules/compliance/platform_compliance.py
class PlatformComplianceChecker:
    """
    Pre-publish compliance verification.
    Every publish_job passes through this before the API call is made.
    """

    def check_all(self, brand_id: str, platform: str,
                   video_path: str, caption: str,
                   hashtags: list[str]) -> 'ComplianceResult':
        """Runs all compliance checks. Returns {passed, issues, warnings}."""

    def check_video_specs(self, video_path: str, platform: str) -> list[str]:
        """Checks duration, resolution, aspect ratio, file size, codec."""

    def check_content_uniqueness(self, video_path: str, brand_id: str,
                                   platform: str) -> bool:
        """
        Computes perceptual hash of first/middle/last frames.
        Checks against recently published videos across ALL brands.
        Same video across different platforms is fine — same video same platform is not.
        """

    def check_caption_compliance(self, caption: str, platform: str) -> list[str]:
        """Checks length limits, banned words, hashtag count limits."""

    def check_posting_frequency(self, brand_id: str, platform: str) -> bool:
        """Verifies minimum gap since last post and daily post count."""

    def check_ai_disclosure_required(self, platform: str) -> bool:
        """
        TikTok: YES (required). YouTube: REQUIRED for realistic synthetic content.
        Instagram/Facebook/Snapchat: RECOMMENDED.
        """

    def apply_ai_disclosure(self, publish_params: dict, platform: str) -> dict:
        """Adds required AI disclosure flags to the upload API call parameters."""
```


### 4.5 Anti-Spam Fingerprint Variation

```python
# modules/compliance/anti_spam.py
class AntiSpamVariator:
    """
    Applies subtle variations to each video before platform upload
    to avoid perceptual fingerprinting across accounts.
    """

    def vary_video_for_platform(self, input_path: str, brand_id: str,
                                 platform: str) -> str:
        """
        Applies one or more of:
        1. Slightly different CRF (±1)
        2. Imperceptible colour saturation micro-adjustment (±2%)
        3. Trim first/last 0.1-0.3 seconds
        4. Vary output resolution slightly within platform tolerance
        5. Unique metadata (random encoding timestamp, tool version)
        Returns path to varied output file.
        """

    def vary_caption(self, base_caption: str, platform: str) -> str:
        """Reword first sentence, vary emoji placement, rotate CTA phrase."""

    def vary_hashtags(self, hashtag_pool: list[str], count: int,
                       recent_used: list[list[str]]) -> list[str]:
        """Selects hashtags with ≤60% overlap with last 5 posts' hashtag sets."""

    def generate_unique_metadata(self, brand_id: str) -> dict:
        """Generates unique-per-upload metadata fields."""
```

---

### 4.6 Cross-Brand Deduplication (NEW)

```python
# modules/compliance/cross_brand_dedup.py
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np

class CrossBrandDeduplicator:
    """
    Ensures content is genuinely distinct across brands.
    Platforms (especially Meta) detect coordinated content networks.

    Maintains a rolling window of recent scripts per brand.
    New scripts are rejected if >0.7 cosine similarity to any other brand's
    recent content (last 50 scripts per brand).
    """

    SIMILARITY_THRESHOLD = 0.7
    WINDOW_SIZE = 50  # Scripts per brand to compare against

    def check_script_uniqueness(self, script_text: str,
                                  brand_id: str) -> dict:
        """
        Returns {unique: bool, most_similar_brand: str, similarity: float}
        """
        from database.db import Database
        db = Database()

        # Get recent scripts from OTHER brands
        other_scripts = db.query(
            """SELECT brand_id, script_text FROM scripts
               WHERE brand_id != ? AND created_at > datetime('now', '-30 days')
               ORDER BY created_at DESC LIMIT ?""",
            (brand_id, self.WINDOW_SIZE * 5)
        )

        if not other_scripts:
            return {'unique': True, 'most_similar_brand': None, 'similarity': 0.0}

        corpus = [script_text] + [r['script_text'] for r in other_scripts]
        vectorizer = TfidfVectorizer(stop_words='english', max_features=500)
        tfidf_matrix = vectorizer.fit_transform(corpus)

        similarities = cosine_similarity(tfidf_matrix[0:1], tfidf_matrix[1:])[0]
        max_idx = np.argmax(similarities)
        max_sim = similarities[max_idx]

        return {
            'unique': max_sim < self.SIMILARITY_THRESHOLD,
            'most_similar_brand': other_scripts[max_idx]['brand_id'],
            'similarity': round(float(max_sim), 3),
        }
```

---

## PART 5 — HIGH-QUALITY BRAND-ALIGNED BACKGROUNDS

### 5.1 Background Strategy

**Background Source Priority:**
1. **Pexels API** (free, high quality, programmatic search)
2. **Pixabay API** (free, large library, programmatic search)
3. **FFmpeg-generated motion graphics** (mathematical animations — always available, brand-unique, guaranteed fallback)

No manual library. No Coverr. Fully automated background sourcing from both APIs.

### 5.2 Brand Background Libraries

```python
# modules/content_forge/background_library.py

BRAND_BACKGROUND_PROFILES = {
    "human_success_guru": {
        "themes": [
            "dark silhouette person standing city night",
            "abstract dark particles flowing",
            "slow motion dark smoke wisps",
            "dark corridor light end cinematic",
            "chessboard pieces dark dramatic lighting",
            "storm clouds dark dramatic time-lapse",
            "dark water ripples abstract close-up"
        ],
        "pexels_queries": [
            "dark cinematic abstract motion",
            "silhouette person dramatic lighting",
            "dark abstract particles bokeh"
        ],
        "color_treatment": "desaturate_70_crush_blacks_add_grain",
        "motion_speed": 0.4,
        "generated_fallback": "dark_particle_field"
    },
    "wealth_success_guru": {
        "themes": [
            "city skyline night aerial time-lapse",
            "financial chart data visualization abstract",
            "gold coins falling slow motion",
            "modern office building exterior night",
            "dark luxury interior minimal",
            "stock market data blur abstract green"
        ],
        "pexels_queries": [
            "city lights night aerial",
            "financial abstract dark",
            "luxury minimal dark interior"
        ],
        "color_treatment": "push_greens_desaturate_others_sharpen",
        "motion_speed": 0.6,
        "generated_fallback": "financial_data_stream"
    },
    "zen_success_guru": {
        "themes": [
            "still water reflection dawn",
            "slow motion stone drop water",
            "morning mist forest slow",
            "abstract marble texture slow pan",
            "zen garden sand patterns close-up",
            "single candle flame dark still",
            "slow motion clouds passing moon"
        ],
        "pexels_queries": [
            "peaceful nature minimal slow motion",
            "water reflection calm",
            "zen minimalist nature"
        ],
        "color_treatment": "desaturate_90_cool_tone_very_soft",
        "motion_speed": 0.2,
        "generated_fallback": "slow_gradient_breathe"
    },
    "social_success_guru": {
        "themes": [
            "crowd city walking time-lapse night",
            "bokeh city lights abstract",
            "hands shaking professional close-up",
            "network connection nodes abstract",
            "conference room people silhouettes",
            "dark abstract light particles social"
        ],
        "pexels_queries": [
            "city crowd abstract night",
            "connection network abstract blue",
            "professional meeting dark"
        ],
        "color_treatment": "push_blues_electric_contrast_high",
        "motion_speed": 0.7,
        "generated_fallback": "network_node_pulse"
    },
    "habits_success_guru": {
        "themes": [
            "sunrise time-lapse nature",
            "morning routine person silhouette sunrise",
            "running athlete dawn fog slow",
            "writing journal notebook close-up warm light",
            "plants growing time-lapse green",
            "coffee steam morning light bokeh",
            "forest path morning light rays"
        ],
        "pexels_queries": [
            "sunrise morning routine nature",
            "growth progress nature warm",
            "habit morning motivation nature"
        ],
        "color_treatment": "warm_grade_lift_shadows_amber_tint",
        "motion_speed": 0.65,
        "generated_fallback": "sunrise_horizon_rise"
    },
    "relationships_success_guru": {
        "themes": [
            "two people silhouette sunset",
            "candlelight bokeh warm dark intimate",
            "couple hands close-up warm light",
            "heart rate pulse abstract dark warm",
            "letters envelope vintage warm",
            "city rain window bokeh warm",
            "two chairs facing sunset abstract"
        ],
        "pexels_queries": [
            "intimate warm dark bokeh",
            "couple connection abstract warm",
            "love emotion abstract warm lighting"
        ],
        "color_treatment": "warm_burgundy_grade_soft_vignette_heavy",
        "motion_speed": 0.5,
        "generated_fallback": "warm_particle_drift"
    }
}

# API endpoints and search params
PEXELS_VIDEO_ENDPOINT = "https://api.pexels.com/videos/search"
PIXABAY_VIDEO_ENDPOINT = "https://pixabay.com/api/videos/"

PEXELS_PARAMS = {
    'orientation': 'portrait',
    'size': 'large',
    'per_page': 10,
    'min_duration': 8,
    'max_duration': 30,
}

PIXABAY_PARAMS = {
    'video_type': 'film',
    'orientation': 'vertical',
    'min_width': 1080,
    'per_page': 10,
    'safesearch': 'true',
}

# Rate limits for background fetching (enforced by RateLimitManager)
BACKGROUND_API_LIMITS = {
    'pexels': {'requests_per_hour': 200},
    'pixabay': {'requests_per_hour': 100},
}
# Total per weekly maintenance: 6 brands × 5 requests × 2 APIs = 60 requests
```

### 5.3 Background Manager

```python
class BackgroundManager:
    """
    Manages background video selection, downloading, caching, and quality scoring.
    Always has a background available — never fails.
    """

    def get_background(self, brand_id: str, duration_seconds: float) -> str:
        """
        Priority:
        1. Check local brand library (media/brand_assets/{brand}/backgrounds/)
        2. Check broll_cache for previously downloaded brand-themed clips
        3. Fetch from Pexels (niche-matched query)
        4. Fetch from Pixabay (niche-matched query)
        5. Generate FFmpeg motion graphic (guaranteed fallback)
        Returns path to a usable background video file.
        """

    def score_background(self, video_path: str, brand_id: str) -> float:
        """Scores 0.0-1.0 for colour palette match, motion, quality."""

    def apply_brand_treatment(self, background_path: str, brand_id: str,
                               output_path: str) -> str:
        """Applies brand-specific FFmpeg colour treatment."""

    def generate_fallback_background(self, brand_id: str,
                                      duration_seconds: float) -> str:
        """
        Generates brand-appropriate mathematical animation via FFmpeg.
        Types: dark_particle_field, financial_data_stream, slow_gradient_breathe,
               network_node_pulse, sunrise_horizon_rise, warm_particle_drift
        """

    def maintain_library(self):
        """Weekly cron: download 5 new clips/brand, score, prune low-quality."""

    def pre_download_starter_library(self):
        """Setup: download 5 high-quality clips per brand via Pexels/Pixabay."""
```

### 5.4 FFmpeg Brand-Specific Fallback Generators

```python
def generate_dark_particle_field(duration: float, brand_colors: dict, output: str):
    """Uses FFmpeg tesrc2 + colorize + geq filters for particle field animation."""

def generate_sunrise_horizon(duration: float, brand_colors: dict, output: str):
    """Uses FFmpeg gradient + hue animation to simulate a slow sunrise."""

# ... one generator per brand fallback type
```

---

## PART 6 — PUBLISHING SCHEDULES WITH INTELLIGENT TIMING

### 6.1 Schedule Architecture

Content is **always created in advance** and waits in the queue. Publishing is a separate pipeline that reads from the queue and publishes at optimal, randomised times.

```
CONTENT PIPELINE (continuous)                PUBLISH PIPELINE (scheduled)
================================             ================================
Trend scan (every 2h)                        Midnight: reset daily counts
  ↓                                          04:00: refresh OAuth tokens
Generate scripts                             05:45: check approval queue
  ↓                                          06:00: first publish window
Assemble videos                              10:00: second publish window
  ↓                                          14:00: third publish window
Add to pending_review queue                  18:00: fourth publish window
  ↓                                          21:00: fifth publish window
Review gate                                  Each window: ±30min random
  ↓                                          Each window: brand rotates
Approved videos                              Next day: learn from analytics
→ publish_queue (ready to post)              Adjust windows toward peak
```

### 6.2 Optimal Posting Windows Per Brand Per Platform

Based on platform research for psychology, finance, wellness, and relationship content targeting primarily UK and US audiences:

```python
# modules/publish_engine/schedule_config.py

POSTING_WINDOWS_UTC = {
    # Windows defined as [hour, minute] TARGET times
    # Actual publish time = target ± random(0, 59) minutes
    # Random offset = deterministic from hash(brand_id + platform + date + window_index)

    "human_success_guru": {
        "tiktok":    {"windows": [[6,0],[12,30],[19,0]], "best_days": [1,2,3,4,5], "daily_limit": 2},
        "instagram": {"windows": [[7,0],[19,30]],       "best_days": [1,2,3,4,5], "daily_limit": 1},
        "facebook":  {"windows": [[12,0]],              "best_days": [1,2,3,5],   "daily_limit": 1},
        "youtube":   {"windows": [[8,0],[15,0]],        "best_days": [1,2,3,4,5,6,7], "daily_limit": 2},
        "snapchat":  {"windows": [[18,0]],              "best_days": [1,2,3,4,5,6,7], "daily_limit": 1},
    },
    "wealth_success_guru": {
        "tiktok":    {"windows": [[6,30],[11,0],[17,30]], "best_days": [1,2,3,4,5], "daily_limit": 2},
        "instagram": {"windows": [[7,30],[12,0]],        "best_days": [1,2,3,4],   "daily_limit": 1},
        "facebook":  {"windows": [[13,0]],               "best_days": [2,3,4],     "daily_limit": 1},
        "youtube":   {"windows": [[7,0],[16,0]],         "best_days": [1,2,3,4,5], "daily_limit": 2},
        "snapchat":  {"windows": [[17,30]],              "best_days": [1,2,3,4,5,6], "daily_limit": 1},
    },
    "zen_success_guru": {
        "tiktok":    {"windows": [[6,0],[20,0]],  "best_days": [1,2,3,4,5,6,7], "daily_limit": 1},
        "instagram": {"windows": [[7,0]],         "best_days": [1,3,5,7],       "daily_limit": 1},
        "facebook":  {"windows": [[8,30]],        "best_days": [1,4,7],         "daily_limit": 1},
        "youtube":   {"windows": [[7,30]],        "best_days": [1,2,3,4,5,6,7], "daily_limit": 1},
        "snapchat":  {"windows": [[19,0]],        "best_days": [1,3,5,7],       "daily_limit": 1},
    },
    "social_success_guru": {
        "tiktok":    {"windows": [[7,30],[12,0],[18,30]], "best_days": [1,2,3,4,5], "daily_limit": 2},
        "instagram": {"windows": [[8,0],[18,0]],         "best_days": [1,2,3,4,5], "daily_limit": 1},
        "facebook":  {"windows": [[13,30]],              "best_days": [2,3,4,5],   "daily_limit": 1},
        "youtube":   {"windows": [[8,30],[17,0]],        "best_days": [1,2,3,4,5,6], "daily_limit": 2},
        "snapchat":  {"windows": [[18,30]],              "best_days": [1,2,3,4,5,6], "daily_limit": 1},
    },
    "habits_success_guru": {
        "tiktok":    {"windows": [[5,30],[11,30],[19,30]], "best_days": [1,2,3,4,5,6,7], "daily_limit": 2},
        "instagram": {"windows": [[6,0],[19,0]],          "best_days": [1,2,3,4,5,6,7], "daily_limit": 1},
        "facebook":  {"windows": [[8,0]],                 "best_days": [1,2,3,4,5],     "daily_limit": 1},
        "youtube":   {"windows": [[6,30],[14,0]],         "best_days": [1,2,3,4,5,6,7], "daily_limit": 2},
        "snapchat":  {"windows": [[7,0]],                 "best_days": [1,2,3,4,5,6,7], "daily_limit": 1},
    },
    "relationships_success_guru": {
        "tiktok":    {"windows": [[9,0],[20,0],[22,0]], "best_days": [1,2,3,4,5,6,7], "daily_limit": 2},
        "instagram": {"windows": [[9,30],[20,30]],      "best_days": [1,2,3,4,5,6,7], "daily_limit": 1},
        "facebook":  {"windows": [[14,0]],              "best_days": [1,2,3,4,5,6,7], "daily_limit": 1},
        "youtube":   {"windows": [[10,0],[19,0]],       "best_days": [1,2,3,4,5,6,7], "daily_limit": 2},
        "snapchat":  {"windows": [[20,0]],              "best_days": [1,2,3,4,5,6,7], "daily_limit": 1},
    }
}
```

### 6.3 Smart Scheduler with Time Randomisation

```python
# modules/publish_engine/scheduler.py
class SmartScheduler:
    """
    Calculates optimal, varied publish times for each pending job.
    Learns from analytics to improve window selection over time.
    """

    RANDOM_WINDOW_MINUTES = 60

    def calculate_publish_time(self, brand_id: str, platform: str,
                                content_ready_at: 'datetime') -> 'datetime':
        """
        Algorithm:
        1. Get posting windows for brand × platform
        2. Check which windows still available today (past gap since last post)
        3. Among available windows, select highest-performing (from analytics)
        4. Add deterministic offset:
           offset = int(md5(f"{brand_id}{platform}{date}{window_idx}").hexdigest(), 16) % 61 - 30
           (±30 minutes, same result each run = stable)
        5. Verify no other brand on same platform within ±5 minutes
        6. Return final datetime
        """

    def get_performance_ranked_windows(self, brand_id: str,
                                        platform: str) -> list[dict]:
        """Queries analytics, groups by hour, calculates avg CPS. Falls back to defaults if < 20 data points."""

    def schedule_batch(self, video_ids: list[int]) -> list[dict]:
        """Distributes multiple videos across windows/days. Enforces daily_limit."""

    def get_next_24h_schedule(self) -> dict:
        """Returns {brand_id: {platform: [scheduled_datetimes]}}"""

    def reoptimise_windows(self):
        """Weekly: analyses 30 days, shifts windows toward highest-CPS hours. Max 30min shift/week."""
```

---

## PART 7 — CONTENT PRE-PRODUCTION QUEUE SYSTEM

### 7.1 Queue Architecture

```python
# modules/queue/content_queue.py
class ContentQueue:
    """
    Manages the full content pre-production pipeline.
    Target: Always maintain QUEUE_TARGET_DAYS_AHEAD days of content per brand per platform.
    """

    QUEUE_TARGET_DAYS_AHEAD = 3

    def get_queue_depth(self, brand_id: str, platform: str) -> int:
        """Returns number of approved, ready-to-publish videos in queue."""

    def needs_more_content(self, brand_id: str) -> bool:
        """Returns True if any platform for this brand has < target days of content ready."""

    def add_to_queue(self, video_id: int, brand_id: str) -> bool:
        """After review approval, adds video to publish queue with scheduled time."""

    def get_next_ready(self, brand_id: str, platform: str) -> dict | None:
        """Returns next video ready for this brand × platform at scheduled time."""

    def get_queue_status(self) -> dict:
        """Returns {brand_id: {platform: {queued, days_ahead, next_post}}}"""

    def flush_expired(self):
        """Removes videos from queue that are > 14 days old (stale content)."""
```

### 7.2 Complete Cron Schedule

```cron
# /etc/cron.d/autofarm — Full production cron schedule (V6.0)

# === CONTENT GENERATION (content-vm) ===
# Every 2 hours: scan trends and generate content if queue is low
# Resource-aware: job_scheduler checks RAM/CPU before starting
0 */2 * * *  autofarm  python /app/jobs/scan_and_generate.py >> /app/logs/generate.log 2>&1

# Every 15 minutes: send pending review via Telegram (primary) or email (fallback)
*/15 * * * *  autofarm  python /app/jobs/process_review_queue.py >> /app/logs/review.log 2>&1

# Every 30 minutes: check for auto-approval threshold expiry
*/30 * * * *  autofarm  python /app/jobs/check_auto_approvals.py >> /app/logs/review.log 2>&1

# === PUBLISHING (content-vm → proxy-vm) ===
# Every 5 minutes: check if any scheduled post is due
*/5 * * * *  autofarm  python /app/jobs/publish_due.py >> /app/logs/publish.log 2>&1

# Token refresh — 15 minutes before first daily publish window
45 4 * * *  autofarm  python /app/jobs/refresh_tokens.py >> /app/logs/tokens.log 2>&1

# === ANALYTICS & OPTIMISATION ===
0 3 * * *  autofarm  python /app/jobs/pull_analytics.py >> /app/logs/analytics.log 2>&1

# Background library maintenance (weekly Monday)
0 2 * * 1  autofarm  python /app/jobs/maintain_backgrounds.py >> /app/logs/backgrounds.log 2>&1

# Posting window optimisation (weekly Monday)
0 4 * * 1  autofarm  python /app/jobs/reoptimise_schedule.py >> /app/logs/schedule.log 2>&1

# === HOUSEKEEPING ===
0 0 * * *  autofarm  python /app/jobs/reset_daily_counts.py
1 0 * * *  autofarm  python /app/jobs/reset_api_quotas.py
0 8 * * *  autofarm  python /app/jobs/send_daily_digest.py
0 6 * * *  autofarm  python /app/jobs/check_storage.py >> /app/logs/health.log 2>&1
0 * * * *  autofarm  python /app/jobs/check_queue_depth.py
30 2 * * *  autofarm  python /app/jobs/backup_database.py >> /app/logs/backup.log 2>&1

# Google Drive cleanup (daily — only if Drive is enabled)
0 5 * * *  autofarm  python /app/jobs/cleanup_gdrive.py >> /app/logs/gdrive.log 2>&1

# Orphaned file cleanup (daily — partial video assemblies, temp files)
0 4 * * *  autofarm  python /app/jobs/cleanup_orphans.py >> /app/logs/cleanup.log 2>&1

# Config validation (daily)
30 5 * * *  autofarm  python /app/jobs/validate_config.py >> /app/logs/health.log 2>&1

# User agent refresh (monthly, 1st of month)
0 3 1 * *  autofarm  python /app/jobs/refresh_user_agents.py >> /app/logs/health.log 2>&1
```

---

## PART 8 — REVIEW SYSTEM

### 8.1 Telegram Bot Review (Primary)

*[See Part 2.4 above for TelegramReviewer class]*

### 8.2 Email Review with Google Drive (Fallback)


```python
# modules/review_gate/email_sender.py (updated)
class ReviewEmailSender:

    def send_review_email(self, review_id: int) -> bool:
        """
        Enhanced review email includes:
        1. THUMBNAIL: Embedded inline as base64 PNG (≈100KB)
        2. VIDEO: Uploaded to Google Drive, preview URL in "Watch" button
        3. SCRIPT: Full voiceover script formatted in readable sections
        4. METRICS: Duration, word count, hook type, series info
        5. PLATFORM TARGETS: Which platforms this will publish to
        6. APPROVE/REJECT: Two large CTA buttons
        7. EXPIRY NOTICE: When review expires (if auto-approve set)
        8. VIDEO LINK EXPIRY: "Video link expires in 14 days"
        """

    def _upload_to_gdrive_and_get_url(self, video_path: str,
                                        thumbnail_path: str,
                                        review_token: str,
                                        brand_id: str) -> tuple[str, str]:
        """
        Uploads video + thumbnail to Google Drive.
        Returns (video_preview_url, thumbnail_direct_url).
        """

    def _embed_thumbnail(self, thumbnail_path: str) -> str:
        """Converts thumbnail PNG to base64 data URI (480×270)."""

    def _build_brand_html_email(self, brand_config: dict,
                                  review_data: dict,
                                  video_url: str,
                                  thumbnail_b64: str,
                                  approval_base_url: str) -> str:
        """
        Builds complete HTML email with brand colours.
        Uses brand primary_color for header, accent_color for CTA buttons.
        """
```

### 8.3 Review Email HTML Structure

```html
<!-- VIDEO SECTION in review email -->
<table width="100%" cellpadding="0" cellspacing="0">
  <tr>
    <td align="center" style="padding: 20px 0;">

      <!-- Thumbnail (always visible — base64 embedded) -->
      <img src="{thumbnail_base64_data_uri}"
           width="480" height="270"
           style="border-radius: 8px; border: 2px solid {brand_accent_color};"
           alt="Video thumbnail"/>

      <!-- Drive embed (renders in most email clients) -->
      <div style="margin-top: 16px;">
        <iframe src="{google_drive_preview_url}"
          width="480" height="270" allow="autoplay"
          style="border: none; border-radius: 8px;">
        </iframe>
      </div>

      <!-- Fallback button for clients that block iframes -->
      <div style="margin-top: 12px;">
        <a href="{google_drive_preview_url}"
           style="background-color: #4285f4; color: white; padding: 12px 24px;
                  border-radius: 6px; text-decoration: none; font-size: 14px;">
          ▶ Watch on Google Drive
        </a>
      </div>

      <p style="font-size: 11px; color: #999; margin-top: 8px;">
        Video link expires in 14 days and is deleted after review.
      </p>
    </td>
  </tr>
</table>
```

---

## PART 9 — INFRASTRUCTURE RESILIENCE (NEW/EXPANDED)

### 9.1 OCI Idle Instance Guard

```python
# modules/infrastructure/idle_guard.py
import psutil
import time
import subprocess
import logging

logger = logging.getLogger(__name__)

class IdleGuard:
    """
    Prevents OCI from reclaiming Always Free instances due to low usage.

    Oracle's criteria for "idle" (ALL must be true over 7 days):
    - CPU utilisation 95th percentile < 20%
    - Memory utilisation < 20% (A1 shapes)
    - Network utilisation < 20%

    This daemon runs as a supervisord process and:
    1. Monitors system metrics every 60 seconds
    2. If CPU drops below 15% for >30 minutes, triggers a light workload
    3. The "workload" is useful work: SQLite ANALYZE, log compression, etc.
    4. If no useful work available, runs a brief CPU exercise (10s)

    IMPORTANT: This is NOT about faking usage. The system genuinely uses
    resources during content generation. This guard only covers the gaps
    between generation cycles.
    """

    CPU_FLOOR_PERCENT = 15
    CHECK_INTERVAL_SECONDS = 60
    LOW_CPU_THRESHOLD_MINUTES = 30

    def run(self):
        """Main daemon loop. Run via supervisord."""
        low_cpu_since = None

        while True:
            cpu_percent = psutil.cpu_percent(interval=5)
            mem_percent = psutil.virtual_memory().percent

            if cpu_percent < self.CPU_FLOOR_PERCENT:
                if low_cpu_since is None:
                    low_cpu_since = time.time()
                elif time.time() - low_cpu_since > self.LOW_CPU_THRESHOLD_MINUTES * 60:
                    self._do_useful_work()
                    low_cpu_since = None
            else:
                low_cpu_since = None

            time.sleep(self.CHECK_INTERVAL_SECONDS)

    def _do_useful_work(self):
        """Performs genuinely useful maintenance tasks to raise CPU usage."""
        tasks = [
            self._sqlite_maintenance,
            self._compress_old_logs,
            self._verify_file_integrity,
            self._update_search_index,
        ]
        for task in tasks:
            try:
                task()
            except Exception as e:
                logger.warning(f"Idle guard task failed: {e}")

    def _sqlite_maintenance(self):
        """ANALYZE and integrity check on database."""
        from database.db import Database
        db = Database()
        db.execute("ANALYZE")
        db.execute("PRAGMA integrity_check")

    def _compress_old_logs(self):
        """Gzip logs older than 1 day."""
        subprocess.run(
            ['find', '/app/logs', '-name', '*.log', '-mtime', '+1',
             '-exec', 'gzip', '-q', '{}', ';'],
            capture_output=True
        )

    def _verify_file_integrity(self):
        """Checksums on recent video files."""
        import hashlib
        from pathlib import Path
        for video in Path('/app/media/output').glob('*.mp4'):
            if video.stat().st_mtime > time.time() - 86400:
                hashlib.md5(video.read_bytes()).hexdigest()

    def _update_search_index(self):
        """Rebuild FTS index for scripts (if using FTS5)."""
        pass
```

### 9.2 Resource-Aware Job Scheduler

```python
# modules/infrastructure/resource_scheduler.py
import psutil
import time
import logging

logger = logging.getLogger(__name__)

class ResourceScheduler:
    """
    Controls concurrency of heavy jobs based on system resources.
    Prevents OOM kills on the 20GB content-vm.

    Resource thresholds:
    - Video assembly: requires 4GB free RAM, <70% CPU
    - TTS generation: requires 2GB free RAM
    - LLM inference: requires 6GB free RAM (Ollama model loading)
    - Background download: requires 1GB free RAM, <50% disk used

    Only 1 video assembly job runs at a time.
    TTS and LLM never run concurrently with video assembly.
    """

    THRESHOLDS = {
        'video_assembly': {'min_free_ram_gb': 4, 'max_cpu_percent': 70},
        'tts_generation': {'min_free_ram_gb': 2, 'max_cpu_percent': 80},
        'llm_inference':  {'min_free_ram_gb': 6, 'max_cpu_percent': 80},
        'background_download': {'min_free_ram_gb': 1, 'max_disk_percent': 80},
    }

    def can_start_job(self, job_type: str) -> tuple[bool, str]:
        """
        Returns (can_start, reason).
        If can_start is False, caller should retry after delay.
        """
        if job_type not in self.THRESHOLDS:
            return True, "No resource constraints for this job type"

        thresholds = self.THRESHOLDS[job_type]
        mem = psutil.virtual_memory()
        free_ram_gb = mem.available / (1024**3)
        cpu_percent = psutil.cpu_percent(interval=1)
        disk = psutil.disk_usage('/app')

        if free_ram_gb < thresholds.get('min_free_ram_gb', 0):
            return False, f"Insufficient RAM: {free_ram_gb:.1f}GB free, need {thresholds['min_free_ram_gb']}GB"

        if cpu_percent > thresholds.get('max_cpu_percent', 100):
            return False, f"CPU too high: {cpu_percent}%, max {thresholds['max_cpu_percent']}%"

        if disk.percent > thresholds.get('max_disk_percent', 100):
            return False, f"Disk too full: {disk.percent}%, max {thresholds['max_disk_percent']}%"

        return True, "Resources available"

    def wait_for_resources(self, job_type: str, max_wait_seconds: int = 600) -> bool:
        """Blocks until resources are available or timeout."""
        start = time.time()
        while time.time() - start < max_wait_seconds:
            can_start, reason = self.can_start_job(job_type)
            if can_start:
                return True
            logger.info(f"Waiting for resources ({job_type}): {reason}")
            time.sleep(30)
        return False
```

### 9.3 Retry Handler with Exponential Backoff

```python
# modules/infrastructure/retry_handler.py
import time
import random
import logging
from functools import wraps

logger = logging.getLogger(__name__)

def retry_with_backoff(max_retries: int = 5, base_delay: float = 1.0,
                        max_delay: float = 60.0, jitter: float = 0.25,
                        retry_on: tuple = (Exception,)):
    """
    Decorator for exponential backoff with jitter.
    Sequence: 1s → 2s → 4s → 8s → 16s (capped at max_delay)
    Jitter: ±25% of delay (prevents thundering herd)

    Usage:
        @retry_with_backoff(max_retries=3, retry_on=(requests.Timeout, ConnectionError))
        def upload_video(session, video_path):
            ...
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except retry_on as e:
                    last_exception = e
                    if attempt == max_retries:
                        logger.error(f"{func.__name__} failed after {max_retries} retries: {e}")
                        raise
                    delay = min(base_delay * (2 ** attempt), max_delay)
                    delay *= (1 + random.uniform(-jitter, jitter))
                    logger.warning(
                        f"{func.__name__} attempt {attempt+1}/{max_retries} failed: {e}. "
                        f"Retrying in {delay:.1f}s"
                    )
                    time.sleep(delay)
            raise last_exception
        return wrapper
    return decorator
```

### 9.4 Job State Machine

```python
# modules/infrastructure/job_state_machine.py
from enum import Enum

class JobState(Enum):
    TREND_FOUND = "trend_found"
    SCRIPT_DRAFT = "script_draft"
    SCRIPT_SAFETY_CHECK = "script_safety_check"
    TTS_QUEUED = "tts_queued"
    TTS_DONE = "tts_done"
    VIDEO_ASSEMBLY = "video_assembly"
    VIDEO_ASSEMBLED = "video_assembled"
    QUALITY_CHECK = "quality_check"
    QUALITY_PASSED = "quality_passed"
    REVIEW_PENDING = "review_pending"
    REVIEW_APPROVED = "review_approved"
    REVIEW_REJECTED = "review_rejected"
    SCHEDULED = "scheduled"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"

VALID_TRANSITIONS = {
    JobState.TREND_FOUND: [JobState.SCRIPT_DRAFT, JobState.FAILED],
    JobState.SCRIPT_DRAFT: [JobState.SCRIPT_SAFETY_CHECK, JobState.FAILED],
    JobState.SCRIPT_SAFETY_CHECK: [JobState.TTS_QUEUED, JobState.SCRIPT_DRAFT, JobState.FAILED],
    JobState.TTS_QUEUED: [JobState.TTS_DONE, JobState.FAILED],
    JobState.TTS_DONE: [JobState.VIDEO_ASSEMBLY, JobState.FAILED],
    JobState.VIDEO_ASSEMBLY: [JobState.VIDEO_ASSEMBLED, JobState.FAILED],
    JobState.VIDEO_ASSEMBLED: [JobState.QUALITY_CHECK, JobState.FAILED],
    JobState.QUALITY_CHECK: [JobState.QUALITY_PASSED, JobState.SCRIPT_DRAFT, JobState.FAILED],
    JobState.QUALITY_PASSED: [JobState.REVIEW_PENDING],
    JobState.REVIEW_PENDING: [JobState.REVIEW_APPROVED, JobState.REVIEW_REJECTED],
    JobState.REVIEW_APPROVED: [JobState.SCHEDULED],
    JobState.SCHEDULED: [JobState.PUBLISHING, JobState.FAILED],
    JobState.PUBLISHING: [JobState.PUBLISHED, JobState.FAILED],
    JobState.FAILED: [JobState.TREND_FOUND, JobState.SCRIPT_DRAFT, JobState.TTS_QUEUED,
                       JobState.VIDEO_ASSEMBLY],  # Retry from any earlier state
}

class JobStateMachine:
    """
    Tracks content jobs through their lifecycle.
    Failed jobs can be retried from their last successful state.
    Orphan detection: jobs stuck in non-terminal states for >24h are flagged.
    """

    def transition(self, job_id: int, new_state: JobState) -> bool:
        """Validates and records state transition."""

    def get_retryable_jobs(self) -> list[dict]:
        """Returns jobs in FAILED state that can be retried."""

    def get_orphaned_jobs(self, max_age_hours: int = 24) -> list[dict]:
        """Returns jobs stuck in non-terminal states beyond max_age."""

    def cleanup_orphans(self):
        """Daily job: marks old orphans as FAILED, deletes partial files."""
```

### 9.5 Circuit Breaker (from V5.1, unchanged)

```python
# modules/infrastructure/circuit_breaker.py
class CircuitBreaker:
    """
    Prevents cascade failures when a platform API is down.
    States: CLOSED → OPEN (5 failures → 15min timeout) → HALF_OPEN (test)
    Jobs for open circuits are skipped and rescheduled, not failed.
    """
    FAILURE_THRESHOLD = 5
    TIMEOUT_SECONDS = 900
```

### 9.6 Health Monitor (from V5.1, expanded)

```python
# modules/infrastructure/health_monitor.py
class HealthMonitor:
    """
    Expanded from V5.1. New checks:
    - Ollama health and response latency
    - Groq rate limit remaining
    - Telegram bot connectivity
    - Orphaned job count
    - Swap usage (should be near zero normally)
    - OCI idle guard status
    """

    def full_health_check(self) -> dict:
        """Returns comprehensive system health for /health endpoint and daily digest."""
        return {
            'system': {
                'cpu_percent': psutil.cpu_percent(),
                'ram_used_gb': psutil.virtual_memory().used / (1024**3),
                'ram_total_gb': psutil.virtual_memory().total / (1024**3),
                'swap_used_gb': psutil.swap_memory().used / (1024**3),
                'disk_used_percent': psutil.disk_usage('/app').percent,
            },
            'services': {
                'ollama': self._check_ollama(),
                'sqlite': self._check_sqlite(),
                'squid_proxies': self._check_all_proxies(),
                'telegram_bot': self._check_telegram(),
            },
            'api_quotas': {
                'groq': self._check_groq_quota(),
                'youtube': self._check_youtube_quota(),
                'pexels': self._check_pexels_quota(),
            },
            'content_pipeline': {
                'queue_depth': self._check_queue_depth(),
                'orphaned_jobs': self._count_orphaned_jobs(),
                'last_publish_per_brand': self._last_publish_times(),
                'review_queue_age': self._oldest_pending_review(),
            },
            'storage': {
                'oci_object_storage': self._check_oci_storage(),
                'google_drive': self._check_gdrive_storage(),
            }
        }
```

### 9.7 Configuration Validator (NEW)

```python
# modules/infrastructure/config_validator.py
class ConfigValidator:
    """
    Runs on system startup and daily via cron.
    Validates all required configuration is present and functional.
    Prevents the system from operating in a silently broken state.
    """

    def validate_all(self) -> dict:
        """
        Returns {valid: bool, errors: [], warnings: []}
        Checks:
        1. All required .env variables present and non-empty
        2. Groq API key valid (test completion call)
        3. Pexels API key valid (test search)
        4. Ollama responsive (test generation)
        5. Database schema version matches expected
        6. Disk space > 10GB free
        7. Proxy-vm reachable from content-vm
        8. All Squid proxy ports responding
        9. Telegram bot token valid
        10. SMTP credentials valid (test connection)
        11. Cron jobs installed correctly
        12. SSL certificates not expiring within 7 days (if applicable)
        """
```

### 9.8 Database Connection Pool

```python
# database/connection_pool.py
import sqlite3
import fcntl
import os
import threading
import logging

logger = logging.getLogger(__name__)

class DatabasePool:
    """
    Thread-safe and process-safe SQLite connection manager.

    SQLite WAL mode allows concurrent readers but only one writer.
    Multiple cron jobs may try to write simultaneously.

    Strategy:
    - WAL mode enabled (concurrent reads)
    - busy_timeout = 30000ms (30s wait for write lock)
    - Process-level write lock using fcntl.flock on a lockfile
    - WAL checkpoint every 1000 pages (prevents WAL file bloat)
    """

    def __init__(self, db_path: str = None):
        self.db_path = db_path or os.getenv('DATABASE_PATH', '/app/data/autofarm.db')
        self.lock_path = self.db_path + '.writelock'
        self._local = threading.local()

    def get_connection(self) -> sqlite3.Connection:
        """Returns a connection with WAL mode and appropriate timeouts."""
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            conn = sqlite3.connect(self.db_path, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return self._local.conn

    def write_with_lock(self, sql: str, params: tuple = ()):
        """Executes a write operation with process-level file lock."""
        conn = self.get_connection()
        lock_fd = open(self.lock_path, 'w')
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            conn.execute(sql, params)
            conn.commit()
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            lock_fd.close()

    def checkpoint(self):
        """Run WAL checkpoint to prevent unbounded WAL growth."""
        conn = self.get_connection()
        conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
```

### 9.9 Structured Logging

```python
# modules/infrastructure/logging_config.py
import structlog
import logging
import sys

def configure_logging():
    """
    Configures structured JSON logging for all modules.
    Every log entry includes: timestamp, level, module, brand_id (if applicable),
    job_id (if applicable), duration_ms (for timed operations).
    """
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
    )
```

---

## PART 10 — ZERO-TOUCH BRAND EXPANSION SYSTEM

### 10.1 Add New Brand Command

```python
# scripts/add_brand.py
"""
Usage: python scripts/add_brand.py

Interactive flow:
1. Enter brand name + niche description
2. System calls Groq to generate full brand config
3. Shows generated config for human review/editing
4. On confirmation: creates DB records, directories, assets
5. Integrates brand into full pipeline
"""
```

### 10.2 Brand Config Auto-Generator

```python
# modules/ai_brain/brand_generator.py
class BrandConfigGenerator:
    """
    Uses Groq to generate a complete brand configuration from a brief description.
    Generates: positioning, pillars, visual identity, voice persona, hook priority,
    CTA examples, series formats, premium rules, subreddits, affiliate categories.
    """

    GENERATION_PROMPT = """
    You are a brand strategy expert and digital marketing specialist.
    Create a complete brand identity for a faceless social media channel in the
    Success Guru Network. [full schema definition]
    Respond with JSON only. No preamble.
    """
```

---

## PART 11 — EXPERT-LAYER ADDITIONS

### 11.1 IT Developer Expert Additions

#### Circuit Breaker Pattern

```python
# modules/infrastructure/circuit_breaker.py
class CircuitBreaker:
    """
    Prevents cascade failures when a platform API is down.
    States: CLOSED → OPEN (5 failures → 15min timeout) → HALF_OPEN (test)
    Jobs for open circuits are skipped and rescheduled, not failed.
    """
    FAILURE_THRESHOLD = 5
    TIMEOUT_SECONDS = 900
```

#### System Health Monitor

```python
# modules/infrastructure/health_monitor.py
class HealthMonitor:
    """
    Lightweight monitoring. Writes metrics to SQLite. Alerts via Telegram.
    Checks: disk, RAM, Ollama, SQLite, Google Drive storage, API quotas,
    review queue age, last publish per brand, content queue depth, circuit breakers.
    """

    def check_gdrive_storage(self) -> dict:
        """
        Checks Google Drive storage usage.
        Alerts at >12GB (80% of 15GB). Forces cleanup at critical threshold.
        """
        from modules.review_gate.gdrive_uploader import GDriveVideoUploader
        uploader = GDriveVideoUploader()
        used_gb = uploader.get_storage_usage_gb()
        status = 'ok'
        if used_gb > 12:
            status = 'critical'
            uploader.cleanup_expired_reviews()
        elif used_gb > 9:
            status = 'warning'
        return {'service': 'google_drive', 'used_gb': used_gb, 'limit_gb': 15, 'status': status}
```

#### Graceful Shutdown Handler

```python
# modules/infrastructure/shutdown_handler.py
class GracefulShutdownHandler:
    """Handles SIGTERM/SIGINT. No half-assembled videos, no mid-upload API calls."""
```

#### Automatic Database Backup

```python
# jobs/backup_database.py
"""
Daily at 2:30am UTC:
1. SQLite .backup() to timestamped file
2. Upload to OCI Object Storage /backups/ folder
3. Retain 14 days of backups
4. Alert if backup fails
"""
```

### 11.2 Marketing Expert Additions

#### A/B Hook Testing Framework

```python
# modules/marketing/ab_testing.py
class HookABTester:
    """
    Every 10th video: create TWO versions with different hooks.
    Publish A first, B 2 hours later. Compare 3-second hold rate after 48h.
    Update hook_performance weights with result.
    """
```

#### First Comment Automation

```python
# modules/marketing/first_comment.py
class FirstCommentPoster:
    """Posts a pinned first comment after publishing. Brand-appropriate templates."""
```

#### UTM Tracking, Trending Audio, Content Calendar

```python
# modules/marketing/utm_tracker.py — UTM params on affiliate links
# modules/marketing/trending_audio.py — TikTok trending sounds (low-volume overlay)
# modules/marketing/calendar_view.py — Visual HTML calendar at GET /calendar
```

### 11.3 Brand Expert Additions

#### Brand Safety Scorer

```python
# modules/brand/safety_scorer.py
class BrandSafetyScorer:
    """
    Evaluates scripts against brand guidelines BEFORE video assembly.
    Uses Ollama (local, free). Score 0-10. Rejects if < 7.
    Checks: voice consistency, forbidden words, tone, pillar alignment, CTA.
    """
```

#### Quality Gate

```python
# modules/brand/quality_gate.py
class QualityGate:
    """
    Last check before review queue.
    Thresholds: word_count 80-200, hook ≤15 words, sentences ≤13 words,
    brand_safety ≥7.0, duration 30-62s, thumbnail quality ≥0.6
    """
```

#### Voice Consistency Tracker & Milestone Tracker

```python
# modules/brand/voice_tracker.py — Tracks semantic drift using embeddings
# modules/brand/milestone_tracker.py — Follower milestones + strategy suggestions
```

### 11.4 Admin Dashboard

```python
# modules/dashboard/dashboard.py
"""
Lightweight web dashboard served by the approval server.
Pure Python + HTML/CSS/JS. No external dependencies.
http://{PROXY_VM_PUBLIC_IP}:8080/dashboard

Pages: /dashboard, /dashboard/brand/{id}, /dashboard/queue,
       /dashboard/schedule, /dashboard/analytics, /dashboard/health,
       /dashboard/compliance, /review/queue
"""
```

---

## PART 12 — FREE TIER LIMIT TRACKER (CORRECTED)

```python
# modules/compliance/free_tier_monitor.py
FREE_TIER_LIMITS = {
    "groq_api": {
        "model_limits": {
            "llama-3.3-70b-versatile": {
                "requests_per_minute": 30,
                "requests_per_day": 1000,
                "tokens_per_minute": 12000,
                "tokens_per_day": 100000,
            },
            "llama-3.1-8b-instant": {
                "requests_per_minute": 30,
                "requests_per_day": 14400,
                "tokens_per_minute": 6000,
                "tokens_per_day": 500000,
            }
        },
        "notes": "Free tier has 429 on exceed, no charges. Limits per org."
    },
    "pexels_api": {"requests_per_hour": 200, "requests_per_month": 20000},
    "pixabay_api": {"requests_per_hour": 100},
    "newsapi": {"requests_per_day": 100},
    "oci_object_storage": {"total_gb": 20, "alert_threshold_gb": 16},
    "oci_outbound_transfer": {"total_tb_per_month": 10, "alert_threshold_tb": 8},
    "gmail_smtp": {"emails_per_day": 500, "notes": "Gmail App Password"},
    "google_drive": {"total_gb": 15, "alert_threshold_gb": 12, "notes": "Optional — Telegram review is primary"},
    "youtube_data_api": {
        "quota_units_per_day_per_project": 10000,
        "notes": "1 project initially. Split if needed."
    },
    "reddit_api": {"requests_per_minute": 60},
    "telegram_bot_api": {
        "messages_per_second": 30,
        "messages_per_minute_per_chat": 20,
        "notes": "Generous limits — not a concern for review workflow"
    }
}

class FreeTierMonitor:
    """
    Tracks usage against free tier limits for all services.
    Alerts via Telegram if any service >80% of limit.
    Auto-throttles if approaching limit.
    """
```

---

## PART 13 — OCI SETUP SCRIPTS (EXPANDED)

### 13.1 `infrastructure/full_setup.sh`


```bash
#!/bin/bash
# AUTOFARM ZERO — Complete OCI Infrastructure Setup
# Run from local machine with OCI CLI configured
# Prerequisites: oci cli installed (oci setup config)
set -e
echo "🚀 AutoFarm Zero — OCI Infrastructure Setup"

# === STEP 1: Create Compartment ===
source infrastructure/create_compartment.sh

# === STEP 2: Create VMs ===
echo "Creating content-vm (3 OCPU, 20GB RAM)..."
CONTENT_VM_OCID=$(oci compute instance launch \
  --compartment-id $COMPARTMENT_OCID \
  --availability-domain $AD \
  --image-id $UBUNTU_2204_ARM_IMAGE_OCID \
  --shape VM.Standard.A1.Flex \
  --shape-config '{"ocpus":3,"memoryInGBs":20}' \
  --subnet-id $CONTENT_SUBNET_OCID \
  --assign-public-ip false \
  --display-name "autofarm-content-vm" \
  --ssh-authorized-keys-file ~/.ssh/id_rsa.pub \
  --query "data.id" --raw-output)

echo "Creating proxy-vm (1 OCPU, 4GB RAM)..."
PROXY_VM_OCID=$(oci compute instance launch \
  --compartment-id $COMPARTMENT_OCID \
  --availability-domain $AD \
  --image-id $UBUNTU_2204_ARM_IMAGE_OCID \
  --shape VM.Standard.A1.Flex \
  --shape-config '{"ocpus":1,"memoryInGBs":4}' \
  --subnet-id $PROXY_SUBNET_OCID \
  --assign-public-ip true \
  --display-name "autofarm-proxy-vm" \
  --ssh-authorized-keys-file ~/.ssh/id_rsa.pub \
  --query "data.id" --raw-output)

# === STEP 3: Create Object Storage Bucket (backups only) ===
oci os bucket create \
  --compartment-id $COMPARTMENT_OCID \
  --name "autofarm-backups" \
  --versioning Disabled

oci os object-lifecycle-policy put \
  --bucket-name "autofarm-backups" \
  --items '[{"action":"DELETE","is-enabled":true,"name":"auto-delete-old-backups","object-name-filter":{"inclusion-prefixes":["backup/"]},"time-amount":14,"time-unit":"DAYS"}]'

# === STEP 4: Secondary VNICs for IP separation ===
source infrastructure/setup_secondary_vnics.sh $PROXY_VM_OCID

# === STEP 5: Output connection info ===
CONTENT_PRIVATE_IP=$(oci compute instance list-vnics \
  --instance-id $CONTENT_VM_OCID \
  --query "data[0].\"private-ip\"" --raw-output)
PROXY_PUBLIC_IP=$(oci compute instance list-vnics \
  --instance-id $PROXY_VM_OCID \
  --query "data[0].\"public-ip\"" --raw-output)

echo ""
echo "✅ Infrastructure created successfully"
echo "Content VM private IP: $CONTENT_PRIVATE_IP"
echo "Proxy VM public IP:    $PROXY_PUBLIC_IP"
echo ""
echo "NEXT STEPS:"
echo "1. SSH to proxy-vm: ssh ubuntu@$PROXY_PUBLIC_IP"
echo "2. Run on proxy-vm: bash infrastructure/setup_proxy_vm.sh"
echo "3. From proxy-vm, SSH to content-vm and run: bash scripts/setup_content_vm.sh"
echo ""
echo "Save to .env.infrastructure:"
echo "CONTENT_VM_PRIVATE_IP=$CONTENT_PRIVATE_IP"
echo "PROXY_VM_PUBLIC_IP=$PROXY_PUBLIC_IP"
echo "COMPARTMENT_OCID=$COMPARTMENT_OCID"
```


### 13.2 `infrastructure/setup_proxy_vm.sh`

```bash
#!/bin/bash
# Run on proxy-vm after SSH access is confirmed
# Sets up 6 brand-specific Squid proxy instances + approval server
set -e
echo "🔀 Setting up AutoFarm proxy-vm..."

sudo apt update
sudo apt install -y squid net-tools python3.11 python3.11-venv git curl wget iproute2

# Install uv and Python dependencies (lightweight — proxy + approval only)
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.cargo/env
git clone https://github.com/your-repo/autofarm-success-guru.git /app
cd /app
uv venv .venv
source .venv/bin/activate
uv pip install -r pyproject_proxy.toml

# Create per-brand Squid config directories
for brand in human_success_guru wealth_success_guru zen_success_guru \
             social_success_guru habits_success_guru relationships_success_guru; do
    sudo mkdir -p /etc/squid/$brand
    sudo mkdir -p /var/log/squid/$brand
    sudo mkdir -p /var/spool/squid/$brand
    sudo mkdir -p /var/run/squid
done

# Configure secondary VNICs
# IMPORTANT: Replace {PRIVATE_IP_B}, {PRIVATE_IP_C}, {GATEWAY} with actual values
# from the OCI console after secondary VNICs are attached
sudo ip addr add {PRIVATE_IP_B}/24 dev eth1
sudo ip addr add {PRIVATE_IP_C}/24 dev eth2
sudo ip link set eth1 up
sudo ip link set eth2 up

# Policy routing to prevent asymmetric routing
echo "1 eth0rt" | sudo tee -a /etc/iproute2/rt_tables
echo "2 eth1rt" | sudo tee -a /etc/iproute2/rt_tables
echo "3 eth2rt" | sudo tee -a /etc/iproute2/rt_tables

sudo ip route add default via {GATEWAY} dev eth0 table eth0rt
sudo ip route add default via {GATEWAY} dev eth1 table eth1rt
sudo ip route add default via {GATEWAY} dev eth2 table eth2rt

sudo ip rule add from {PRIVATE_IP_A} table eth0rt
sudo ip rule add from {PRIVATE_IP_B} table eth1rt
sudo ip rule add from {PRIVATE_IP_C} table eth2rt

# Generate per-brand Squid configs from template
python3 /app/scripts/generate_squid_configs.py

# Install per-brand systemd services
for brand in human_success_guru wealth_success_guru zen_success_guru \
             social_success_guru habits_success_guru relationships_success_guru; do
    sudo systemctl enable squid-$brand
    sudo systemctl start squid-$brand
done

# Firewall: only allow approval server, SSH, and content-vm
sudo iptables -A INPUT -s {CONTENT_VM_PRIVATE_IP}/32 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 8080 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 22 -j ACCEPT
sudo iptables -A INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT
sudo iptables -A INPUT -j DROP
sudo apt install -y iptables-persistent
sudo netfilter-persistent save

# Set up supervisord for approval server
sudo apt install -y supervisor
sudo cp config/supervisord_proxy.conf /etc/supervisor/conf.d/autofarm-proxy.conf
sudo supervisorctl reread && sudo supervisorctl update

echo "✅ Proxy VM setup complete"
echo "Testing all 6 brand proxies..."
python3 /app/scripts/test_proxy_routing.py
echo "Approval server: http://$(curl -s ifconfig.me):8080"
```
### 13.3 `scripts/generate_squid_configs.py`

```python
"""
Generates /etc/squid/{brand_id}/squid.conf for each brand.
Called during proxy-vm setup. Reads config from .env.
IMPORTANT: tcp_outgoing_address uses PRIVATE IPs (OCI NATs to public).
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BRAND_CONFIG = {
    'human_success_guru':           {'port': os.getenv('PROXY_PORT_HUMAN_SUCCESS_GURU', '3128'),
                                     'interface_ip': os.getenv('PROXY_PRIVATE_IP_A')},
    'wealth_success_guru':          {'port': os.getenv('PROXY_PORT_WEALTH_SUCCESS_GURU', '3129'),
                                     'interface_ip': os.getenv('PROXY_PRIVATE_IP_A')},
    'zen_success_guru':             {'port': os.getenv('PROXY_PORT_ZEN_SUCCESS_GURU', '3130'),
                                     'interface_ip': os.getenv('PROXY_PRIVATE_IP_B')},
    'social_success_guru':          {'port': os.getenv('PROXY_PORT_SOCIAL_SUCCESS_GURU', '3131'),
                                     'interface_ip': os.getenv('PROXY_PRIVATE_IP_B')},
    'habits_success_guru':          {'port': os.getenv('PROXY_PORT_HABITS_SUCCESS_GURU', '3132'),
                                     'interface_ip': os.getenv('PROXY_PRIVATE_IP_C')},
    'relationships_success_guru':   {'port': os.getenv('PROXY_PORT_RELATIONSHIPS_SUCCESS_GURU', '3133'),
                                     'interface_ip': os.getenv('PROXY_PRIVATE_IP_C')},
}

SQUID_TEMPLATE = """
http_port {port} name={brand_id}

tcp_outgoing_address {interface_ip}

acl localnet src {content_vm_ip}/32
acl SSL_ports port 443
acl Safe_ports port 80
acl Safe_ports port 443
acl CONNECT method CONNECT

http_access allow localnet
http_access deny all

cache deny all

access_log /var/log/squid/{brand_id}/access.log combined
cache_log /var/log/squid/{brand_id}/cache.log
pid_filename /var/run/squid/{brand_id}.pid

coredump_dir /var/spool/squid/{brand_id}
"""

content_vm_ip = os.getenv('CONTENT_VM_PRIVATE_IP')

for brand_id, cfg in BRAND_CONFIG.items():
    config_path = Path(f'/etc/squid/{brand_id}/squid.conf')
    config_content = SQUID_TEMPLATE.format(
        port=cfg['port'],
        brand_id=brand_id,
        interface_ip=cfg['interface_ip'],
        content_vm_ip=content_vm_ip,
    ).strip()
    config_path.write_text(config_content)
    print(f"✅ Generated {config_path}")

print("All Squid configs generated.")
```

### 13.4 `scripts/test_proxy_routing.py`

```python
"""
Verifies all 6 brand proxies route through correct IPs.
Run during setup and included in test_pipeline.py as Test #28.
"""
from modules.network.ip_router import BrandIPRouter

router = BrandIPRouter()
results = router.verify_all_brands()

print("\n🌐 IP ROUTING VERIFICATION")
print("=" * 55)
all_passed = True
for r in results:
    if r['verified']:
        print(f"  ✅ {r['brand_id']:<35} → {r['actual_source_ip']}")
    else:
        print(f"  ❌ {r['brand_id']:<35} → FAILED: {r.get('error', 'unknown')}")
        all_passed = False

print("=" * 55)
if all_passed:
    print("All 6 brand proxies routing correctly.\n")
else:
    print("⚠️  Some proxies failed. Check Squid service status on proxy-vm.\n")
    exit(1)
```


### 13.5 `scripts/setup_content_vm.sh` (UPDATED)

```bash
#!/bin/bash
# Run on content-vm after SSH access via proxy-vm
set -e
echo "📦 Setting up AutoFarm content-vm (V6.0)..."

sudo apt update && sudo apt upgrade -y
sudo apt install -y ffmpeg python3.11 python3.11-venv git imagemagick \
  curl wget htop tmux build-essential pkg-config libssl-dev \
  sqlite3 espeak-ng

# === SWAP FILE (CRITICAL for 20GB RAM VM) ===
echo "Setting up 8GB swap file..."
sudo fallocate -l 8G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
# Set swappiness low — swap is OOM protection, not regular use
echo 'vm.swappiness=10' | sudo tee -a /etc/sysctl.conf
sudo sysctl -p

# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.cargo/env

# Clone repository
git clone https://github.com/your-repo/autofarm-success-guru.git /app
cd /app

# Create virtualenv and install dependencies
uv venv .venv
source .venv/bin/activate
uv pip install -r pyproject.toml

# Install Ollama (ARM build)
curl -fsSL https://ollama.com/install.sh | sh
systemctl enable ollama && systemctl start ollama
sleep 10
ollama pull llama3.1:8b

# Configure Ollama for low-memory operation
mkdir -p /etc/systemd/system/ollama.service.d
cat > /etc/systemd/system/ollama.service.d/override.conf << 'EOF'
[Service]
Environment="OLLAMA_NUM_PARALLEL=1"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
Environment="OLLAMA_KEEP_ALIVE=10m"
EOF
systemctl daemon-reload && systemctl restart ollama

# Install Kokoro TTS (requires espeak-ng already installed above)
pip install kokoro soundfile --break-system-packages
python scripts/install_kokoro.py

# NOTE: Whisper is NOT installed. System generates its own audio.
# No transcription needed.

# Download brand fonts
python scripts/download_fonts.py

# Initialise database
python scripts/init_db.py

# Create all directories
python scripts/create_directories.py

# Set up supervisord
sudo apt install -y supervisor
sudo cp config/supervisord_content.conf /etc/supervisor/conf.d/autofarm.conf
sudo supervisorctl reread && sudo supervisorctl update

# Install cron jobs
python scripts/install_cron.py --vm content

# Generate encryption key
python scripts/generate_encryption_key.py

# Validate configuration
python scripts/validate_config.py

echo "✅ Content VM setup complete (V6.0)"
echo "Swap: 8GB configured"
echo "Ollama: llama3.1:8b (primary LLM)"
echo "Kokoro TTS: installed"
echo "Whisper: NOT installed (not needed)"
echo ""
echo "Next: Edit .env with API keys, then: python scripts/add_account.py"
```

### 13.6 Security Hardening

```bash
# infrastructure/security_hardening.sh — Run on both VMs
sudo sed -i 's/PermitRootLogin yes/PermitRootLogin no/' /etc/ssh/sshd_config
sudo sed -i 's/#PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config
sudo systemctl restart sshd
sudo apt install -y unattended-upgrades fail2ban
sudo dpkg-reconfigure --priority=low unattended-upgrades
sudo systemctl enable fail2ban
sudo cp config/logrotate.conf /etc/logrotate.d/autofarm
```

---

## PART 14 — DIRECTORY STRUCTURE (UPDATED)

```
autofarm/
├── pyproject.toml
├── pyproject_proxy.toml
├── .env.example
├── .env
├── .env.infrastructure
├── .gitignore
├── README.md
├── Makefile
│
├── infrastructure/
│   ├── full_setup.sh
│   ├── create_compartment.sh
│   ├── setup_secondary_vnics.sh
│   ├── setup_proxy_vm.sh
│   ├── security_hardening.sh
│   └── network_diagram.md
│
├── config/
│   ├── brands.json
│   ├── platforms.json
│   ├── settings.py
│   ├── youtube_projects.json          # Simplified: 1 project initially
│   ├── cached_responses/              # NEW: Emergency LLM fallback templates
│   │   ├── script_generation.json
│   │   ├── caption_variation.json
│   │   └── hashtag_generation.json
│   ├── supervisord_content.conf
│   ├── supervisord_proxy.conf
│   └── logrotate.conf
│
├── database/
│   ├── schema.sql
│   ├── db.py
│   ├── connection_pool.py             # NEW: Process-safe SQLite access
│   └── credential_manager.py
│
├── account_manager/
│   ├── manager.py
│   ├── account_setup.py
│   └── token_refresher.py
│
├── modules/
│   ├── network/
│   │   ├── ip_router.py
│   │   └── ua_generator.py            # NEW: Dynamic user agent generation
│   │
│   ├── ai_brain/
│   │   ├── llm_router.py              # NEW: Ollama/Groq/Cached routing
│   │   ├── hook_engine.py
│   │   ├── brand_generator.py
│   │   ├── script_writer.py
│   │   ├── classifier.py
│   │   ├── duplicate_checker.py
│   │   ├── hashtag_generator.py
│   │   └── brain.py
│   │
│   ├── compliance/
│   │   ├── rate_limits.py
│   │   ├── rate_limit_manager.py
│   │   ├── platform_compliance.py
│   │   ├── anti_spam.py
│   │   ├── cross_brand_dedup.py       # NEW: Cross-brand similarity check
│   │   └── free_tier_monitor.py
│   │
│   ├── queue/
│   │   └── content_queue.py
│   │
│   ├── storage/
│   │   └── oci_storage.py
│   │
│   ├── infrastructure/
│   │   ├── circuit_breaker.py
│   │   ├── health_monitor.py
│   │   ├── shutdown_handler.py
│   │   ├── idle_guard.py              # NEW: OCI idle reclamation prevention
│   │   ├── resource_scheduler.py      # NEW: RAM/CPU-aware job scheduling
│   │   ├── retry_handler.py           # NEW: Exponential backoff with jitter
│   │   ├── job_state_machine.py       # NEW: Content job lifecycle tracking
│   │   ├── config_validator.py        # NEW: Startup validation
│   │   └── logging_config.py          # NEW: Structured JSON logging
│   │
│   ├── marketing/
│   │   ├── ab_testing.py
│   │   ├── first_comment.py
│   │   ├── utm_tracker.py
│   │   ├── trending_audio.py
│   │   └── calendar_view.py
│   │
│   ├── brand/
│   │   ├── safety_scorer.py
│   │   ├── quality_gate.py
│   │   ├── voice_tracker.py
│   │   └── milestone_tracker.py
│   │
│   ├── dashboard/
│   │   └── dashboard.py
│   │
│   ├── trend_scanner/
│   ├── content_forge/
│   │   └── background_library.py
│   ├── review_gate/
│   │   ├── approval_tracker.py
│   │   ├── gate.py
│   │   ├── email_sender.py
│   │   ├── telegram_reviewer.py       # NEW: Primary review channel
│   │   ├── approval_server.py
│   │   └── gdrive_uploader.py         # Now optional (fallback only)
│   ├── publish_engine/
│   │   └── schedule_config.py
│   ├── feedback_loop/
│   └── notifications/
│
├── scripts/
│   ├── setup_content_vm.sh
│   ├── setup_gdrive_auth.py           # Now optional
│   ├── generate_squid_configs.py
│   ├── test_proxy_routing.py
│   ├── add_brand.py
│   ├── validate_config.py             # NEW: Startup configuration check
│   ├── download_fonts.py
│   ├── predownload_backgrounds.py
│   ├── create_directories.py
│   ├── generate_encryption_key.py
│   ├── install_cron.py
│   ├── add_account.py
│   ├── list_accounts.py
│   ├── toggle_publish_mode.py
│   ├── approve_content.py
│   └── test_pipeline.py
│
├── jobs/
│   ├── scan_and_generate.py
│   ├── process_review_queue.py
│   ├── check_auto_approvals.py
│   ├── publish_due.py
│   ├── refresh_tokens.py
│   ├── pull_analytics.py
│   ├── maintain_backgrounds.py
│   ├── reoptimise_schedule.py
│   ├── reset_daily_counts.py
│   ├── reset_api_quotas.py
│   ├── send_daily_digest.py
│   ├── check_storage.py
│   ├── check_queue_depth.py
│   ├── cleanup_gdrive.py
│   ├── cleanup_orphans.py             # NEW: Partial file cleanup
│   ├── validate_config.py             # NEW: Daily config check
│   ├── refresh_user_agents.py         # NEW: Monthly UA update
│   └── backup_database.py
│
└── tests/
    ├── test_compliance.py
    ├── test_scheduler.py
    ├── test_ip_routing.py
    ├── test_llm_router.py             # NEW
    ├── test_cross_brand_dedup.py      # NEW
    ├── test_resource_scheduler.py     # NEW
    ├── test_job_state_machine.py      # NEW
    ├── test_brand_config.py
    ├── test_hook_engine.py
    ├── test_review_gate.py
    ├── test_telegram_review.py        # NEW
    ├── test_script_writer.py
    ├── test_video_assembler.py
    └── test_publish_engine.py
```

---

## PART 15 — DATABASE SCHEMA (ADDITIONS)

Add all of the following to `database/schema.sql`:

```sql
-- Google Drive review file tracking (replaces OCI storage for reviews)
CREATE TABLE IF NOT EXISTS gdrive_review_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id TEXT NOT NULL UNIQUE,
    review_token TEXT NOT NULL,
    brand_id TEXT NOT NULL,
    file_type TEXT NOT NULL,              -- 'video' or 'thumbnail'
    preview_url TEXT,
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted INTEGER DEFAULT 0
);

-- System metrics (lightweight monitoring)
CREATE TABLE IF NOT EXISTS system_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_name TEXT NOT NULL,
    metric_value REAL NOT NULL,
    label TEXT,
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- A/B test tracking
CREATE TABLE IF NOT EXISTS ab_tests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    variant_a_script_id INTEGER REFERENCES scripts(id),
    variant_b_script_id INTEGER REFERENCES scripts(id),
    variant_a_job_id INTEGER REFERENCES publish_jobs(id),
    variant_b_job_id INTEGER REFERENCES publish_jobs(id),
    hook_type_a TEXT,
    hook_type_b TEXT,
    winner TEXT,
    result_metric TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    resolved_at TIMESTAMP
);

-- Brand safety evaluations
CREATE TABLE IF NOT EXISTS brand_safety_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    script_id INTEGER REFERENCES scripts(id),
    brand_id TEXT NOT NULL,
    safety_score REAL NOT NULL,
    passed INTEGER NOT NULL,
    issues TEXT,
    evaluated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Content queue
CREATE TABLE IF NOT EXISTS content_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id INTEGER REFERENCES videos(id),
    brand_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    status TEXT DEFAULT 'waiting',
    scheduled_for TIMESTAMP,
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(video_id, platform)
);

-- Milestones
CREATE TABLE IF NOT EXISTS milestones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER REFERENCES accounts(id),
    milestone_type TEXT NOT NULL,
    reached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    notified INTEGER DEFAULT 0
);

-- Circuit breaker state
CREATE TABLE IF NOT EXISTS circuit_breakers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    state TEXT DEFAULT 'CLOSED',
    failure_count INTEGER DEFAULT 0,
    last_failure_at TIMESTAMP,
    opens_until TIMESTAMP,
    UNIQUE(brand_id, platform)
);

-- Background library
CREATE TABLE IF NOT EXISTS background_library (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand_id TEXT NOT NULL,
    file_path TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    source_id TEXT,
    quality_score REAL DEFAULT 0.5,
    times_used INTEGER DEFAULT 0,
    last_used_at TIMESTAMP,
    downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    active INTEGER DEFAULT 1
);

-- OCI storage (backups only)
CREATE TABLE IF NOT EXISTS oci_backup_objects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    object_name TEXT NOT NULL UNIQUE,
    object_type TEXT DEFAULT 'backup',
    size_bytes INTEGER,
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted INTEGER DEFAULT 0
);
```

```sql
-- Job state tracking (NEW)
CREATE TABLE IF NOT EXISTS job_states (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    job_type TEXT NOT NULL,
    brand_id TEXT NOT NULL,
    state TEXT NOT NULL,
    previous_state TEXT,
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_job_states_state ON job_states(state);
CREATE INDEX idx_job_states_brand ON job_states(brand_id);

-- LLM routing log (NEW)
CREATE TABLE IF NOT EXISTS llm_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,  -- 'ollama', 'groq', 'cached'
    task_type TEXT NOT NULL,
    brand_id TEXT,
    tokens_used INTEGER,
    latency_ms INTEGER,
    success INTEGER DEFAULT 1,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_llm_requests_provider ON llm_requests(provider, created_at);

-- Cross-brand dedup log (NEW)
CREATE TABLE IF NOT EXISTS dedup_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    script_id INTEGER,
    brand_id TEXT NOT NULL,
    most_similar_brand TEXT,
    similarity_score REAL,
    passed INTEGER NOT NULL,
    checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Resource usage snapshots (NEW)
CREATE TABLE IF NOT EXISTS resource_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cpu_percent REAL,
    ram_used_gb REAL,
    ram_total_gb REAL,
    swap_used_gb REAL,
    disk_used_percent REAL,
    ollama_loaded INTEGER DEFAULT 0,
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

Total: **26 tables** (22 from V5.1 + 4 new).

---

## PART 16 — COMPLETE .env TEMPLATE (UPDATED)

```bash
# === OCI INFRASTRUCTURE ===
OCI_REGION=uk-london-1
COMPARTMENT_OCID=
VCN_OCID=
CONTENT_VM_PRIVATE_IP=10.0.1.x

# === PROXY VM NETWORK ===
PROXY_VM_INTERNAL_IP=10.0.2.x
PROXY_VM_PUBLIC_IP=x.x.x.x
PROXY_PRIVATE_IP_A=10.0.2.a
PROXY_PRIVATE_IP_B=10.0.2.b
PROXY_PRIVATE_IP_C=10.0.2.c

PUBLIC_IP_GROUP_A=x.x.x.x
PUBLIC_IP_GROUP_B=x.x.x.x
PUBLIC_IP_GROUP_C=x.x.x.x

PROXY_PORT_HUMAN_SUCCESS_GURU=3128
PROXY_PORT_WEALTH_SUCCESS_GURU=3129
PROXY_PORT_ZEN_SUCCESS_GURU=3130
PROXY_PORT_SOCIAL_SUCCESS_GURU=3131
PROXY_PORT_HABITS_SUCCESS_GURU=3132
PROXY_PORT_RELATIONSHIPS_SUCCESS_GURU=3133

# === SMTP (Gmail) ===
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USE_TLS=true
SMTP_USER=motivated.success.win@gmail.com
SMTP_PASSWORD=your_16_character_app_password
SMTP_FROM_NAME=Success Guru Network

# === TELEGRAM (PRIMARY review + notifications) ===
TELEGRAM_BOT_TOKEN=
TELEGRAM_REVIEW_CHAT_ID=          # Chat ID for review approvals
TELEGRAM_ALERTS_CHAT_ID=          # Chat ID for system alerts (can be same)

# === GOOGLE DRIVE (OPTIONAL — fallback for full-quality review) ===
GDRIVE_ENABLED=false              # Set true only if email review needed
GDRIVE_CREDENTIALS_PATH=config/gdrive_credentials.json
GDRIVE_TOKEN_PATH=config/gdrive_token.json
GDRIVE_REVIEW_FOLDER=AutoFarm Reviews
GDRIVE_FILE_EXPIRY_DAYS=14
GDRIVE_ALERT_THRESHOLD_GB=12

# === API KEYS ===
GROQ_API_KEY=                     # Fallback LLM only — Ollama is primary
PEXELS_API_KEY=
PIXABAY_API_KEY=
NEWSAPI_KEY=

# === ENCRYPTION ===
FERNET_KEY=

# === DATABASE ===
DATABASE_PATH=/app/data/autofarm.db

# === SYSTEM ===
PUBLISH_MODE=review               # 'review' or 'auto'
AUTO_APPROVE_HOURS=0
QUEUE_TARGET_DAYS=3
MAX_CONCURRENT_VIDEO_ASSEMBLY=1   # Never exceed 1 on 20GB RAM
OLLAMA_MODEL=llama3.1:8b
```

---

## PART 17 — COMPLETE TEST SUITE (EXPANDED)

### `scripts/test_pipeline.py`

```python
"""
Full end-to-end system test. Run before going live.

Tests (in order):
01. Configuration validation (all .env vars, API keys)
02. Brand config load and validation (all 6 brands)
03. Database connectivity, schema integrity, WAL mode active
04. Database connection pool (concurrent read/write test)
05. Credential encryption round-trip
06. Ollama responsiveness (test prompt, measure latency)
07. LLM Router: Ollama → Groq failover test
08. LLM Router: Groq rate limit simulation → Cached fallback
09. Groq API connectivity (test with small prompt, check token tracking)
10. Brand safety scorer (test known-good and known-bad scripts)
11. Cross-brand deduplication (test similar/dissimilar scripts)
12. Kokoro TTS (generate voiceover for all 6 brand voices — confirm distinct)
13. Pexels API connectivity and brand-matched background fetch
14. FFmpeg background treatment for all 6 brands
15. Resource scheduler (simulate low RAM, confirm job blocking)
16. Full video assembly for Human Success Guru
17. Full video assembly for Habits Success Guru (different visual identity)
18. Thumbnail generation for all 6 brands
19. Quality gate check (pass and fail scenarios)
20. Job state machine (test valid/invalid transitions)
21. Telegram review send (send test review, confirm message delivered)
22. Approval server: approve via HTTP, confirm publish job in DB
23. Approval server: reject via HTTP, confirm video status updated
24. Healthcheck endpoint (GET /health returns valid JSON)
25. IP routing verification (each brand routes through correct IP)
26. Rate limit manager (simulate limit breach, confirm rejection)
27. Platform compliance checker (test known-violation scenario)
28. Anti-spam variator (confirm two videos have different hashes)
29. Scheduler (generate schedule for next 7 days, confirm time variation)
30. Circuit breaker (simulate failure cascade, confirm opens)
31. Retry handler (simulate transient failure, confirm retry succeeds)
32. Health monitor full check
33. Free tier monitor (all services tracked)
34. Idle guard (verify daemon responds to health check)
35. `add_brand.py` dry-run (generate config without saving)

Print: PASS ✅ / FAIL ❌ / SKIP ⏭ for each test with timing.
Final: {N}/35 tests passed in {T}s
"""
```

---

## PART 18 — DEPENDENCIES (UPDATED)

### `pyproject.toml` additions (content-vm)

```toml
[project]
dependencies = [
    # Core
    "requests>=2.31",
    "python-dotenv>=1.0",
    "cryptography>=42.0",
    "schedule>=1.2",
    "psutil>=5.9",

    # Database
    "sqlite-utils>=3.36",

    # AI/ML
    "kokoro>=0.9.4",
    "soundfile>=0.12",
    "scikit-learn>=1.4",       # For cross-brand dedup TF-IDF

    # Video
    # ffmpeg installed via apt, not pip

    # Telegram (primary review)
    "python-telegram-bot>=20.7",

    # Google Drive (optional fallback)
    "google-auth>=2.27",
    "google-auth-oauthlib>=1.2",
    "google-auth-httplib2>=0.2",
    "google-api-python-client>=2.120",

    # Logging
    "structlog>=24.1",

    # Image processing
    "Pillow>=10.2",

    # OCI SDK
    "oci>=2.120",
]
```

### `pyproject_proxy.toml` (proxy-vm)

```toml
[project]
dependencies = [
    "flask>=3.0",
    "requests>=2.31",
    "python-dotenv>=1.0",
    "cryptography>=42.0",
    "python-telegram-bot>=20.7",  # Telegram bot runs on proxy-vm
    "structlog>=24.1",
]
```

---

## PART 19 — ACCOUNTS & PLATFORMS

### TikTok & Snapchat
All 12 accounts (6 TikTok + 6 Snapchat) not yet created. Pre-populated in accounts table with `status = 'pending_setup'`. Publishing silently skips them. When accounts are created externally, run `python scripts/add_account.py` to register credentials.

### GCP Projects for YouTube Quota
One separate GCP project per brand:
```
humansuccessguru-yt         → Human Success Guru YouTube
wealthsuccessguru-yt        → Wealth Success Guru YouTube
zensuccessguru-yt           → Zen Success Guru YouTube
socialsuccessguru-yt        → Social Success Guru YouTube
habitssuccessguru-yt        → Habits Success Guru YouTube
relationshipssuccessguru-yt → Relationships Success Guru YouTube
```
Each needs: YouTube Data API v3 enabled + OAuth 2.0 credentials (TV & Limited Input type).

---


## PART 20 — NON-NEGOTIABLE IMPLEMENTATION RULES (EXPANDED)

**COMPLIANCE:**
1. Every platform API call MUST pass through `RateLimitManager.check_and_increment()`. No exceptions.
2. Every video upload MUST call `PlatformComplianceChecker.check_all()` before upload. Failed compliance = reschedule, not abandon.
3. `AntiSpamVariator.vary_video_for_platform()` called for every video before every platform upload.
4. AI disclosure flags set on every upload where required (TikTok, YouTube at minimum).
5. `CrossBrandDeduplicator.check_script_uniqueness()` runs on every new script. Rejected if >0.7 similarity.

**SCHEDULING:**
6. Posting times NEVER identical two consecutive days for same brand × platform.
7. Minimum post gap enforced from `PLATFORM_LIMITS[platform]['min_post_gap_minutes']`.

**IP ROUTING:**
8. Every outbound API call in publishing MUST use `BrandIPRouter.get_session(brand_id)`. Raw `requests.get()` forbidden in publishing modules.

**QUALITY:**
9. `QualityGate.check()` runs before `ReviewGate.process()`. Failed quality = auto-reject.
10. `BrandSafetyScorer.score_script()` runs before video assembly. Off-brand scripts regenerated.

**LLM ROUTING:**
11. All LLM calls go through `LLMRouter.generate()`. Direct Ollama/Groq calls forbidden outside the router.
12. Groq free tier limits tracked in real-time. Never exceed 80% of daily limits.

**RESOURCE MANAGEMENT:**
13. `ResourceScheduler.can_start_job()` checked before every video assembly and TTS generation.
14. Maximum 1 concurrent video assembly job. Enforced by resource scheduler.
15. Swap usage monitored. If swap > 2GB, pause content generation until RAM frees.

**REVIEW:**
16. Telegram review is primary. Email + Google Drive is fallback only.
17. All state transitions go through `JobStateMachine.transition()`.

**INFRASTRUCTURE:**
18. `IdleGuard` daemon MUST be running at all times on content-vm.
19. `ConfigValidator.validate_all()` runs on every system start.
20. All API calls use `retry_with_backoff` decorator for transient failures.

---

## PART 21 — FINAL BUILD ORDER

Build every file in this order. Do not pause. Do not ask for confirmation.

1. `pyproject.toml` + `pyproject_proxy.toml` + `.env.example` + `.gitignore`
2. `infrastructure/` (all setup scripts — do NOT run them during build)
3. `config/brands.json` + `config/platforms.json` + `config/settings.py` + `config/youtube_projects.json` + `config/cached_responses/`
4. `database/schema.sql` + `database/db.py` + `database/connection_pool.py` + `database/credential_manager.py`
5. `modules/infrastructure/logging_config.py` + `modules/infrastructure/retry_handler.py` + `modules/infrastructure/job_state_machine.py`
6. `modules/network/ip_router.py` + `modules/network/ua_generator.py`
7. `modules/ai_brain/llm_router.py`
8. `modules/compliance/` (rate_limits → rate_limit_manager → platform_compliance → anti_spam → cross_brand_dedup → free_tier_monitor)
9. `modules/storage/oci_storage.py`
10. `modules/infrastructure/` (circuit_breaker → health_monitor → shutdown_handler → idle_guard → resource_scheduler → config_validator)
11. `account_manager/` (manager → account_setup → token_refresher)
12. `modules/trend_scanner/` (all scanners)
13. `modules/ai_brain/` (hook_engine → brand_generator → script_writer → classifier → duplicate_checker → hashtag_generator → brain)
14. `modules/brand/` (safety_scorer → quality_gate → voice_tracker → milestone_tracker)
15. `modules/content_forge/` (background_library → tts_engine → broll_fetcher → music_fetcher → caption_generator → video_assembler → thumbnail_maker → forge)
16. `modules/queue/content_queue.py`
17. `modules/review_gate/` (approval_tracker → gate → telegram_reviewer → email_sender → approval_server → gdrive_uploader)
18. `modules/publish_engine/` (schedule_config → base → all 5 platforms → formatters → caption_writer → scheduler → publisher)
19. `modules/feedback_loop/` (scorer → analytics_puller → hook_optimizer → model_updater)
20. `modules/marketing/` (ab_testing → first_comment → utm_tracker → trending_audio → calendar_view)
21. `modules/dashboard/dashboard.py`
22. `modules/notifications/` (email_notifier → telegram_bot)
23. `jobs/` (all 19 jobs)
24. `scripts/` (all scripts including validate_config.py)
25. `tests/` (all test files)
26. `config/supervisord_content.conf` + `config/supervisord_proxy.conf` + `config/logrotate.conf`
27. `Makefile` + `README.md`

---

## PART 22 — FINAL STATUS OUTPUT

```
✅ AutoFarm Zero — Success Guru Network v6.0
=============================================
Infrastructure:
  content-vm:           3 OCPU, 20GB RAM + 8GB swap (content generation)
  proxy-vm:             1 OCPU, 4GB RAM (6 Squid proxies + approval + Telegram bot)
  OCI region:           uk-london-1
  IP groups:            A (human+wealth) · B (zen+social) · C (habits+relationships)

IMPROVEMENTS OVER V5.1:
  ✅ OCI Object Storage corrected to 20GB (was 10GB)
  ✅ OCI idle instance guard daemon active
  ✅ Groq limits corrected (1K RPD for 70B model)
  ✅ LLM Router: Ollama primary → Groq fallback → Cached emergency
  ✅ Whisper removed (unnecessary — saves 1GB RAM)
  ✅ 8GB swap configured (OOM protection)
  ✅ YouTube: 1 GCP project (was 6 — simpler, safer)
  ✅ Telegram review bot (primary — 5s approval vs email)
  ✅ SQLite connection pool with process-level write lock
  ✅ Exponential backoff with jitter on all API calls
  ✅ Cross-brand content deduplication
  ✅ Job state machine with retry from failure point
  ✅ Resource-aware job scheduling (RAM/CPU checks)
  ✅ Structured JSON logging
  ✅ Configuration validator on startup
  ✅ Dynamic user agent generation (monthly refresh)

6 brand configs:        ✅ loaded and validated
Database (26 tables):   ✅ initialised (26 = 22 from V5.1 + 4 new)
Encryption:             ✅ Fernet active

AI Stack:
  Ollama:               ✅ llama3.1:8b (PRIMARY — unlimited, local)
  Groq:                 ⚠  Fallback only — add API key to .env
  Kokoro TTS:           ✅ 6 brand voices
  Whisper:              ❌ Removed (not needed)

Brand fonts:            ✅ 6 downloaded
Background library:     ✅ Pexels + Pixabay APIs configured

IP Routing:
  Squid proxies:        ✅ 6 instances configured
  Proxy verification:   ⏳ run scripts/test_proxy_routing.py

Review System:
  Telegram bot:         ⚠  Add TELEGRAM_BOT_TOKEN + TELEGRAM_REVIEW_CHAT_ID
  Email (fallback):     ⚠  Add Gmail App Password
  Google Drive:         ⏳ Optional — run setup_gdrive_auth.py if needed

Accounts:               ✅ 30 slots (12 active, 18 pending setup)
YouTube GCP project:    ⚠  Create 1 project (not 6)
Compliance engine:      ✅ Rate limits for all platforms + cross-brand dedup
Free tier monitor:      ✅ Watching 11 services (corrected limits)
Publish mode:           ✅ REVIEW — Telegram approval required
Resource scheduler:     ✅ Max 1 concurrent video assembly
Idle guard:             ✅ Daemon configured

TEST RESULTS:           {N}/35 passed

SETUP ORDER:
  1. OCI: infrastructure/full_setup.sh (from local machine with OCI CLI)
  2. proxy-vm: ssh → infrastructure/setup_proxy_vm.sh
  3. content-vm: ssh via proxy-vm → scripts/setup_content_vm.sh
  4. Telegram: Create bot via @BotFather, add token to .env
  5. GCP: Create 1 YouTube project, download OAuth credentials
  6. API Keys: Edit .env (Groq, Pexels, Pixabay, Gmail App Password)
  7. Accounts: python scripts/add_account.py (Facebook + Instagram + YouTube first)
  8. Backgrounds: python scripts/predownload_backgrounds.py
  9. Validate: python scripts/validate_config.py
  10. Test: python scripts/test_pipeline.py
  11. Launch: make run
  12. Autopilot: python scripts/toggle_publish_mode.py
```

---

## START COMMAND

```bash
mkdir -p autofarm-success-guru-v6 && cd autofarm-success-guru-v6
```

Build every file in the order in Part 21. Do not pause. Do not ask for confirmation. Build the complete codebase, then run the test suite reporting pass/fail for all 35 tests, then print the final status table above.
