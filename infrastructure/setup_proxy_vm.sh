#!/bin/bash
# Run on proxy-vm after SSH access is confirmed
# Sets up 6 brand-specific Squid proxy instances + approval server + Telegram bot
set -e
echo "Setting up AutoFarm proxy-vm..."

sudo apt update
sudo apt install -y squid net-tools python3.11 python3.11-venv git curl wget iproute2 supervisor

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
echo "Configuring secondary VNIC interfaces..."
echo "NOTE: Replace placeholder IPs with actual values from OCI console"

# Uncomment and set actual IPs after getting VNIC assignments:
# sudo ip addr add {PRIVATE_IP_B}/24 dev eth1
# sudo ip addr add {PRIVATE_IP_C}/24 dev eth2
# sudo ip link set eth1 up
# sudo ip link set eth2 up

# Policy routing to prevent asymmetric routing
# Uncomment after setting actual IPs:
# echo "1 eth0rt" | sudo tee -a /etc/iproute2/rt_tables
# echo "2 eth1rt" | sudo tee -a /etc/iproute2/rt_tables
# echo "3 eth2rt" | sudo tee -a /etc/iproute2/rt_tables
#
# sudo ip route add default via {GATEWAY} dev eth0 table eth0rt
# sudo ip route add default via {GATEWAY} dev eth1 table eth1rt
# sudo ip route add default via {GATEWAY} dev eth2 table eth2rt
#
# sudo ip rule add from {PRIVATE_IP_A} table eth0rt
# sudo ip rule add from {PRIVATE_IP_B} table eth1rt
# sudo ip rule add from {PRIVATE_IP_C} table eth2rt

# Generate per-brand Squid configs from template
python3 /app/scripts/generate_squid_configs.py

# Create per-brand systemd service files
for brand in human_success_guru wealth_success_guru zen_success_guru \
             social_success_guru habits_success_guru relationships_success_guru; do
    cat > /tmp/squid-$brand.service << SVCEOF
[Unit]
Description=Squid proxy for brand $brand
After=network.target

[Service]
Type=forking
ExecStart=/usr/sbin/squid -f /etc/squid/$brand/squid.conf -n $brand
ExecReload=/bin/kill -HUP \$MAINPID
ExecStop=/usr/sbin/squid -f /etc/squid/$brand/squid.conf -n $brand -k shutdown
PIDFile=/var/run/squid/${brand}.pid
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
SVCEOF
    sudo mv /tmp/squid-$brand.service /etc/systemd/system/squid-$brand.service
    sudo systemctl daemon-reload
    sudo systemctl enable squid-$brand
    sudo systemctl start squid-$brand
    echo "Started squid-$brand"
done

# Firewall: only allow approval server, SSH, and content-vm
CONTENT_VM_PRIVATE_IP=$(grep CONTENT_VM_PRIVATE_IP /app/.env | cut -d= -f2)
sudo iptables -A INPUT -i lo -j ACCEPT
sudo iptables -A INPUT -m state --state ESTABLISHED,RELATED -j ACCEPT
sudo iptables -A INPUT -s ${CONTENT_VM_PRIVATE_IP}/32 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 8080 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 22 -j ACCEPT
sudo iptables -A INPUT -j DROP
sudo apt install -y iptables-persistent
sudo netfilter-persistent save

# Set up supervisord for approval server and Telegram bot
sudo cp config/supervisord_proxy.conf /etc/supervisor/conf.d/autofarm-proxy.conf
sudo supervisorctl reread && sudo supervisorctl update

echo "Proxy VM setup complete"
echo "Testing all 6 brand proxies..."
python3 /app/scripts/test_proxy_routing.py
echo "Approval server: http://$(curl -s ifconfig.me):8080"
