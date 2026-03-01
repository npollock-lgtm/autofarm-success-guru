#!/bin/bash
# Sets up secondary VNICs on the proxy-vm for IP-isolated brand publishing
# Usage: source infrastructure/setup_secondary_vnics.sh <PROXY_VM_OCID>
# Called from full_setup.sh
set -e

PROXY_VM_OCID=$1

if [ -z "$PROXY_VM_OCID" ]; then
    echo "Error: PROXY_VM_OCID required as first argument"
    exit 1
fi

echo "Setting up secondary VNICs on proxy-vm for IP separation..."

# Get the primary VNIC info
PRIMARY_VNIC_ID=$(oci compute instance list-vnics \
  --instance-id $PROXY_VM_OCID \
  --query "data[0].id" --raw-output)

# Determine availability domain from the instance
AD=$(oci compute instance get \
  --instance-id $PROXY_VM_OCID \
  --query "data.\"availability-domain\"" --raw-output)

echo "Creating Secondary VNIC B (for zen + social brands)..."
VNIC_B_ATTACH=$(oci compute instance attach-vnic \
  --instance-id $PROXY_VM_OCID \
  --create-vnic-details "{
    \"subnetId\": \"$PROXY_SUBNET_OCID\",
    \"assignPublicIp\": true,
    \"displayName\": \"autofarm-proxy-vnic-b\",
    \"skipSourceDestCheck\": false
  }" \
  --query "data.id" --raw-output)

echo "Creating Secondary VNIC C (for habits + relationships brands)..."
VNIC_C_ATTACH=$(oci compute instance attach-vnic \
  --instance-id $PROXY_VM_OCID \
  --create-vnic-details "{
    \"subnetId\": \"$PROXY_SUBNET_OCID\",
    \"assignPublicIp\": true,
    \"displayName\": \"autofarm-proxy-vnic-c\",
    \"skipSourceDestCheck\": false
  }" \
  --query "data.id" --raw-output)

echo "Waiting for VNICs to attach..."
sleep 15

# Get the IPs assigned to each VNIC
echo ""
echo "VNIC IP Assignments:"
oci compute instance list-vnics \
  --instance-id $PROXY_VM_OCID \
  --query "data[*].{name:\"display-name\", private_ip:\"private-ip\", public_ip:\"public-ip\"}" \
  --output table

echo ""
echo "Secondary VNICs created successfully."
echo ""
echo "IP Mapping (update .env with these):"
echo "  Group A (human + wealth):        Primary VNIC public IP"
echo "  Group B (zen + social):           VNIC B public IP"
echo "  Group C (habits + relationships): VNIC C public IP"
echo ""
echo "IMPORTANT: Run the VNIC configuration script on proxy-vm after SSH:"
echo "  sudo /usr/local/bin/secondary_vnic_all_configure.sh"
echo "  (Available from Oracle's secondary VNIC scripts package)"
