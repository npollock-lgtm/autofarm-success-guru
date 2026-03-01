#!/bin/bash
# scripts/setup_content_vm.sh — Run on content-vm after SSH access via proxy-vm
# Sets up the full AutoFarm V6 content generation environment
set -e

echo "Setting up AutoFarm content-vm (V6.0)..."

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

echo "Content VM setup complete (V6.0)"
echo "Swap: 8GB configured"
echo "Ollama: llama3.1:8b (primary LLM)"
echo "Kokoro TTS: installed"
echo "Whisper: NOT installed (not needed)"
echo ""
echo "Next: Edit .env with API keys, then: python scripts/add_account.py"
