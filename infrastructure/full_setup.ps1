# ============================================================================
# AutoFarm Zero — Complete OCI Infrastructure Setup (PowerShell)
# Run from local Windows machine with OCI CLI configured
# Prerequisites: oci cli installed (oci setup config)
#
# Usage: .\infrastructure\full_setup.ps1
# ============================================================================

$ErrorActionPreference = "Stop"

function Run-OCI {
    param([string]$Description, [string]$Command)
    Write-Host ""
    Write-Host ">>> $Description" -ForegroundColor Cyan
    $result = Invoke-Expression $Command 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: $result" -ForegroundColor Red
        throw "Failed: $Description"
    }
    return ($result | Out-String).Trim()
}

function Retry-OCI {
    param([string]$Description, [string]$Command, [int]$IntervalSeconds = 60)
    Write-Host ""
    Write-Host ">>> $Description (will retry until success)" -ForegroundColor Cyan
    while ($true) {
        Write-Host "[$(Get-Date -Format 'HH:mm:ss')] Attempting..." -ForegroundColor Yellow
        $result = Invoke-Expression $Command 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Host "SUCCESS!" -ForegroundColor Green
            return ($result | Out-String).Trim()
        }
        Write-Host "ERROR: $result" -ForegroundColor Red
        Write-Host "Retrying in $IntervalSeconds seconds... Press Ctrl+C to stop." -ForegroundColor Red
        Start-Sleep -Seconds $IntervalSeconds
    }
}

Write-Host "=============================================" -ForegroundColor Green
Write-Host "  AutoFarm Zero — OCI Infrastructure Setup"   -ForegroundColor Green
Write-Host "=============================================" -ForegroundColor Green

# ===================================================================
# STEP 1: Create Compartment
# ===================================================================
Write-Host ""
Write-Host "STEP 1: Creating Compartment" -ForegroundColor Magenta

$TENANCY_OCID = Run-OCI "Getting Tenancy OCID" `
    'oci iam compartment list --all --query "data[0].""compartment-id""" --raw-output'
Write-Host "Tenancy: $TENANCY_OCID"

$COMPARTMENT_OCID = Run-OCI "Creating compartment: autofarm-success-guru" `
    "oci iam compartment create --compartment-id $TENANCY_OCID --name autofarm-success-guru --description ""AutoFarm Zero Success Guru Network"" --query ""data.id"" --raw-output"
Write-Host "Compartment: $COMPARTMENT_OCID"

Write-Host "Waiting 30 seconds for compartment to activate..." -ForegroundColor Yellow
Start-Sleep -Seconds 30

# ===================================================================
# STEP 2: Create VCN, Internet Gateway, NAT Gateway
# ===================================================================
Write-Host ""
Write-Host "STEP 2: Creating Network Infrastructure" -ForegroundColor Magenta

$VCN_OCID = Run-OCI "Creating VCN" `
    "oci network vcn create --compartment-id $COMPARTMENT_OCID --cidr-block ""10.0.0.0/16"" --display-name ""autofarm-vcn"" --dns-label ""autofarmvcn"" --query ""data.id"" --raw-output"
Write-Host "VCN: $VCN_OCID"

$IGW_OCID = Run-OCI "Creating Internet Gateway" `
    "oci network internet-gateway create --compartment-id $COMPARTMENT_OCID --vcn-id $VCN_OCID --is-enabled true --display-name ""autofarm-igw"" --query ""data.id"" --raw-output"
Write-Host "IGW: $IGW_OCID"

$NAT_OCID = Run-OCI "Creating NAT Gateway" `
    "oci network nat-gateway create --compartment-id $COMPARTMENT_OCID --vcn-id $VCN_OCID --display-name ""autofarm-nat"" --query ""data.id"" --raw-output"
Write-Host "NAT: $NAT_OCID"

# ===================================================================
# STEP 3: Create Security Lists
# ===================================================================
Write-Host ""
Write-Host "STEP 3: Creating Security Lists" -ForegroundColor Magenta

$CONTENT_SL_OCID = Run-OCI "Creating content subnet security list" `
    'oci network security-list create --compartment-id $COMPARTMENT_OCID --vcn-id $VCN_OCID --display-name "content-subnet-sl" --egress-security-rules ''[{"destination":"0.0.0.0/0","protocol":"all","isStateless":false}]'' --ingress-security-rules ''[{"source":"10.0.2.0/24","protocol":"6","isStateless":false,"tcpOptions":{"destinationPortRange":{"min":22,"max":22}}}]'' --query "data.id" --raw-output'
Write-Host "Content SL: $CONTENT_SL_OCID"

$PROXY_SL_OCID = Run-OCI "Creating proxy subnet security list" `
    'oci network security-list create --compartment-id $COMPARTMENT_OCID --vcn-id $VCN_OCID --display-name "proxy-subnet-sl" --egress-security-rules ''[{"destination":"0.0.0.0/0","protocol":"all","isStateless":false}]'' --ingress-security-rules ''[{"source":"0.0.0.0/0","protocol":"6","isStateless":false,"tcpOptions":{"destinationPortRange":{"min":22,"max":22}}},{"source":"0.0.0.0/0","protocol":"6","isStateless":false,"tcpOptions":{"destinationPortRange":{"min":8080,"max":8080}}},{"source":"10.0.1.0/24","protocol":"6","isStateless":false,"tcpOptions":{"destinationPortRange":{"min":3128,"max":3133}}}]'' --query "data.id" --raw-output'
Write-Host "Proxy SL: $PROXY_SL_OCID"

# ===================================================================
# STEP 4: Create Route Tables
# ===================================================================
Write-Host ""
Write-Host "STEP 4: Creating Route Tables" -ForegroundColor Magenta

$CONTENT_RT_OCID = Run-OCI "Creating content route table (via NAT)" `
    "oci network route-table create --compartment-id $COMPARTMENT_OCID --vcn-id $VCN_OCID --display-name ""content-rt"" --route-rules '[{""cidrBlock"":""0.0.0.0/0"",""networkEntityId"":""$NAT_OCID""}]' --query ""data.id"" --raw-output"
Write-Host "Content RT: $CONTENT_RT_OCID"

$PROXY_RT_OCID = Run-OCI "Creating proxy route table (via IGW)" `
    "oci network route-table create --compartment-id $COMPARTMENT_OCID --vcn-id $VCN_OCID --display-name ""proxy-rt"" --route-rules '[{""cidrBlock"":""0.0.0.0/0"",""networkEntityId"":""$IGW_OCID""}]' --query ""data.id"" --raw-output"
Write-Host "Proxy RT: $PROXY_RT_OCID"

# ===================================================================
# STEP 5: Create Subnets
# ===================================================================
Write-Host ""
Write-Host "STEP 5: Creating Subnets" -ForegroundColor Magenta

$CONTENT_SUBNET_OCID = Run-OCI "Creating content subnet (private)" `
    "oci network subnet create --compartment-id $COMPARTMENT_OCID --vcn-id $VCN_OCID --cidr-block ""10.0.1.0/24"" --display-name ""content-subnet"" --prohibit-public-ip-on-vnic true --route-table-id $CONTENT_RT_OCID --security-list-ids ""[""""$CONTENT_SL_OCID""""]"" --query ""data.id"" --raw-output"
Write-Host "Content Subnet: $CONTENT_SUBNET_OCID"

$PROXY_SUBNET_OCID = Run-OCI "Creating proxy subnet (public)" `
    "oci network subnet create --compartment-id $COMPARTMENT_OCID --vcn-id $VCN_OCID --cidr-block ""10.0.2.0/24"" --display-name ""proxy-subnet"" --prohibit-public-ip-on-vnic false --route-table-id $PROXY_RT_OCID --security-list-ids ""[""""$PROXY_SL_OCID""""]"" --query ""data.id"" --raw-output"
Write-Host "Proxy Subnet: $PROXY_SUBNET_OCID"

# ===================================================================
# STEP 6: Get Availability Domain and Ubuntu ARM Image
# ===================================================================
Write-Host ""
Write-Host "STEP 6: Getting Availability Domain and Image" -ForegroundColor Magenta

$AD = Run-OCI "Getting availability domain" `
    "oci iam availability-domain list --compartment-id $COMPARTMENT_OCID --query ""data[0].name"" --raw-output"
Write-Host "AD: $AD"

$IMAGE_OCID = Run-OCI "Getting Ubuntu 22.04 ARM image" `
    'oci compute image list --compartment-id $COMPARTMENT_OCID --operating-system "Canonical Ubuntu" --operating-system-version "22.04" --shape "VM.Standard.A1.Flex" --sort-by TIMECREATED --sort-order DESC --query "data[0].id" --raw-output'
Write-Host "Image: $IMAGE_OCID"

# ===================================================================
# STEP 7: Create VMs (with retry for capacity)
# ===================================================================
Write-Host ""
Write-Host "STEP 7: Creating Virtual Machines" -ForegroundColor Magenta
Write-Host "NOTE: If 'Out of host capacity', will retry every 60 seconds" -ForegroundColor Yellow

$PROXY_VM_OCID = Retry-OCI "Creating proxy-vm (1 OCPU, 4GB RAM)" `
    "oci compute instance launch --compartment-id $COMPARTMENT_OCID --availability-domain $AD --image-id $IMAGE_OCID --shape VM.Standard.A1.Flex --shape-config '{""ocpus"":1,""memoryInGBs"":4}' --subnet-id $PROXY_SUBNET_OCID --assign-public-ip true --display-name ""autofarm-proxy-vm"" --ssh-authorized-keys-file $HOME\.ssh\id_rsa.pub --query ""data.id"" --raw-output"
Write-Host "Proxy VM: $PROXY_VM_OCID"

$CONTENT_VM_OCID = Retry-OCI "Creating content-vm (3 OCPU, 20GB RAM)" `
    "oci compute instance launch --compartment-id $COMPARTMENT_OCID --availability-domain $AD --image-id $IMAGE_OCID --shape VM.Standard.A1.Flex --shape-config '{""ocpus"":3,""memoryInGBs"":20}' --subnet-id $CONTENT_SUBNET_OCID --assign-public-ip false --display-name ""autofarm-content-vm"" --ssh-authorized-keys-file $HOME\.ssh\id_rsa.pub --query ""data.id"" --raw-output"
Write-Host "Content VM: $CONTENT_VM_OCID"

# ===================================================================
# STEP 8: Create Object Storage Bucket
# ===================================================================
Write-Host ""
Write-Host "STEP 8: Creating Object Storage Bucket" -ForegroundColor Magenta

Run-OCI "Creating autofarm-backups bucket" `
    "oci os bucket create --compartment-id $COMPARTMENT_OCID --name ""autofarm-backups"" --versioning Disabled"

Run-OCI "Setting 14-day lifecycle policy" `
    'oci os object-lifecycle-policy put --bucket-name "autofarm-backups" --items ''[{"action":"DELETE","is-enabled":true,"name":"auto-delete-old-backups","object-name-filter":{"inclusion-prefixes":["backup/"]},"time-amount":14,"time-unit":"DAYS"}]'''

# ===================================================================
# STEP 9: Create Secondary VNICs for IP Separation
# ===================================================================
Write-Host ""
Write-Host "STEP 9: Creating Secondary VNICs" -ForegroundColor Magenta

$VNIC_B_JSON = '{"subnetId":"' + $PROXY_SUBNET_OCID + '","assignPublicIp":true,"displayName":"autofarm-proxy-vnic-b","skipSourceDestCheck":false}'
Run-OCI "Creating VNIC B (zen + social brands)" `
    "oci compute instance attach-vnic --instance-id $PROXY_VM_OCID --create-vnic-details '$VNIC_B_JSON' --query ""data.id"" --raw-output"

$VNIC_C_JSON = '{"subnetId":"' + $PROXY_SUBNET_OCID + '","assignPublicIp":true,"displayName":"autofarm-proxy-vnic-c","skipSourceDestCheck":false}'
Run-OCI "Creating VNIC C (habits + relationships brands)" `
    "oci compute instance attach-vnic --instance-id $PROXY_VM_OCID --create-vnic-details '$VNIC_C_JSON' --query ""data.id"" --raw-output"

Write-Host "Waiting 15 seconds for VNICs to attach..." -ForegroundColor Yellow
Start-Sleep -Seconds 15

# ===================================================================
# STEP 10: Get IP Addresses
# ===================================================================
Write-Host ""
Write-Host "STEP 10: Retrieving IP Addresses" -ForegroundColor Magenta

$CONTENT_PRIVATE_IP = Run-OCI "Getting content-vm private IP" `
    "oci compute instance list-vnics --instance-id $CONTENT_VM_OCID --query ""data[0].""""private-ip"""""" --raw-output"

$PROXY_PUBLIC_IP = Run-OCI "Getting proxy-vm public IP" `
    "oci compute instance list-vnics --instance-id $PROXY_VM_OCID --query ""data[0].""""public-ip"""""" --raw-output"

Write-Host ""
Write-Host "Fetching all proxy-vm VNIC IPs..."
oci compute instance list-vnics --instance-id $PROXY_VM_OCID --query "data[*].{name:""display-name"", private_ip:""private-ip"", public_ip:""public-ip""}" --output table

# ===================================================================
# SUMMARY
# ===================================================================
Write-Host ""
Write-Host "=============================================" -ForegroundColor Green
Write-Host "  INFRASTRUCTURE CREATED SUCCESSFULLY"        -ForegroundColor Green
Write-Host "=============================================" -ForegroundColor Green
Write-Host ""
Write-Host "Content VM private IP: $CONTENT_PRIVATE_IP" -ForegroundColor White
Write-Host "Proxy VM public IP:    $PROXY_PUBLIC_IP" -ForegroundColor White
Write-Host ""
Write-Host "--- Save these for your .env file ---" -ForegroundColor Yellow
Write-Host "COMPARTMENT_OCID=$COMPARTMENT_OCID"
Write-Host "VCN_OCID=$VCN_OCID"
Write-Host "CONTENT_VM_PRIVATE_IP=$CONTENT_PRIVATE_IP"
Write-Host "PROXY_VM_PUBLIC_IP=$PROXY_PUBLIC_IP"
Write-Host "CONTENT_VM_OCID=$CONTENT_VM_OCID"
Write-Host "PROXY_VM_OCID=$PROXY_VM_OCID"
Write-Host ""
Write-Host "--- NEXT STEPS ---" -ForegroundColor Yellow
Write-Host "1. SSH to proxy-vm:   ssh ubuntu@$PROXY_PUBLIC_IP"
Write-Host "2. Run on proxy-vm:   bash infrastructure/setup_proxy_vm.sh"
Write-Host "3. From proxy-vm, SSH to content-vm: ssh ubuntu@$CONTENT_PRIVATE_IP"
Write-Host "4. Run on content-vm: bash scripts/setup_content_vm.sh"
Write-Host ""
Write-Host "NOTE: Write down the VNIC table above and update your .env with all 3 public IPs" -ForegroundColor Yellow
