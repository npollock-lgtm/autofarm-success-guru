# AutoFarm Zero — Network Architecture Diagram

## OCI Infrastructure Layout

```
                          INTERNET
                             |
                    +--------+--------+
                    |  OCI Tenancy    |
                    |  (uk-london-1)  |
                    +--------+--------+
                             |
              +==============+===============+
              |  Compartment:                |
              |  autofarm-success-guru       |
              +==============+===============+
                             |
                   VCN: autofarm-vcn
                   CIDR: 10.0.0.0/16
                             |
              +--------------+--------------+
              |                             |
    +---------+----------+       +----------+---------+
    | content-subnet     |       | proxy-subnet       |
    | 10.0.1.0/24        |       | 10.0.2.0/24        |
    | PRIVATE (NAT GW)   |       | PUBLIC (IGW)       |
    +--------------------+       +--------------------+
    |                    |       |                    |
    | content-vm         |       | proxy-vm           |
    | A1 Flex            |       | A1 Flex            |
    | 3 OCPU / 20GB RAM  |       | 1 OCPU / 4GB RAM   |
    | + 8GB swap         |       |                    |
    | 150GB block vol    |       | 50GB block vol     |
    |                    |       |                    |
    | Services:          |       | Network Interfaces:|
    |  - Ollama (LLaMA)  |       |  eth0: Primary     |
    |  - Kokoro TTS      |  SSH  |    -> Public IP A   |
    |  - FFmpeg          | <---> |  eth1: VNIC B      |
    |  - SQLite DB       |       |    -> Public IP B   |
    |  - Python modules  |       |  eth2: VNIC C      |
    |  - Supervisord     |       |    -> Public IP C   |
    |  - Idle Guard      |       |                    |
    |  - Cron jobs       |       | Services:          |
    |                    |       |  - 6x Squid proxy  |
    +--------------------+       |  - Approval server |
              |                  |  - Telegram bot    |
              |                  |  - Supervisord     |
         NAT Gateway             +--------------------+
              |                           |
              v                  Internet Gateway
          INTERNET                        |
    (trend scanning,                      v
     background fetch,              INTERNET
     Ollama/Groq API)         (platform APIs,
                               Telegram API)
```

## IP Routing — Brand to Public IP Mapping

```
Brand                        Squid Port  Interface  Public IP
-------------------------------------------------------------------
human_success_guru           3128        eth0       Public IP A
wealth_success_guru          3129        eth0       Public IP A
zen_success_guru             3130        eth1       Public IP B
social_success_guru          3131        eth1       Public IP B
habits_success_guru          3132        eth2       Public IP C
relationships_success_guru   3133        eth2       Public IP C
```

## Data Flow — Content Generation to Publishing

```
1. TREND SCAN (content-vm -> internet via NAT)
   content-vm -> NAT GW -> Reddit/NewsAPI/Google Trends

2. SCRIPT GENERATION (content-vm local)
   content-vm -> Ollama (localhost:11434)
   content-vm -> Groq API (fallback, via NAT GW)

3. VIDEO ASSEMBLY (content-vm local)
   Kokoro TTS -> FFmpeg -> local storage

4. REVIEW (content-vm -> proxy-vm -> Telegram)
   content-vm -> proxy-vm:8080 -> Telegram Bot API

5. PUBLISH (content-vm -> proxy-vm -> platform APIs)
   content-vm -> squid:{brand_port} -> platform API
   Each brand routes through its dedicated IP

6. ANALYTICS (content-vm -> internet via NAT)
   content-vm -> NAT GW -> platform analytics APIs
```

## Security Boundaries

```
+-------------------------------------------+
|  content-subnet (PRIVATE)                 |
|  - No public IP                           |
|  - Outbound only via NAT Gateway          |
|  - Inbound: SSH from proxy-subnet only    |
|  - All platform API calls routed through  |
|    proxy-vm Squid instances               |
+-------------------------------------------+
              |
              | SSH (port 22) from proxy only
              |
+-------------------------------------------+
|  proxy-subnet (PUBLIC)                    |
|  - 3 public IPs (primary + 2 VNICs)      |
|  - Inbound: SSH (22), Approval (8080)     |
|  - Squid ports (3128-3133) from content   |
|    subnet only                            |
|  - iptables: default DROP                 |
+-------------------------------------------+
```

## Object Storage

```
OCI Object Storage (20GB free tier)
  Bucket: autofarm-backups
    /backup/autofarm_YYYYMMDD_HHMMSS.db
    Lifecycle: auto-delete after 14 days
    Alert threshold: 16GB (80%)
```
