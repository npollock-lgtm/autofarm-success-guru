# ============================================================================
# Makefile — AutoFarm Zero — Success Guru Network v6.0
#
# Primary developer interface for building, testing, and running the system.
# All targets assume the project virtualenv is managed by `uv`.
# ============================================================================

SHELL := /bin/bash
PYTHON := .venv/bin/python
UV := uv
APP_DIR := /app
DB_PATH := data/autofarm.db

.PHONY: help install install-dev init-db run test lint format check \
        validate-config create-dirs download-fonts predownload-bg \
        install-cron install-kokoro setup-gdrive encrypt-key \
        backup-db test-proxy test-pipeline clean

# ---------------------------------------------------------------------------
# Default target
# ---------------------------------------------------------------------------
help: ## Show this help message
	@echo "AutoFarm Zero — Success Guru Network v6.0"
	@echo ""
	@echo "Usage: make [target]"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'
	@echo ""

# ---------------------------------------------------------------------------
# Installation
# ---------------------------------------------------------------------------
install: ## Install production dependencies with uv
	$(UV) sync

install-dev: ## Install production + dev dependencies
	$(UV) sync --dev

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
init-db: ## Initialise SQLite database with schema (26 tables, WAL mode)
	$(PYTHON) scripts/init_db.py

backup-db: ## Run manual database backup
	$(PYTHON) -m jobs.backup_database

# ---------------------------------------------------------------------------
# Setup helpers (run once during deployment)
# ---------------------------------------------------------------------------
create-dirs: ## Create all required data directories
	$(PYTHON) scripts/create_directories.py

download-fonts: ## Download Google Fonts for all brands
	$(PYTHON) scripts/download_fonts.py

predownload-bg: ## Pre-download background video clips for all brands
	$(PYTHON) scripts/predownload_backgrounds.py

install-cron: ## Install crontab entries for all scheduled jobs
	$(PYTHON) scripts/install_cron.py content-vm

install-kokoro: ## Download Kokoro TTS voice models
	$(PYTHON) scripts/install_kokoro.py

setup-gdrive: ## Interactive Google Drive OAuth setup (optional)
	$(PYTHON) scripts/setup_gdrive_auth.py

encrypt-key: ## Generate and store a new Fernet encryption key
	$(PYTHON) scripts/generate_encryption_key.py

# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------
run: ## Launch the full AutoFarm system (all cron jobs + daemons)
	@echo "Starting AutoFarm Zero v6.0 ..."
	@echo "  - Validating configuration..."
	$(PYTHON) scripts/validate_config.py
	@echo "  - Starting supervisord daemons..."
	supervisorctl start autofarm-content:*
	@echo "  - Cron jobs managed by system crontab."
	@echo "AutoFarm is running. Use 'make status' to check health."

status: ## Show system health status
	@echo "=== AutoFarm Zero v6.0 — System Status ==="
	@supervisorctl status autofarm-content:* 2>/dev/null || echo "  supervisord not available (local dev)"
	@echo ""
	@echo "Database:"
	@ls -lh $(DB_PATH) 2>/dev/null || echo "  Database not initialised. Run: make init-db"
	@echo ""
	@echo "Disk usage:"
	@du -sh data/ 2>/dev/null || echo "  No data directory"

# ---------------------------------------------------------------------------
# Testing
# ---------------------------------------------------------------------------
test: ## Run the full test suite (unittest)
	$(PYTHON) -m pytest tests/ -v --tb=short 2>/dev/null || \
	$(PYTHON) -m unittest discover -s tests -v

test-pipeline: ## Run the 35-test end-to-end pipeline test
	$(PYTHON) scripts/test_pipeline.py

test-proxy: ## Test proxy routing for all 6 brands
	$(PYTHON) scripts/test_proxy_routing.py

# ---------------------------------------------------------------------------
# Code quality
# ---------------------------------------------------------------------------
lint: ## Run ruff linter on all source files
	$(UV) run ruff check modules/ database/ account_manager/ jobs/ scripts/ config/

format: ## Auto-format code with ruff
	$(UV) run ruff format modules/ database/ account_manager/ jobs/ scripts/ config/

check: validate-config lint ## Run config validation + linting

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
validate-config: ## Validate all configuration files, env vars, and database
	$(PYTHON) scripts/validate_config.py

# ---------------------------------------------------------------------------
# Account management
# ---------------------------------------------------------------------------
add-brand: ## Interactive: add a new brand to the network
	$(PYTHON) scripts/add_brand.py

add-account: ## Interactive: register a new platform account
	$(PYTHON) scripts/add_account.py

list-accounts: ## List all registered platform accounts
	$(PYTHON) scripts/list_accounts.py

toggle-mode: ## Toggle publish mode (dry_run / live / review_only)
	$(PYTHON) scripts/toggle_publish_mode.py

approve: ## CLI approve/reject pending reviews
	$(PYTHON) scripts/approve_content.py

# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------
clean: ## Remove temporary files and __pycache__ directories
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache
	rm -rf data/temp/* 2>/dev/null || true
	@echo "Cleaned temporary files."
