#!/bin/bash
# Security hardening script — Run on both content-vm and proxy-vm
# Disables root SSH, enforces key-based auth, installs fail2ban and auto-updates
set -e
echo "Applying security hardening..."

# Disable root SSH login
sudo sed -i 's/^#*PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config

# Disable password authentication (key-only)
sudo sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config

# Disable empty passwords
sudo sed -i 's/^#*PermitEmptyPasswords.*/PermitEmptyPasswords no/' /etc/ssh/sshd_config

# Limit SSH to protocol 2
echo "Protocol 2" | sudo tee -a /etc/ssh/sshd_config

# Set SSH idle timeout (15 minutes)
sudo sed -i 's/^#*ClientAliveInterval.*/ClientAliveInterval 300/' /etc/ssh/sshd_config
sudo sed -i 's/^#*ClientAliveCountMax.*/ClientAliveCountMax 3/' /etc/ssh/sshd_config

# Restart SSH
sudo systemctl restart sshd

# Install and configure unattended-upgrades for automatic security patches
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure --priority=low unattended-upgrades

# Install and enable fail2ban for brute-force protection
sudo apt install -y fail2ban

# Create custom fail2ban jail config
cat > /tmp/autofarm-jail.local << 'EOF'
[DEFAULT]
bantime = 3600
findtime = 600
maxretry = 5
backend = systemd

[sshd]
enabled = true
port = ssh
filter = sshd
logpath = /var/log/auth.log
maxretry = 3
bantime = 7200
EOF
sudo mv /tmp/autofarm-jail.local /etc/fail2ban/jail.local

sudo systemctl enable fail2ban
sudo systemctl restart fail2ban

# Set up logrotate for AutoFarm logs
sudo cp config/logrotate.conf /etc/logrotate.d/autofarm

# Set restrictive permissions on application directory
sudo chown -R autofarm:autofarm /app 2>/dev/null || true
chmod 700 /app/data 2>/dev/null || true
chmod 600 /app/.env 2>/dev/null || true

# Disable core dumps (prevent credential leaks)
echo '* hard core 0' | sudo tee -a /etc/security/limits.conf
echo 'fs.suid_dumpable = 0' | sudo tee -a /etc/sysctl.conf
sudo sysctl -p

echo "Security hardening complete"
echo "  - Root SSH: disabled"
echo "  - Password auth: disabled"
echo "  - fail2ban: active (3 attempts, 2h ban)"
echo "  - Unattended upgrades: enabled"
echo "  - Core dumps: disabled"
echo "  - Log rotation: configured"
