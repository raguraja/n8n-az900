# AZ-900 Study Bot

A self-hosted Telegram study bot for the **Microsoft Azure AZ-900** certification exam. Powered by **Ollama** (local LLM) and orchestrated via **n8n** on a Linux server.

---

## How It Works

### Overview

```
Telegram <-> n8n Workflows <-> Ollama (qwen2:7b)
                  |
         ~/.az900/data/state.json     (quiz state & progress)
         ~/.az900/data/progress.md    (human-readable summary)
```

Three n8n workflows handle everything:

| Workflow | Role |
|---|---|
| **AZ-900 Scheduler** | Picks a topic, generates a lesson via Ollama, sends it to Telegram, then sends a quiz 5 min later |
| **AZ-900 Answer Handler** | Listens for your A/B/C/D reply or button tap, checks it, updates your score |
| **AZ-900 Daily Summary** | Sends a nightly progress report at 9 PM with accuracy stats and topic breakdown |

---

## Study Session Flow

Every hour (8 AM to 9 PM):

1. Scheduler picks an unseen AZ-900 topic
2. Sends lesson to Telegram with sections: WHAT IT IS / KEY CONCEPTS / ANALOGY / EXAM TIPS
3. Waits 5 minutes
4. Sends a 4-choice quiz with inline A/B/C/D buttons
5. You tap a button (or type A/B/C/D)
6. Answer Handler checks your answer and updates score
7. At 9 PM, Daily Summary is sent

---

## Topics Covered (42 total)

| Category | Topics |
|---|---|
| **Cloud Concepts** | IaaS/PaaS/SaaS, CapEx/OpEx, deployment models, high availability, scalability |
| **Azure Architecture** | Regions, availability zones, ARM, resource hierarchy, management groups |
| **Compute & Networking** | VMs, AKS, Functions, App Service, VNets, VPN Gateway, Load Balancer, Firewall |
| **Azure Storage** | Blob, Files, Queue, Table, redundancy tiers (LRS/ZRS/GRS), lifecycle policies |
| **Identity & Security** | Entra ID, RBAC, Zero Trust, MFA, Defender for Cloud, Key Vault, Sentinel |
| **Cost & Governance** | Pricing Calculator, Cost Management, Azure Policy, SLAs, Azure Advisor |

---

## Setup

### Requirements

- [n8n](https://n8n.io/) self-hosted (tested on v2.14.2)
- [Ollama](https://ollama.com/) running locally with `qwen2:7b`
- A Telegram Bot token (from @BotFather)
- Your Telegram Chat ID
- Python 3.10+ (for standalone mode)

```bash
ollama pull qwen2:7b
mkdir -p ~/.az900/data
pip install requests pytz
```

### 1. Import Workflows into n8n

1. Open your n8n instance at `http://YOUR_SERVER:5678`
2. Go to **Settings > Import**
3. Upload `n8n_workflows_export.json`
4. Three workflows will be imported: AZ-900 Scheduler, AZ-900 Answer Handler, AZ-900 Daily Summary

### 2. Set Credentials

In each workflow, configure:
- **Telegram Bot token** from @BotFather
- **Telegram Chat ID** (your personal chat ID)

To find your Chat ID: message your bot, then open
`https://api.telegram.org/botYOUR_TOKEN/getUpdates` and look for `"chat": {"id": ...}`

### 3. Activate Workflows

Toggle each workflow to **Active** in the n8n UI.

---

## Standalone Mode (without n8n)

If the n8n UI has issues, run the scheduler directly:

```bash
python3 az900_scheduler.py
```

Edit these lines in the script first:

```python
TELEGRAM_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
TELEGRAM_CHAT_ID = "YOUR_TELEGRAM_CHAT_ID"
OLLAMA_BASE = "http://127.0.0.1:11434"
MODEL = "qwen2:7b"
```

Run as a systemd service:

```ini
[Unit]
Description=AZ-900 Study Bot
After=network.target

[Service]
User=YOUR_USERNAME
WorkingDirectory=/home/YOUR_USERNAME
ExecStart=/usr/bin/python3 /home/YOUR_USERNAME/az900_scheduler.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now az900-bot
```

---

## Interacting With the Bot

| Action | How |
|---|---|
| Answer a quiz | Tap A / B / C / D buttons, or type the letter |
| Skip current quiz | Type `skip` or `/skip` |
| Reset pending quiz | Type `reset` or `/reset` |

---

## Progress Tracking

State is saved to `~/.az900/data/state.json` and a human-readable summary at `~/.az900/data/progress.md`, updated after every session.

Daily report example (sent at 9 PM):
```
Daily AZ-900 Report
Progress: 14/42 topics
Mastered: 5
Needs Attention: 2
Accuracy: 9/11 (81%)
```

---

## Active Hours

Default is 24/7. To restrict to study hours, edit `az900_scheduler.py`:

```python
ACTIVE_START = 8   # 8 AM
ACTIVE_END = 21    # 9 PM
```

Timezone is CST (America/Chicago). Change `pytz.timezone(...)` for your zone.
