"""
Global application settings for AutoFarm Zero — Success Guru Network v6.0.

Centralizes all configuration values, loading from environment variables
with sensible defaults. All modules import settings from here.
"""

import os
import json
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# === Paths ===
BASE_DIR = Path(os.getenv('APP_DIR', '/app'))
DATA_DIR = BASE_DIR / 'data'
MEDIA_DIR = BASE_DIR / 'media'
LOGS_DIR = BASE_DIR / 'logs'
CONFIG_DIR = BASE_DIR / 'config'

# === Database ===
DATABASE_PATH = os.getenv('DATABASE_PATH', str(DATA_DIR / 'autofarm.db'))
DB_BUSY_TIMEOUT_MS = 30000
DB_WAL_CHECKPOINT_PAGES = 1000
DB_CACHE_SIZE_KB = 64000

# === OCI Infrastructure ===
OCI_REGION = os.getenv('OCI_REGION', 'us-ashburn-1')
COMPARTMENT_OCID = os.getenv('COMPARTMENT_OCID', '')
VCN_OCID = os.getenv('VCN_OCID', '')
CONTENT_VM_PRIVATE_IP = os.getenv('CONTENT_VM_PRIVATE_IP', '10.0.1.2')

# === Proxy VM ===
PROXY_VM_INTERNAL_IP = os.getenv('PROXY_VM_INTERNAL_IP', '10.0.2.2')
PROXY_VM_PUBLIC_IP = os.getenv('PROXY_VM_PUBLIC_IP', '')
PROXY_PRIVATE_IP_A = os.getenv('PROXY_PRIVATE_IP_A', '')
PROXY_PRIVATE_IP_B = os.getenv('PROXY_PRIVATE_IP_B', '')
PROXY_PRIVATE_IP_C = os.getenv('PROXY_PRIVATE_IP_C', '')

PUBLIC_IP_GROUP_A = os.getenv('PUBLIC_IP_GROUP_A', '')
PUBLIC_IP_GROUP_B = os.getenv('PUBLIC_IP_GROUP_B', '')
PUBLIC_IP_GROUP_C = os.getenv('PUBLIC_IP_GROUP_C', '')

# Brand-to-proxy-port mapping
PROXY_PORTS = {
    'human_success_guru': int(os.getenv('PROXY_PORT_HUMAN_SUCCESS_GURU', '3128')),
    'wealth_success_guru': int(os.getenv('PROXY_PORT_WEALTH_SUCCESS_GURU', '3129')),
    'zen_success_guru': int(os.getenv('PROXY_PORT_ZEN_SUCCESS_GURU', '3130')),
    'social_success_guru': int(os.getenv('PROXY_PORT_SOCIAL_SUCCESS_GURU', '3131')),
    'habits_success_guru': int(os.getenv('PROXY_PORT_HABITS_SUCCESS_GURU', '3132')),
    'relationships_success_guru': int(os.getenv('PROXY_PORT_RELATIONSHIPS_SUCCESS_GURU', '3133')),
}

# Brand-to-IP-group mapping
BRAND_IP_GROUPS = {
    'human_success_guru': 'A',
    'wealth_success_guru': 'A',
    'zen_success_guru': 'B',
    'social_success_guru': 'B',
    'habits_success_guru': 'C',
    'relationships_success_guru': 'C',
}

# === SMTP ===
SMTP_HOST = os.getenv('SMTP_HOST', 'smtp.gmail.com')
SMTP_PORT = int(os.getenv('SMTP_PORT', '587'))
SMTP_USE_TLS = os.getenv('SMTP_USE_TLS', 'true').lower() == 'true'
SMTP_USER = os.getenv('SMTP_USER', '')
SMTP_PASSWORD = os.getenv('SMTP_PASSWORD', '')
SMTP_FROM_NAME = os.getenv('SMTP_FROM_NAME', 'Success Guru Network')

# === Telegram ===
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_REVIEW_CHAT_ID = os.getenv('TELEGRAM_REVIEW_CHAT_ID', '')
TELEGRAM_ALERTS_CHAT_ID = os.getenv('TELEGRAM_ALERTS_CHAT_ID', '')

# === Google Drive (optional) ===
GDRIVE_ENABLED = os.getenv('GDRIVE_ENABLED', 'false').lower() == 'true'
GDRIVE_CREDENTIALS_PATH = os.getenv('GDRIVE_CREDENTIALS_PATH', str(CONFIG_DIR / 'gdrive_credentials.json'))
GDRIVE_TOKEN_PATH = os.getenv('GDRIVE_TOKEN_PATH', str(CONFIG_DIR / 'gdrive_token.json'))
GDRIVE_REVIEW_FOLDER = os.getenv('GDRIVE_REVIEW_FOLDER', 'AutoFarm Reviews')
GDRIVE_FILE_EXPIRY_DAYS = int(os.getenv('GDRIVE_FILE_EXPIRY_DAYS', '14'))
GDRIVE_ALERT_THRESHOLD_GB = float(os.getenv('GDRIVE_ALERT_THRESHOLD_GB', '12'))

# === API Keys ===
GROQ_API_KEY = os.getenv('GROQ_API_KEY', '')
PEXELS_API_KEY = os.getenv('PEXELS_API_KEY', '')
PIXABAY_API_KEY = os.getenv('PIXABAY_API_KEY', '')
NEWSAPI_KEY = os.getenv('NEWSAPI_KEY', '')

# === Encryption ===
FERNET_KEY = os.getenv('FERNET_KEY', '')

# === System Settings ===
PUBLISH_MODE = os.getenv('PUBLISH_MODE', 'review')  # 'review' or 'auto'
AUTO_APPROVE_HOURS = int(os.getenv('AUTO_APPROVE_HOURS', '0'))
QUEUE_TARGET_DAYS = int(os.getenv('QUEUE_TARGET_DAYS', '3'))
MAX_CONCURRENT_VIDEO_ASSEMBLY = int(os.getenv('MAX_CONCURRENT_VIDEO_ASSEMBLY', '1'))
OLLAMA_MODEL = os.getenv('OLLAMA_MODEL', 'llama3.1:8b')
OLLAMA_HOST = os.getenv('OLLAMA_HOST', 'http://localhost:11434')

# === Content Pipeline ===
TREND_SCAN_INTERVAL_HOURS = 2
REVIEW_CHECK_INTERVAL_MINUTES = 15
PUBLISH_CHECK_INTERVAL_MINUTES = 5
RANDOM_WINDOW_MINUTES = 60
MIN_BRAND_SAFETY_SCORE = 7.0
CROSS_BRAND_SIMILARITY_THRESHOLD = 0.7
CROSS_BRAND_WINDOW_SIZE = 50

# === Resource Thresholds ===
VIDEO_ASSEMBLY_MIN_FREE_RAM_GB = 4
TTS_MIN_FREE_RAM_GB = 2
LLM_MIN_FREE_RAM_GB = 6
MAX_CPU_PERCENT_HEAVY_JOB = 70
MAX_DISK_PERCENT = 80
SWAP_WARNING_GB = 2

# === OCI Object Storage ===
OCI_BUCKET_NAME = 'autofarm-backups'
OCI_TOTAL_FREE_GB = 20
OCI_ALERT_THRESHOLD_GB = 16
BACKUP_RETENTION_DAYS = 14

# === Brand Config Loader ===
_brands_cache = None

def get_brands_config() -> dict:
    """Loads and caches brands.json configuration."""
    global _brands_cache
    if _brands_cache is None:
        brands_path = CONFIG_DIR / 'brands.json'
        with open(brands_path, 'r') as f:
            _brands_cache = json.load(f)
    return _brands_cache


def get_brand_ids() -> list[str]:
    """Returns list of all brand IDs."""
    config = get_brands_config()
    return list(config['brands'].keys())


def get_brand_config(brand_id: str) -> dict:
    """Returns configuration for a specific brand."""
    config = get_brands_config()
    return config['brands'][brand_id]


_platforms_cache = None

def get_platforms_config() -> dict:
    """Loads and caches platforms.json configuration."""
    global _platforms_cache
    if _platforms_cache is None:
        platforms_path = CONFIG_DIR / 'platforms.json'
        with open(platforms_path, 'r') as f:
            _platforms_cache = json.load(f)
    return _platforms_cache


def get_platform_config(platform: str) -> dict:
    """Returns configuration for a specific platform."""
    config = get_platforms_config()
    return config['platforms'][platform]


def get_all_brand_platforms() -> list[tuple[str, str]]:
    """Returns all (brand_id, platform) combinations."""
    brands = get_brands_config()
    pairs = []
    for brand_id, brand_cfg in brands['brands'].items():
        for platform in brand_cfg.get('platforms', []):
            pairs.append((brand_id, platform))
    return pairs
