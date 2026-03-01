# AutoFarm V6 — Step-by-Step Deployment Guide

This guide walks you through deploying AutoFarm on Oracle Cloud, written for non-technical users. Follow every step in order. Do NOT skip ahead.

---

## TABLE OF CONTENTS

1. [What You Need Before Starting](#1-what-you-need-before-starting)
2. [Create Your Oracle Cloud Account](#2-create-your-oracle-cloud-account)
3. [Set Up Your Local Computer](#3-set-up-your-local-computer)
4. [Create the Cloud Infrastructure](#4-create-the-cloud-infrastructure)
5. [Set Up the Proxy Server](#5-set-up-the-proxy-server)
6. [Set Up the Content Server](#6-set-up-the-content-server)
7. [Get Your API Keys](#7-get-your-api-keys)
8. [Configure the System](#8-configure-the-system)
9. [Register Social Media Accounts](#9-register-social-media-accounts)
10. [Launch and Test](#10-launch-and-test)
11. [Daily Operations](#11-daily-operations)
12. [Troubleshooting](#12-troubleshooting)

---

## 1. WHAT YOU NEED BEFORE STARTING

Before you start, gather these items. You WILL need all of them.

### Required Accounts (free)
- [ ] **Oracle Cloud account** — You'll create this in Step 2
- [ ] **Gmail account** — For sending email notifications (you may already have one)
- [ ] **Telegram account** — For reviewing content before it publishes
- [ ] **Groq account** — Free AI API (backup brain) — https://console.groq.com
- [ ] **Pexels account** — Free stock video — https://www.pexels.com/api
- [ ] **Pixabay account** — Free stock video — https://pixabay.com/api/docs

### Required Social Media Accounts (create all 6 brands on each platform)
You need 6 separate accounts on each of these platforms:
- [ ] TikTok (6 accounts)
- [ ] Instagram (6 accounts)
- [ ] Facebook Pages (6 pages)
- [ ] YouTube (6 channels) — requires Google Cloud projects
- [ ] Snapchat (6 accounts)

The 6 brand names are:
1. Human Success Guru
2. Wealth Success Guru
3. Zen Success Guru
4. Social Success Guru
5. Habits Success Guru
6. Relationships Success Guru

### Hardware Requirements
- A computer with internet access (Windows, Mac, or Linux)
- The computer is only needed during setup — after that, everything runs in the cloud

### Time Required
- First-time setup: **4-6 hours** (spread across a day or two)
- Most of that time is waiting for things to install

---

## 2. CREATE YOUR ORACLE CLOUD ACCOUNT

Oracle Cloud offers an **Always-Free Tier** that gives you two small servers for free, forever. This is where AutoFarm will run.

### Step 2.1 — Sign Up

1. Go to: **https://www.oracle.com/cloud/free/**
2. Click **"Start for free"**
3. Fill in your details:
   - Use your real name and email
   - Choose **"United Kingdom South (London)"** as your region (this is important — the system is configured for `uk-london-1`)
   - You'll need a credit card for verification, but you will NOT be charged
4. Complete the verification process
5. Wait for the confirmation email (can take up to 30 minutes)
6. Sign in to your new account at **https://cloud.oracle.com**

### Step 2.2 — Verify Free Tier Availability

1. Once logged in, click the hamburger menu (☰) at the top left
2. Go to **Governance → Limits, Quotas and Usage**
3. Search for "VM.Standard.A1.Flex"
4. Confirm you see **4 OCPUs** and **24 GB RAM** available
5. If you see 0, your account may need 24-48 hours to fully activate

> **IMPORTANT**: If you chose a different region than London, you'll need to change the `OCI_REGION` setting in the `.env` file later. The system works in any region, but London was the default.

---

## 3. SET UP YOUR LOCAL COMPUTER

You need to install a tool called "OCI CLI" on your computer. This lets you talk to Oracle Cloud from your command line.

### For Windows Users:

1. **Install Windows Terminal** (if you don't have it):
   - Open the Microsoft Store
   - Search for "Windows Terminal"
   - Click Install

2. **Install OCI CLI**:
   - Open Windows Terminal (or PowerShell)
   - Paste this command and press Enter:
     ```
     powershell -NoProfile -ExecutionPolicy Bypass -Command "iex ((New-Object System.Net.WebClient).DownloadString('https://raw.githubusercontent.com/oracle/oci-cli/master/scripts/install/install.ps1'))"
     ```
   - Follow the prompts (just press Enter for defaults)
   - Close and reopen your terminal

3. **Configure OCI CLI**:
   - Run: `oci setup config`
   - It will ask you several questions:
     - **Config file location**: Press Enter (accept default)
     - **User OCID**: Go to Oracle Cloud → click your profile icon (top right) → click your username → copy the OCID (starts with `ocid1.user...`)
     - **Tenancy OCID**: Go to Oracle Cloud → Administration → Tenancy Details → copy the OCID
     - **Region**: Type `uk-london-1` (or your region)
     - **Generate API key**: Type `Y`
     - **Key file location**: Press Enter (accept default)
   - It will show you a **public key**. Copy the entire key.
   - Go to Oracle Cloud → your profile → API Keys → Add API Key → Paste Public Key → paste what you copied → Add

4. **Create an SSH Key** (this is your "password" to connect to your servers):
   - Run: `ssh-keygen -t rsa -b 4096`
   - Press Enter for all prompts (no password needed)
   - This creates two files:
     - `C:\Users\YourName\.ssh\id_rsa` (private key — NEVER share this)
     - `C:\Users\YourName\.ssh\id_rsa.pub` (public key — this goes to Oracle)

### For Mac Users:

1. **Install OCI CLI**:
   - Open Terminal (Applications → Utilities → Terminal)
   - Paste: `bash -c "$(curl -L https://raw.githubusercontent.com/oracle/oci-cli/master/scripts/install/install.sh)"`
   - Follow the prompts

2. **Configure OCI CLI**: Same as Windows Step 3 above

3. **Create SSH Key**: Same as Windows Step 4 above (the files go to `~/.ssh/`)

### Verify It Works

Run this command:
```
oci iam availability-domain list
```
If you see a JSON response with your availability domain, you're good. If you see an error, double-check your API key was added correctly.

---

## 4. CREATE THE CLOUD INFRASTRUCTURE

This step creates your two servers and all the networking. The scripts in the project do this automatically.

### Step 4.1 — Upload the Project Code

First, get the AutoFarm code onto a place where your servers can download it. The easiest way:

1. Create a **private GitHub repository**:
   - Go to https://github.com/new
   - Name it something like `autofarm-success-guru`
   - Select **Private**
   - Click "Create repository"

2. Upload all the project files to this repository:
   - If you have Git installed: open terminal in the project folder and run:
     ```
     git init
     git add .
     git commit -m "Initial commit"
     git remote add origin https://github.com/YOUR_USERNAME/autofarm-success-guru.git
     git push -u origin main
     ```
   - If you DON'T have Git: download GitHub Desktop from https://desktop.github.com, then use its interface to create the repository and upload files

3. **Update the clone URLs** in two files:
   - Open `infrastructure/setup_proxy_vm.sh` — find the line that says `git clone https://github.com/your-repo/autofarm-success-guru.git /app` and change `your-repo` to your actual GitHub username
   - Open `scripts/setup_content_vm.sh` — same change

### Step 4.2 — Run the Infrastructure Setup

1. Open your terminal
2. Navigate to the project folder:
   - Windows: `cd C:\Users\YourName\Documents\autofarm`
   - Mac: `cd ~/Documents/autofarm`
3. Run the setup script:
   ```
   bash infrastructure/full_setup.sh
   ```
4. This will take **5-15 minutes**. It creates:
   - A compartment (isolated area) in Oracle Cloud
   - A virtual network with two subnets
   - Two servers:
     - **content-vm**: 3 CPUs, 20 GB RAM (does the heavy work — AI, video creation)
     - **proxy-vm**: 1 CPU, 4 GB RAM (handles internet traffic, reviews)
   - A storage bucket for backups
   - Three separate public IP addresses (so brands look independent)

5. **IMPORTANT**: When it finishes, it will print several values. **Write these down**:
   - Content VM private IP (like `10.0.1.xxx`)
   - Proxy VM public IP (like `132.xxx.xxx.xxx`)
   - COMPARTMENT_OCID
   - CONTENT_VM_OCID
   - PROXY_VM_OCID

> **If the script fails**: The most common error is "Out of host capacity" — this means Oracle doesn't have free ARM servers available right now. Wait a few hours and try again. It can sometimes take several attempts over a day or two.

---

## 5. SET UP THE PROXY SERVER

The proxy server is the "front door" — it handles all communication with social media platforms and the Telegram review system.

### Step 5.1 — Connect to the Proxy Server

1. Open your terminal
2. Connect via SSH:
   ```
   ssh ubuntu@PROXY_PUBLIC_IP
   ```
   Replace `PROXY_PUBLIC_IP` with the IP address from Step 4.
3. Type `yes` when asked about the fingerprint
4. You should see a Ubuntu welcome message

### Step 5.2 — Run the Proxy Setup Script

Once connected, run:
```
bash infrastructure/setup_proxy_vm.sh
```

This takes **10-20 minutes** and automatically:
- Installs Squid proxy software (6 independent instances, one per brand)
- Sets up the approval server (where you review content)
- Sets up the Telegram notification bot
- Configures the firewall
- Starts all services

### Step 5.3 — Configure the Secondary Network Interfaces

After the script finishes, you need to configure the extra IP addresses:

1. Go to Oracle Cloud Console in your web browser
2. Navigate: ☰ → Compute → Instances → click **autofarm-proxy-vm**
3. Scroll down to **Attached VNICs** → you should see 3 VNICs
4. Click each one and note down the **Private IP** and **Public IP** for each:
   - Primary VNIC → Public IP A (for human_success_guru + wealth_success_guru)
   - VNIC B → Public IP B (for zen_success_guru + social_success_guru)
   - VNIC C → Public IP C (for habits_success_guru + relationships_success_guru)
5. Back in your SSH session on proxy-vm, edit the setup script to add the actual IPs:
   ```
   sudo nano /etc/network/interfaces
   ```
   Or run Oracle's VNIC configuration tool:
   ```
   sudo /usr/local/bin/secondary_vnic_all_configure.sh
   ```

### Step 5.4 — Run Security Hardening

Still connected to the proxy server:
```
bash infrastructure/security_hardening.sh
```

This locks down the server (disables password login, enables brute-force protection).

### Step 5.5 — Verify Proxy Setup

```
python3 /app/scripts/test_proxy_routing.py
```

You should see all 6 brands showing different IP addresses. If not, the secondary VNICs need reconfiguring.

---

## 6. SET UP THE CONTENT SERVER

The content server is the "brain" — it runs the AI, creates videos, and manages everything.

### Step 6.1 — Connect to the Content Server

You can ONLY reach the content server through the proxy server (it has no public IP for security). From your proxy-vm SSH session:

```
ssh ubuntu@CONTENT_PRIVATE_IP
```

Replace `CONTENT_PRIVATE_IP` with the private IP from Step 4 (starts with `10.0.1.`).

### Step 6.2 — Run the Content Server Setup

```
bash scripts/setup_content_vm.sh
```

This takes **30-60 minutes** (the AI model download is large). It installs:
- FFmpeg (video creation tool)
- Ollama + LLaMA 3.1 AI model (the "brain")
- Kokoro TTS (text-to-speech voices — one per brand)
- Python and all dependencies
- Sets up 8 GB swap space (safety net for memory)
- Creates the database (26 tables)
- Installs all scheduled tasks (cron jobs)

### Step 6.3 — Run Security Hardening

```
bash infrastructure/security_hardening.sh
```

---

## 7. GET YOUR API KEYS

You need API keys from several services. Here's how to get each one:

### 7.1 — Groq API Key (backup AI)

1. Go to https://console.groq.com
2. Sign up for free
3. Go to API Keys → Create API Key
4. Copy the key — save it somewhere safe

### 7.2 — Pexels API Key (stock video)

1. Go to https://www.pexels.com/api/
2. Click "Get Started" and create an account
3. Your API key will be on your dashboard after approval

### 7.3 — Pixabay API Key (stock video)

1. Go to https://pixabay.com/api/docs/
2. Create an account
3. Your API key appears on the API documentation page

### 7.4 — Telegram Bot Token

1. Open Telegram on your phone
2. Search for **@BotFather** and start a chat
3. Send: `/newbot`
4. Follow the prompts — give it a name like "AutoFarm Review Bot"
5. BotFather will give you a **token** — save it
6. Create a **private group** in Telegram for reviews
7. Add your bot to the group
8. To get the **Chat ID**:
   - Send any message in the group
   - Go to: `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
   - Look for `"chat":{"id":-XXXXXXXXX}` — that negative number is your Chat ID

### 7.5 — Gmail App Password

1. Go to https://myaccount.google.com/security
2. Enable **2-Factor Authentication** if not already on
3. Go to App Passwords (search "App passwords" in your Google account settings)
4. Select "Mail" and "Other" → name it "AutoFarm"
5. Google will show you a 16-character password — save it

### 7.6 — YouTube API (if using YouTube)

This is more complex — you need Google Cloud projects:
1. Go to https://console.cloud.google.com
2. Create a new project for each brand pair (3 projects total — YouTube allows 6 channels across 3 projects due to quota limits)
3. Enable the **YouTube Data API v3** in each project
4. Create **OAuth 2.0 credentials** in each project
5. The `config/youtube_projects.json` file maps brands to projects

> **TIP**: YouTube setup is the most complex part. You can skip YouTube initially and add it later. The system works fine with just TikTok, Instagram, Facebook, and Snapchat.

---

## 8. CONFIGURE THE SYSTEM

### Step 8.1 — Edit the Environment File

SSH into the content-vm and edit the `.env` file:

```
nano /app/.env
```

Fill in ALL the values. Here's what each one means:

```
# OCI INFRASTRUCTURE — these came from Step 4
OCI_REGION=uk-london-1
COMPARTMENT_OCID=ocid1.compartment.oc1..xxxxx     (from Step 4)
VCN_OCID=ocid1.vcn.oc1..xxxxx                      (from Step 4)
CONTENT_VM_PRIVATE_IP=10.0.1.xxx                    (from Step 4)

# PROXY VM — these came from Steps 4 and 5
PROXY_VM_INTERNAL_IP=10.0.2.xxx                     (from Step 4)
PROXY_VM_PUBLIC_IP=xxx.xxx.xxx.xxx                   (from Step 4)
PROXY_PRIVATE_IP_A=10.0.2.aaa                       (from Step 5.3)
PROXY_PRIVATE_IP_B=10.0.2.bbb                       (from Step 5.3)
PROXY_PRIVATE_IP_C=10.0.2.ccc                       (from Step 5.3)
PUBLIC_IP_GROUP_A=xxx.xxx.xxx.xxx                    (from Step 5.3)
PUBLIC_IP_GROUP_B=xxx.xxx.xxx.xxx                    (from Step 5.3)
PUBLIC_IP_GROUP_C=xxx.xxx.xxx.xxx                    (from Step 5.3)

# GMAIL — from Step 7.5
SMTP_USER=your_email@gmail.com
SMTP_PASSWORD=xxxx xxxx xxxx xxxx                    (the 16-char app password)

# TELEGRAM — from Step 7.4
TELEGRAM_BOT_TOKEN=123456:ABC-xxxxx
TELEGRAM_REVIEW_CHAT_ID=-100xxxxxxxxx
TELEGRAM_ALERTS_CHAT_ID=-100xxxxxxxxx                (can be same as review chat)

# API KEYS — from Steps 7.1-7.3
GROQ_API_KEY=gsk_xxxxx
PEXELS_API_KEY=xxxxx
PIXABAY_API_KEY=xxxxx

# SYSTEM — leave these defaults unless you know what you're doing
PUBLISH_MODE=review                                  (start with review mode!)
AUTO_APPROVE_HOURS=0                                 (0 = manual approval only)
OLLAMA_MODEL=llama3.1:8b
```

Save: Press `Ctrl+X`, then `Y`, then `Enter`.

### Step 8.2 — Generate Encryption Key

```
python scripts/generate_encryption_key.py
```

This creates a secret key that encrypts your social media passwords in the database.

### Step 8.3 — Validate Everything

```
python scripts/validate_config.py
```

This checks ALL your settings and tells you if anything is missing or wrong. Fix any errors it reports before continuing.

---

## 9. REGISTER SOCIAL MEDIA ACCOUNTS

For each of your 30 accounts (6 brands × 5 platforms), you need to register the OAuth credentials.

### Step 9.1 — Add Each Account

Run the interactive registration tool:

```
python scripts/add_account.py
```

It will ask you:
1. Which brand? (choose from the list)
2. Which platform? (tiktok/instagram/facebook/youtube/snapchat)
3. Your OAuth credentials for that account

You'll need to run this **30 times** (once per account). Each platform has slightly different OAuth flows:

- **TikTok**: Requires TikTok for Developers app → OAuth redirect
- **Instagram/Facebook**: Requires Meta for Developers app → Graph API tokens
- **YouTube**: Requires Google Cloud OAuth credentials (from Step 7.6)
- **Snapchat**: Requires Snap Kit developer account → OAuth

> **TIP**: Start with just TikTok for all 6 brands. Get that working first, then add more platforms one at a time.

### Step 9.2 — Verify Accounts

```
python scripts/list_accounts.py
```

This shows a table of all registered accounts and their status.

---

## 10. LAUNCH AND TEST

### Step 10.1 — Test the Full Pipeline

Before going live, run the test suite:

```
python scripts/test_pipeline.py
```

This runs 35 tests checking every part of the system. You want to see mostly PASS. Some tests may show SKIP (that's OK for optional features like Google Drive).

### Step 10.2 — Start in Review Mode

The system should already be set to `PUBLISH_MODE=review`, which means:
- Videos are created automatically
- BUT nothing publishes until YOU approve it via Telegram

Start the system:
```
make run
```

### Step 10.3 — Wait for First Content

The system scans for trends every 2 hours. After a few hours, you should receive your first review request in Telegram:
- You'll see a thumbnail image
- A short preview video
- The full script text
- Two buttons: ✅ **Approve** or ❌ **Reject**

### Step 10.4 — Switch to Live Mode (when ready)

After you've reviewed and approved several videos and are happy with the quality:

```
python scripts/toggle_publish_mode.py
```

Choose `live` to let approved content publish automatically at optimal times.

---

## 11. DAILY OPERATIONS

### What Happens Automatically

Once running, the system handles everything:

| What | When | What It Does |
|------|------|-------------|
| Trend scanning | Every 2 hours | Finds trending topics |
| Content creation | After scan | Writes script, creates video |
| Review notification | Every 15 min | Sends new content to your Telegram |
| Publishing | Every 5 min | Posts approved content at optimal times |
| Analytics | Daily 3 AM | Pulls performance data |
| Database backup | Daily 2:30 AM | Backs up to Oracle Cloud storage |
| Health check | Continuous | Monitors system health |
| Token refresh | Daily 4:45 AM | Keeps social media logins alive |

### Your Daily Tasks

1. **Check Telegram** for review notifications (2-3 per day per brand)
2. **Approve or reject** content
3. **Check the dashboard** occasionally: `http://PROXY_PUBLIC_IP:8080/dashboard`
4. **Read the daily digest** (sent to Telegram at 8 AM) — shows yesterday's performance

### Useful Commands (SSH into content-vm)

```bash
# Check system status
make status

# See what's in the publishing queue
python -c "from modules.queue.content_queue import ContentQueue; q = ContentQueue(); print(q.get_queue_summary())"

# View the visual calendar
# Open in browser: http://PROXY_PUBLIC_IP:8080/calendar

# Check disk space
df -h

# View recent logs
tail -50 /app/logs/publish.log
tail -50 /app/logs/generate.log

# Run a manual trend scan (if you don't want to wait)
python -m jobs.scan_and_generate

# Force a manual analytics pull
python -m jobs.pull_analytics
```

---

## 12. TROUBLESHOOTING

### "Out of host capacity" when creating VMs

This is the most common issue. Oracle's free ARM servers are in high demand.
- **Solution**: Wait and try again. Use a script that retries automatically, or try at off-peak hours (early morning or late night).
- Some people report success after trying every 30 minutes for a few hours.

### Can't SSH to proxy-vm

- Verify the public IP is correct in Oracle Cloud Console
- Check that your SSH key was uploaded: Oracle Cloud → Compute → Instances → your instance → scroll to SSH keys
- Try: `ssh -v ubuntu@IP_ADDRESS` for verbose output

### Can't SSH from proxy-vm to content-vm

- The content-vm has no public IP — you MUST go through proxy-vm first
- Verify the private IP: Oracle Cloud → Compute → Instances → content-vm → Primary VNIC → Private IP
- Make sure the security list allows SSH from proxy subnet

### Ollama won't start / runs out of memory

- Check RAM: `free -h` (should show ~20 GB)
- Check swap: `swapon --show` (should show 8 GB)
- Restart Ollama: `sudo systemctl restart ollama`
- Check logs: `journalctl -u ollama -f`

### No Telegram notifications

- Verify bot token: `curl https://api.telegram.org/bot<TOKEN>/getMe`
- Verify chat ID: send a message in your group, then check `getUpdates`
- Make sure the bot is added to the group
- Check proxy-vm supervisor: `sudo supervisorctl status`

### Videos not publishing

- Check publish mode: `grep PUBLISH_MODE /app/.env` (should be `live` or `review`)
- Check the queue: `python -c "from modules.queue.content_queue import ContentQueue; q = ContentQueue(); print(q.get_queue_summary())"`
- Check rate limits: the system respects platform limits and won't over-post
- Check token status: `python scripts/list_accounts.py`
- Look at publish logs: `tail -100 /app/logs/publish.log`

### "Rate limit exceeded" errors

This is NORMAL. The system is designed to respect platform rate limits. It will automatically retry later. You don't need to do anything.

### System seems idle / not generating content

- The idle guard daemon prevents Oracle from reclaiming your VM — this is working correctly
- Content generation happens every 2 hours, not continuously
- Check the cron schedule: `crontab -l`
- Manually trigger: `python -m jobs.scan_and_generate`

### Database errors

- Check DB exists: `ls -la /app/data/autofarm.db`
- Check DB integrity: `sqlite3 /app/data/autofarm.db "PRAGMA integrity_check;"`
- Restore from backup: backups are in Oracle Object Storage, also locally at `/app/data/backups/`

---

## GLOSSARY

| Term | Meaning |
|------|---------|
| **SSH** | Secure Shell — how you remotely connect to a server (like remote desktop, but text-only) |
| **VM** | Virtual Machine — a server running in the cloud |
| **API Key** | A password that lets software talk to another service |
| **OAuth** | A way for you to let AutoFarm post to your social media without giving it your actual password |
| **Proxy** | An intermediary server — makes each brand's traffic come from a different IP address |
| **VNIC** | Virtual Network Interface Card — gives a server an additional IP address |
| **Cron** | A scheduler that runs tasks at specific times (like an alarm clock for programs) |
| **Supervisord** | A program that keeps other programs running (restarts them if they crash) |
| **WAL mode** | Write-Ahead Logging — makes the database faster and more reliable |
| **TTS** | Text-to-Speech — converts written scripts into spoken audio |
| **CPS** | Content Performance Score — the system's rating of how well a video performed (0-10) |
| **Dry run** | Testing mode where everything works except actually posting to social media |

---

## COST SUMMARY

| Item | Cost |
|------|------|
| Oracle Cloud (2 VMs + storage) | **Free forever** (Always-Free Tier) |
| Groq API | **Free** (generous free tier) |
| Pexels API | **Free** |
| Pixabay API | **Free** |
| Telegram Bot | **Free** |
| Gmail (for notifications) | **Free** |
| Google Drive (optional) | **Free** (15 GB) |
| **Total monthly cost** | **$0** |

> **Note**: If you exceed Groq's free tier (unlikely with 6 brands), the system automatically falls back to Ollama which runs locally on your content-vm at no cost. The Groq API is only used as a backup when Ollama is busy.
