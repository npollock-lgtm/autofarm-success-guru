#!/bin/bash
# Creates a fully isolated OCI compartment for AutoFarm
# Sourced by full_setup.sh — exports COMPARTMENT_OCID, VCN_OCID, IGW_OCID, NAT_OCID
set -e

COMPARTMENT_NAME="autofarm-success-guru"
TENANCY_OCID=$(oci iam compartment list --all --query "data[0].\"compartment-id\"" --raw-output)

echo "Creating compartment: $COMPARTMENT_NAME"
COMPARTMENT_OCID=$(oci iam compartment create \
  --compartment-id $TENANCY_OCID \
  --name $COMPARTMENT_NAME \
  --description "AutoFarm Zero Success Guru Network - isolated content farm" \
  --query "data.id" --raw-output)

echo "Compartment created: $COMPARTMENT_OCID"

# Wait for compartment to be active
echo "Waiting for compartment to become active..."
sleep 30

echo "Creating VCN..."
VCN_OCID=$(oci network vcn create \
  --compartment-id $COMPARTMENT_OCID \
  --cidr-block "10.0.0.0/16" \
  --display-name "autofarm-vcn" \
  --dns-label "autofarmvcn" \
  --query "data.id" --raw-output)

echo "VCN created: $VCN_OCID"

echo "Creating Internet Gateway..."
IGW_OCID=$(oci network internet-gateway create \
  --compartment-id $COMPARTMENT_OCID \
  --vcn-id $VCN_OCID \
  --is-enabled true \
  --display-name "autofarm-igw" \
  --query "data.id" --raw-output)

echo "Internet Gateway created: $IGW_OCID"

echo "Creating NAT Gateway..."
NAT_OCID=$(oci network nat-gateway create \
  --compartment-id $COMPARTMENT_OCID \
  --vcn-id $VCN_OCID \
  --display-name "autofarm-nat" \
  --query "data.id" --raw-output)

echo "NAT Gateway created: $NAT_OCID"

# Export variables for use by full_setup.sh
export COMPARTMENT_OCID VCN_OCID IGW_OCID NAT_OCID

echo "Infrastructure base created."
echo "COMPARTMENT_OCID=$COMPARTMENT_OCID"
echo "VCN_OCID=$VCN_OCID"
echo "IGW_OCID=$IGW_OCID"
echo "NAT_OCID=$NAT_OCID"
