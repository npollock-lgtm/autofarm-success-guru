-- AutoFarm Zero — Success Guru Network v6.0
-- Complete Database Schema (26 tables)
-- SQLite with WAL mode

-- Enable WAL mode and performance pragmas
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA cache_size=-64000;
PRAGMA busy_timeout=30000;
PRAGMA foreign_keys=ON;

-- ============================================================
-- CORE TABLES (from V5.1)
-- ============================================================

-- Table 1: Brands
CREATE TABLE IF NOT EXISTS brands (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    niche TEXT NOT NULL,
    config_json TEXT NOT NULL,
    active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Table 2: Accounts (per brand per platform)
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand_id TEXT NOT NULL REFERENCES brands(id),
    platform TEXT NOT NULL,
    username TEXT,
    account_id TEXT,
    status TEXT DEFAULT 'pending_setup',
    credentials_encrypted TEXT,
    token_expires_at TIMESTAMP,
    last_token_refresh TIMESTAMP,
    follower_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(brand_id, platform)
);
CREATE INDEX IF NOT EXISTS idx_accounts_brand ON accounts(brand_id);
CREATE INDEX IF NOT EXISTS idx_accounts_platform ON accounts(platform);

-- Table 3: Trends
CREATE TABLE IF NOT EXISTS trends (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand_id TEXT NOT NULL REFERENCES brands(id),
    source TEXT NOT NULL,
    topic TEXT NOT NULL,
    raw_data TEXT,
    relevance_score REAL DEFAULT 0.0,
    used INTEGER DEFAULT 0,
    discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_trends_brand ON trends(brand_id);
CREATE INDEX IF NOT EXISTS idx_trends_used ON trends(used);

-- Table 4: Scripts
CREATE TABLE IF NOT EXISTS scripts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand_id TEXT NOT NULL REFERENCES brands(id),
    trend_id INTEGER REFERENCES trends(id),
    hook TEXT NOT NULL,
    hook_type TEXT,
    body TEXT NOT NULL,
    cta TEXT,
    script_text TEXT NOT NULL,
    word_count INTEGER,
    pillar TEXT,
    series_name TEXT,
    series_number INTEGER,
    llm_provider TEXT,
    llm_tokens_used INTEGER,
    safety_score REAL,
    safety_passed INTEGER,
    dedup_score REAL,
    dedup_passed INTEGER,
    status TEXT DEFAULT 'draft',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_scripts_brand ON scripts(brand_id);
CREATE INDEX IF NOT EXISTS idx_scripts_status ON scripts(status);

-- Table 5: Videos
CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    script_id INTEGER NOT NULL REFERENCES scripts(id),
    brand_id TEXT NOT NULL REFERENCES brands(id),
    video_path TEXT,
    thumbnail_path TEXT,
    audio_path TEXT,
    background_path TEXT,
    duration_seconds REAL,
    resolution TEXT DEFAULT '1080x1920',
    file_size_bytes INTEGER,
    quality_score REAL,
    quality_passed INTEGER,
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_videos_brand ON videos(brand_id);
CREATE INDEX IF NOT EXISTS idx_videos_status ON videos(status);

-- Table 6: Reviews
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id INTEGER NOT NULL REFERENCES videos(id),
    brand_id TEXT NOT NULL REFERENCES brands(id),
    review_token TEXT NOT NULL UNIQUE,
    review_method TEXT DEFAULT 'telegram',
    telegram_message_id TEXT,
    email_sent INTEGER DEFAULT 0,
    gdrive_video_id TEXT,
    gdrive_thumbnail_id TEXT,
    status TEXT DEFAULT 'pending',
    reviewer_notes TEXT,
    auto_approve_at TIMESTAMP,
    reviewed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_reviews_token ON reviews(review_token);
CREATE INDEX IF NOT EXISTS idx_reviews_status ON reviews(status);

-- Table 7: Publish Jobs
CREATE TABLE IF NOT EXISTS publish_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id INTEGER NOT NULL REFERENCES videos(id),
    brand_id TEXT NOT NULL REFERENCES brands(id),
    platform TEXT NOT NULL,
    account_id INTEGER REFERENCES accounts(id),
    caption TEXT,
    hashtags TEXT,
    title TEXT,
    description TEXT,
    scheduled_for TIMESTAMP,
    published_at TIMESTAMP,
    platform_post_id TEXT,
    platform_url TEXT,
    ai_disclosure_applied INTEGER DEFAULT 0,
    anti_spam_varied INTEGER DEFAULT 0,
    varied_video_path TEXT,
    status TEXT DEFAULT 'pending',
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_publish_jobs_brand ON publish_jobs(brand_id);
CREATE INDEX IF NOT EXISTS idx_publish_jobs_platform ON publish_jobs(platform);
CREATE INDEX IF NOT EXISTS idx_publish_jobs_status ON publish_jobs(status);
CREATE INDEX IF NOT EXISTS idx_publish_jobs_scheduled ON publish_jobs(scheduled_for);

-- Table 8: Analytics
CREATE TABLE IF NOT EXISTS analytics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    publish_job_id INTEGER REFERENCES publish_jobs(id),
    brand_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    platform_post_id TEXT,
    views INTEGER DEFAULT 0,
    likes INTEGER DEFAULT 0,
    comments INTEGER DEFAULT 0,
    shares INTEGER DEFAULT 0,
    saves INTEGER DEFAULT 0,
    watch_time_seconds REAL DEFAULT 0,
    avg_view_duration_seconds REAL DEFAULT 0,
    retention_rate REAL DEFAULT 0,
    three_second_hold_rate REAL DEFAULT 0,
    impressions INTEGER DEFAULT 0,
    reach INTEGER DEFAULT 0,
    engagement_rate REAL DEFAULT 0,
    cps_score REAL DEFAULT 0,
    pulled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(publish_job_id, pulled_at)
);
CREATE INDEX IF NOT EXISTS idx_analytics_brand ON analytics(brand_id);
CREATE INDEX IF NOT EXISTS idx_analytics_platform ON analytics(platform);

-- Table 9: Hook Performance
CREATE TABLE IF NOT EXISTS hook_performance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand_id TEXT NOT NULL,
    hook_type TEXT NOT NULL,
    platform TEXT NOT NULL,
    avg_three_second_hold REAL DEFAULT 0,
    avg_retention_rate REAL DEFAULT 0,
    avg_cps_score REAL DEFAULT 0,
    sample_count INTEGER DEFAULT 0,
    weight REAL DEFAULT 1.0,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(brand_id, hook_type, platform)
);

-- Table 10: Rate Limit Tracking
CREATE TABLE IF NOT EXISTS rate_limits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    window_type TEXT NOT NULL,
    count INTEGER DEFAULT 0,
    units INTEGER DEFAULT 0,
    window_start TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    window_end TIMESTAMP,
    UNIQUE(brand_id, platform, endpoint, window_type)
);
CREATE INDEX IF NOT EXISTS idx_rate_limits_brand_platform ON rate_limits(brand_id, platform);

-- Table 11: Schedule History
CREATE TABLE IF NOT EXISTS schedule_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    window_hour INTEGER NOT NULL,
    window_minute INTEGER NOT NULL,
    actual_publish_time TIMESTAMP,
    cps_score REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_schedule_history_brand ON schedule_history(brand_id, platform);

-- Table 12: First Comments
CREATE TABLE IF NOT EXISTS first_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    publish_job_id INTEGER REFERENCES publish_jobs(id),
    brand_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    comment_text TEXT NOT NULL,
    comment_id TEXT,
    posted_at TIMESTAMP,
    status TEXT DEFAULT 'pending'
);

-- Table 13: UTM Links
CREATE TABLE IF NOT EXISTS utm_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    publish_job_id INTEGER REFERENCES publish_jobs(id),
    original_url TEXT NOT NULL,
    utm_url TEXT NOT NULL,
    clicks INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Table 14: User Agents
CREATE TABLE IF NOT EXISTS user_agents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand_id TEXT NOT NULL UNIQUE,
    ua_string TEXT NOT NULL,
    persona_os TEXT NOT NULL,
    persona_browser TEXT NOT NULL,
    generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Table 15: Notifications
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    notification_type TEXT NOT NULL,
    channel TEXT NOT NULL,
    recipient TEXT,
    subject TEXT,
    body TEXT,
    status TEXT DEFAULT 'pending',
    sent_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Table 16: System Config
CREATE TABLE IF NOT EXISTS system_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Table 17: Publish Mode Overrides
CREATE TABLE IF NOT EXISTS publish_mode_overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,
    brand_id TEXT,
    platform TEXT,
    mode TEXT NOT NULL DEFAULT 'review',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(scope, brand_id, platform)
);

-- ============================================================
-- NEW V6.0 TABLES
-- ============================================================

-- Table 18: Google Drive review file tracking
CREATE TABLE IF NOT EXISTS gdrive_review_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id TEXT NOT NULL UNIQUE,
    review_token TEXT NOT NULL,
    brand_id TEXT NOT NULL,
    file_type TEXT NOT NULL,
    preview_url TEXT,
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted INTEGER DEFAULT 0
);

-- Table 19: System metrics (lightweight monitoring)
CREATE TABLE IF NOT EXISTS system_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_name TEXT NOT NULL,
    metric_value REAL NOT NULL,
    label TEXT,
    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_system_metrics_name ON system_metrics(metric_name, recorded_at);

-- Table 20: A/B test tracking
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

-- Table 21: Brand safety evaluations
CREATE TABLE IF NOT EXISTS brand_safety_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    script_id INTEGER REFERENCES scripts(id),
    brand_id TEXT NOT NULL,
    safety_score REAL NOT NULL,
    passed INTEGER NOT NULL,
    issues TEXT,
    evaluated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Table 22: Content queue
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
CREATE INDEX IF NOT EXISTS idx_content_queue_brand ON content_queue(brand_id, platform);
CREATE INDEX IF NOT EXISTS idx_content_queue_status ON content_queue(status);

-- Table 23: Milestones
CREATE TABLE IF NOT EXISTS milestones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER REFERENCES accounts(id),
    milestone_type TEXT NOT NULL,
    reached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    notified INTEGER DEFAULT 0
);

-- Table 24: Circuit breaker state
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

-- Table 25: Background library
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
CREATE INDEX IF NOT EXISTS idx_background_library_brand ON background_library(brand_id);

-- Table 26: OCI backup objects
CREATE TABLE IF NOT EXISTS oci_backup_objects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    object_name TEXT NOT NULL UNIQUE,
    object_type TEXT DEFAULT 'backup',
    size_bytes INTEGER,
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deleted INTEGER DEFAULT 0
);

-- ============================================================
-- NEW V6.0 TRACKING TABLES (supplementary to the 26 above)
-- ============================================================

-- Job state tracking
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
CREATE INDEX IF NOT EXISTS idx_job_states_state ON job_states(state);
CREATE INDEX IF NOT EXISTS idx_job_states_brand ON job_states(brand_id);

-- LLM routing log
CREATE TABLE IF NOT EXISTS llm_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    task_type TEXT NOT NULL,
    brand_id TEXT,
    tokens_used INTEGER,
    latency_ms INTEGER,
    success INTEGER DEFAULT 1,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_llm_requests_provider ON llm_requests(provider, created_at);

-- Cross-brand dedup log
CREATE TABLE IF NOT EXISTS dedup_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    script_id INTEGER,
    brand_id TEXT NOT NULL,
    most_similar_brand TEXT,
    similarity_score REAL,
    passed INTEGER NOT NULL,
    checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Resource usage snapshots
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

-- ============================================================
-- DEFAULT DATA
-- ============================================================

-- Insert default system config
INSERT OR IGNORE INTO system_config (key, value) VALUES ('schema_version', '6.0');
INSERT OR IGNORE INTO system_config (key, value) VALUES ('publish_mode', 'review');
INSERT OR IGNORE INTO system_config (key, value) VALUES ('auto_approve_hours', '0');
INSERT OR IGNORE INTO system_config (key, value) VALUES ('queue_target_days', '3');
