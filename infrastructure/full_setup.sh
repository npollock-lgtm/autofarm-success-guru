#!/bin/bash
# AUTOFARM ZERO — Complete OCI Infrastructure Setup
# Run from local machine with OCI CLI configured
# Prerequisites: oci cli installed (oci setup config)
set -e
echo "AutoFarm Zero — OCI Infrastructure Setup"

# === STEP 1: Create Compartment ===
source infrastructure/create_compartment.sh

# === STEP 2: Determine Availability Domain ===
AD=$(oci iam availability-domain list \
  --compartment-id $COMPARTMENT_OCID \
  --query "data[0].name" --raw-output)
echo "Using availability domain: $AD"

# === STEP 3: Get Ubuntu 22.04 ARM image ===
UBUNTU_2204_ARM_IMAGE_OCID=$(oci compute image list \
  --compartment-id $COMPARTMENT_OCID \
  --operating-system "Canonical Ubuntu" \
  --operating-system-version "22.04" \
  --shape "VM.Standard.A1.Flex" \
  --sort-by TIMECREATED --sort-order DESC \
  --query "data[0].id" --raw-output)
echo "Ubuntu ARM image: $UBUNTU_2204_ARM_IMAGE_OCID"

# === STEP 4: Create Subnets and Security Lists ===

# Content subnet security list (private — only outbound via NAT)
CONTENT_SL_OCID=$(oci network security-list create \
  --compartment-id $COMPARTMENT_OCID \
  --vcn-id $VCN_OCID \
  --display-name "content-subnet-sl" \
  --egress-security-rules '[{"destination":"0.0.0.0/0","protocol":"all","isStateless":false}]' \
  --ingress-security-rules '[{"source":"10.0.2.0/24","protocol":"6","isStateless":false,"tcpOptions":{"destinationPortRange":{"min":22,"max":22}}}]' \
  --query "data.id" --raw-output)

# Proxy subnet security list (public — SSH, approval server, Squid ports)
PROXY_SL_OCID=$(oci network security-list create \
  --compartment-id $COMPARTMENT_OCID \
  --vcn-id $VCN_OCID \
  --display-name "proxy-subnet-sl" \
  --egress-security-rules '[{"destination":"0.0.0.0/0","protocol":"all","isStateless":false}]' \
  --ingress-security-rules '[
    {"source":"0.0.0.0/0","protocol":"6","isStateless":false,"tcpOptions":{"destinationPortRange":{"min":22,"max":22}}},
    {"source":"0.0.0.0/0","protocol":"6","isStateless":false,"tcpOptions":{"destinationPortRange":{"min":8080,"max":8080}}},
    {"source":"10.0.1.0/24","protocol":"6","isStateless":false,"tcpOptions":{"destinationPortRange":{"min":3128,"max":3133}}}
  ]' \
  --query "data.id" --raw-output)

# Route table for content subnet (via NAT gateway)
CONTENT_RT_OCID=$(oci network route-table create \
  --compartment-id $COMPARTMENT_OCID \
  --vcn-id $VCN_OCID \
  --display-name "content-rt" \
  --route-rules "[{\"cidrBlock\":\"0.0.0.0/0\",\"networkEntityId\":\"$NAT_OCID\"}]" \
  --query "data.id" --raw-output)

# Route table for proxy subnet (via Internet gateway)
PROXY_RT_OCID=$(oci network route-table create \
  --compartment-id $COMPARTMENT_OCID \
  --vcn-id $VCN_OCID \
  --display-name "proxy-rt" \
  --route-rules "[{\"cidrBlock\":\"0.0.0.0/0\",\"networkEntityId\":\"$IGW_OCID\"}]" \
  --query "data.id" --raw-output)

# Create subnets
CONTENT_SUBNET_OCID=$(oci network subnet create \
  --compartment-id $COMPARTMENT_OCID \
  --vcn-id $VCN_OCID \
  --cidr-block "10.0.1.0/24" \
  --display-name "content-subnet" \
  --prohibit-public-ip-on-vnic true \
  --route-table-id $CONTENT_RT_OCID \
  --security-list-ids "[\"$CONTENT_SL_OCID\"]" \
  --query "data.id" --raw-output)

PROXY_SUBNET_OCID=$(oci network subnet create \
  --compartment-id $COMPARTMENT_OCID \
  --vcn-id $VCN_OCID \
  --cidr-block "10.0.2.0/24" \
  --display-name "proxy-subnet" \
  --prohibit-public-ip-on-vnic false \
  --route-table-id $PROXY_RT_OCID \
  --security-list-ids "[\"$PROXY_SL_OCID\"]" \
  --query "data.id" --raw-output)

echo "Subnets created: content=$CONTENT_SUBNET_OCID proxy=$PROXY_SUBNET_OCID"

# === STEP 5: Create VMs ===
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

echo "Waiting for VMs to reach RUNNING state..."
oci compute instance get --instance-id $CONTENT_VM_OCID --wait-for-state RUNNING
oci compute instance get --instance-id $PROXY_VM_OCID --wait-for-state RUNNING

# === STEP 6: Create Object Storage Bucket (backups only) ===
oci os bucket create \
  --compartment-id $COMPARTMENT_OCID \
  --name "autofarm-backups" \
  --versioning Disabled

oci os object-lifecycle-policy put \
  --bucket-name "autofarm-backups" \
  --items '[{"action":"DELETE","is-enabled":true,"name":"auto-delete-old-backups","object-name-filter":{"inclusion-prefixes":["backup/"]},"time-amount":14,"time-unit":"DAYS"}]'

# === STEP 7: Secondary VNICs for IP separation ===
source infrastructure/setup_secondary_vnics.sh $PROXY_VM_OCID

# === STEP 8: Output connection info ===
CONTENT_PRIVATE_IP=$(oci compute instance list-vnics \
  --instance-id $CONTENT_VM_OCID \
  --query "data[0].\"private-ip\"" --raw-output)
PROXY_PUBLIC_IP=$(oci compute instance list-vnics \
  --instance-id $PROXY_VM_OCID \
  --query "data[0].\"public-ip\"" --raw-output)

echo ""
echo "Infrastructure created successfully"
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
echo "CONTENT_VM_OCID=$CONTENT_VM_OCID"
echo "PROXY_VM_OCID=$PROXY_VM_OCID"
