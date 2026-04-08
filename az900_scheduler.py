#!/usr/bin/env python3
"""
AZ-900 Study Bot Scheduler - Linux version
Sends proactive lessons + quizzes to Telegram every hour.
Active window: 8 AM - 9 PM CST  |  Quiet: 9 PM - 8 AM
"""

import time
import json
import re
import random
import datetime
import requests
import os
import sys
import fcntl
import threading
from pathlib import Path

try:
    import pytz
    CST = pytz.timezone("America/Chicago")
    def now_cst():
        return datetime.datetime.now(CST)
except ImportError:
    def now_cst():
        return datetime.datetime.utcnow() - datetime.timedelta(hours=6)

TELEGRAM_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
TELEGRAM_CHAT_ID = "YOUR_TELEGRAM_CHAT_ID"
OLLAMA_BASE = "http://127.0.0.1:11434"
MODEL = "qwen2:7b"

AZ900_DIR     = Path.home() / ".az900"
PROGRESS_FILE = AZ900_DIR / "data" / "progress.md"
STATE_FILE    = AZ900_DIR / "data" / "state.json"
OFFSET_FILE   = Path.home() / ".az900_tg_offset"

state_lock = threading.Lock()

ACTIVE_START = 0
ACTIVE_END = 24
INTERVAL_HOURS = 1
QUIZ_DELAY_SECS = 300

AZ900_TOPICS = [
    ("Cloud Concepts", "Benefits of cloud: scalability, elasticity, agility, geo-distribution, disaster recovery"),
    ("Cloud Concepts", "CapEx vs OpEx and the consumption-based model"),
    ("Cloud Concepts", "Cloud service models: IaaS, PaaS, SaaS — definitions, examples, responsibilities"),
    ("Cloud Concepts", "Cloud deployment models: public, private, hybrid, multi-cloud"),
    ("Cloud Concepts", "High availability, scalability, reliability, predictability, security, governance, manageability"),
    ("Azure Architecture", "Azure regions, region pairs, and sovereign regions"),
    ("Azure Architecture", "Availability zones and datacenter resiliency"),
    ("Azure Architecture", "Azure resource hierarchy: management groups, subscriptions, resource groups, resources"),
    ("Azure Architecture", "Azure Resource Manager (ARM) and ARM templates / Bicep"),
    ("Compute & Networking", "Azure Virtual Machines: sizes, scale sets, availability sets, Spot VMs"),
    ("Compute & Networking", "Azure App Service: web apps, API apps, pricing tiers"),
    ("Compute & Networking", "Azure Container Instances (ACI) and Azure Kubernetes Service (AKS)"),
    ("Compute & Networking", "Azure Functions and serverless computing — triggers, bindings, hosting plans"),
    ("Compute & Networking", "Azure Virtual Desktop and Windows 365"),
    ("Compute & Networking", "Azure Virtual Networks: VNets, subnets, peering, private/public IPs"),
    ("Compute & Networking", "VPN Gateway vs ExpressRoute: when to use each"),
    ("Compute & Networking", "Azure DNS, Azure Firewall, DDoS Protection plans"),
    ("Compute & Networking", "Network Security Groups (NSGs) and Application Security Groups (ASGs)"),
    ("Compute & Networking", "Azure Load Balancer, Application Gateway, Traffic Manager, Front Door, CDN"),
    ("Azure Storage", "Azure Blob Storage: containers, objects, access tiers (hot/cool/cold/archive)"),
    ("Azure Storage", "Azure Files, Queue Storage, Table Storage — use cases"),
    ("Azure Storage", "Storage account types and redundancy: LRS, ZRS, GRS, GZRS, RA-GRS"),
    ("Azure Storage", "Azure Migrate and Azure Data Box family"),
    ("Azure Storage", "Storage lifecycle management policies"),
    ("Identity & Security", "Microsoft Entra ID (Azure AD): tenants, users, groups, B2B, B2C"),
    ("Identity & Security", "Authentication vs Authorization — MFA, SSPR, Passwordless, FIDO2"),
    ("Identity & Security", "Azure RBAC: built-in roles, scope levels, deny assignments"),
    ("Identity & Security", "Zero Trust model and defense-in-depth layers"),
    ("Identity & Security", "Microsoft Defender for Cloud — security posture, recommendations, alerts"),
    ("Identity & Security", "Microsoft Sentinel — SIEM/SOAR capabilities"),
    ("Identity & Security", "Azure Key Vault: secrets, keys, certificates"),
    ("Identity & Security", "Conditional Access policies and Privileged Identity Management (PIM)"),
    ("Identity & Security", "Microsoft Purview: data governance, compliance, information protection"),
    ("Cost & Governance", "Azure pricing factors: resource type, consumption, region, bandwidth"),
    ("Cost & Governance", "Azure Pricing Calculator vs Total Cost of Ownership (TCO) Calculator"),
    ("Cost & Governance", "Azure Cost Management + Billing: budgets, cost alerts, cost analysis"),
    ("Cost & Governance", "Azure Policy: definitions, initiatives, effects (deny, audit, append)"),
    ("Cost & Governance", "Resource Locks (ReadOnly vs Delete) and resource tags"),
    ("Cost & Governance", "Service Level Agreements (SLAs) and composite SLAs"),
    ("Cost & Governance", "Azure Service Health vs Azure Monitor vs Azure Advisor"),
    ("Cost & Governance", "Microsoft Cloud Adoption Framework (CAF) and Azure landing zones"),
    ("Management Tools", "Azure Portal, Cloud Shell, Azure CLI, Azure PowerShell — differences & use cases"),
    ("Management Tools", "Azure Arc: managing hybrid and multi-cloud resources"),
    ("Management Tools", "Azure Monitor: metrics, logs, alerts, dashboards, Application Insights"),
    ("Management Tools", "Log Analytics workspace and KQL queries"),
]

def get_offset():
    try:
        return int(OFFSET_FILE.read_text().strip()) if OFFSET_FILE.exists() else 0
    except:
        return 0

def save_offset(offset):
    OFFSET_FILE.write_text(str(offset))

def tg_get_updates(offset):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
            json={"offset": offset, "timeout": 10, "limit": 10},
            timeout=15,
        )
        return r.json().get("result", [])
    except Exception as e:
        print(f"[poll] Error: {e}", flush=True)
        return []

def extract_correct_answer(quiz_text):
    """Extract the correct answer letter from quiz text."""
    m = re.search(r"ANSWER:\s*([A-D])", quiz_text, re.IGNORECASE)
    return m.group(1).upper() if m else None

def handle_incoming(text, is_callback=False):
    """Process an incoming message or button press from the AZ-900 bot."""
    text = text.strip()
    text_up = text.upper()
    text_low = text.lower()

    # Reset/skip commands
    if text_low in ("skip", "reset", "/skip", "/reset"):
        with state_lock:
            state = load_state()
            if state.get("pending_quiz"):
                state["pending_quiz"] = None
                save_state(state)
                tg_send("🔄 Quiz skipped. Next lesson coming soon.")
                print("[poll] Quiz reset by user command", flush=True)
            else:
                tg_send("✅ No pending quiz to reset.")
        return

    # Quiz answer A/B/C/D
    if text_up in ("A", "B", "C", "D"):
        with state_lock:
            state = load_state()
            pending = state.get("pending_quiz")
            if not pending:
                tg_send("No active quiz right now.")
                return
            correct = pending.get("correct_answer")
            state["total_questions"] = state.get("total_questions", 0) + 1
            if correct and text_up == correct:
                state["correct_answers"] = state.get("correct_answers", 0) + 1
                reply = f"✅ Correct! The answer is {correct}."
            elif correct:
                reply = f"❌ Incorrect. The correct answer is {correct}."
            else:
                reply = f"✅ Answer recorded: {text_up}"
            state["pending_quiz"] = None
            save_state(state)
            tg_send(reply)
            print(f"[poll] Quiz answered: {text_up} (correct: {correct})", flush=True)
        return

    if not is_callback:
        tg_send("Send A, B, C, or D to answer the quiz. Send 'skip' or 'reset' to skip it.")

def poll_loop():
    """Background thread: polls AZ-900 bot for replies and button presses."""
    offset = get_offset()
    print(f"[poll] Starting AZ-900 Telegram poll (offset={offset})", flush=True)
    while True:
        try:
            updates = tg_get_updates(offset)
            for upd in updates:
                uid = upd.get("update_id", 0)
                offset = max(offset, uid + 1)
                save_offset(offset)

                msg = upd.get("message", {})
                text = msg.get("text", "")
                if text:
                    print(f"[poll] Message: {text[:60]}", flush=True)
                    handle_incoming(text, is_callback=False)

                cb = upd.get("callback_query", {})
                if cb:
                    data = cb.get("data", "")
                    cb_id = cb.get("id", "")
                    if data:
                        print(f"[poll] Button: {data}", flush=True)
                        # Acknowledge the button press
                        try:
                            requests.post(
                                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/answerCallbackQuery",
                                json={"callback_query_id": cb_id},
                                timeout=5,
                            )
                        except:
                            pass
                        handle_incoming(data, is_callback=True)
        except Exception as e:
            print(f"[poll] Loop error: {e}", flush=True)
        time.sleep(2)

def tg_send(text, parse_mode=None, reply_markup=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    if len(text) > 4000:
        text = text[:3990] + "\n...[truncated]"
    try:
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = reply_markup
        r = requests.post(url, json=payload, timeout=30)
        result = r.json()
        if not result.get("ok"):
            print(f"[tg_send] Telegram error: {result.get('description')}", flush=True)
        return result
    except Exception as e:
        print(f"[tg_send] Error: {e}", flush=True)
        return None

def ollama_chat(prompt, system="You are an expert Azure cloud certification tutor for AZ-900.", temperature=0.7):
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "options": {
            "num_predict": 2048,
            "num_ctx": 8192,
            "temperature": temperature,
        },
    }
    try:
        r = requests.post(f"{OLLAMA_BASE}/api/chat", json=payload, timeout=300)
        r.raise_for_status()
        return r.json().get("message", {}).get("content", "").strip()
    except Exception as e:
        return f"ERROR: {e}"

def load_state():
    if not STATE_FILE.exists():
        return {
            "seen_topics": [],
            "needs_attention": [],
            "mastered": [],
            "pending_quiz": None,
            "last_topic": None,
            "total_sessions": 0,
            "correct_answers": 0,
            "total_questions": 0,
            "sessions_since_attention": 3,
            "seen_questions_by_topic": {}
        }
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except:
        return {}

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

def load_progress():
    return PROGRESS_FILE.read_text(encoding="utf-8") if PROGRESS_FILE.exists() else ""

def update_progress(state):
    """Update progress.md file with current study state"""
    seen = state.get("seen_topics", [])
    mastered = state.get("mastered", [])
    needs_attention = state.get("needs_attention", [])
    total_sessions = state.get("total_sessions", 0)
    correct_answers = state.get("correct_answers", 0)
    total_questions = state.get("total_questions", 0)

    progress_md = f"""# AZ-900 Study Progress

**Last Updated**: {now_cst().strftime("%Y-%m-%d %H:%M %p CST")}

## Summary
- Total Sessions: {total_sessions}
- Quiz Accuracy: {correct_answers}/{total_questions} ({(correct_answers*100//max(1,total_questions))}%)
- Topics Seen: {len(seen)}/42
- Mastered: {len(mastered)}
- Needs Attention: {len(needs_attention)}

## Mastered Topics ({len(mastered)})
{chr(10).join(f"- {t}" for t in sorted(mastered)) if mastered else "None yet"}

## Needs Attention ({len(needs_attention)})
{chr(10).join(f"- {t}" for t in sorted(needs_attention)) if needs_attention else "None"}

## Topics Seen ({len(seen)})
{chr(10).join(f"- {t}" for t in sorted(seen)) if seen else "None yet"}
"""

    PROGRESS_FILE.write_text(progress_md, encoding="utf-8")
    print(f"[progress] Updated: {len(seen)} topics, {len(mastered)} mastered, {len(needs_attention)} need attention", flush=True)

def send_daily_report(state):
    """Send daily study summary report"""
    seen = state.get("seen_topics", [])
    mastered = state.get("mastered", [])
    needs_attention = state.get("needs_attention", [])
    total_sessions = state.get("total_sessions", 0)
    correct_answers = state.get("correct_answers", 0)
    total_questions = state.get("total_questions", 0)

    accuracy = (correct_answers * 100) // max(1, total_questions)

    report = f"""📊 *Daily AZ-900 Report*
__{now_cst().strftime("%A, %B %d")}__

*Progress*: {len(seen)}/42 topics
*Mastered*: {len(mastered)} ⭐
*Needs Attention*: {len(needs_attention)} 📍
*Sessions*: {total_sessions}
*Accuracy*: {correct_answers}/{total_questions} ({accuracy}%)

*Top Mastered*: {", ".join(mastered[:3]) if mastered else "None yet"}

*Focus Areas*: {", ".join(needs_attention[:3]) if needs_attention else "Keep going!"}
"""

    tg_send(report)
    print(f"[report] Daily summary sent", flush=True)

def pick_topic(state):
    needs_attention = state.get("needs_attention", [])
    seen = state.get("seen_topics", [])
    mastered = state.get("mastered", [])
    last_topic = (state.get("last_topic") or {}).get("topic", "")

    candidates = [t for t in AZ900_TOPICS if t[1] not in seen and t[1] not in mastered and t[1] != last_topic]
    if candidates:
        return random.choice(candidates)

    candidates = [t for t in AZ900_TOPICS if t[1] not in mastered and t[1] != last_topic]
    if candidates:
        return random.choice(candidates)

    return random.choice(AZ900_TOPICS)

def generate_lesson(category, topic, progress_text):
    prompt = f"""Create a concise AZ-900 study lesson.
Category: {category}
Topic: {topic}
Format with sections: WHAT IT IS, KEY CONCEPTS, REAL-WORLD ANALOGY, EXAM TIPS, QUICK MEMORIZATION
Keep under 500 words."""
    return ollama_chat(prompt)

def generate_quiz(category, topic):
    prompt = f"""Create 1 AZ-900 multiple-choice question.
Category: {category}
Topic: {topic}
Format:
Q1: [question]
A) [option]
B) [option]
C) [option]
D) [option]
ANSWER: [letter]"""
    return ollama_chat(prompt, temperature=0.7)

def is_active():
    h = now_cst().hour
    return ACTIVE_START <= h < ACTIVE_END

def secs_until_active():
    n = now_cst()
    if n.hour < ACTIVE_START:
        target = n.replace(hour=ACTIVE_START, minute=0, second=0, microsecond=0)
    else:
        tomorrow = n + datetime.timedelta(days=1)
        target = tomorrow.replace(hour=ACTIVE_START, minute=0, second=0, microsecond=0)
    delta = target - n
    return max(0, delta.total_seconds())

def run_session():
    state = load_state()
    pending = state.get("pending_quiz")
    if pending:
        print(f"Pending quiz unanswered, skipping", flush=True)
        return

    category, topic = pick_topic(state)
    ts = now_cst().strftime("%Y-%m-%d %H:%M %p CST")
    print(f"[{ts}] Session: {category} / {topic}", flush=True)

    tg_send(f"📚 AZ-900 Study Session\n📌 {topic}\n_Generating lesson..._")

    lesson = generate_lesson(category, topic, load_progress())
    if lesson.startswith("ERROR"):
        tg_send(f"Lesson error: {lesson}")
        return

    tg_send(f"{topic}\n\n{lesson}")

    seen = state.get("seen_topics", [])
    if topic not in seen:
        seen.append(topic)
    state["seen_topics"] = seen
    state["last_topic"] = {"category": category, "topic": topic, "time": ts}
    state["total_sessions"] = state.get("total_sessions", 0) + 1
    save_state(state)
    update_progress(state)

    tg_send("Quiz in 5 minutes...")
    time.sleep(QUIZ_DELAY_SECS)

    quiz = generate_quiz(category, topic)
    if quiz.startswith("ERROR"):
        tg_send(f"Quiz error: {quiz}")
        return

    # Create inline buttons for quiz answers
    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "A", "callback_data": "A"},
                {"text": "B", "callback_data": "B"},
                {"text": "C", "callback_data": "C"},
                {"text": "D", "callback_data": "D"},
            ],
            [
                {"text": "🔄 Reset", "callback_data": "reset"}
            ]
        ]
    }

    quiz_display = re.sub(r'\n?ANSWER:\s*[A-D].*', '', quiz, flags=re.IGNORECASE).strip()
    tg_send(f"🎯 Quiz\n\n{quiz_display}\n\nTap a button OR type your answer (A, B, C, or D):", reply_markup=reply_markup)

    correct_answer = extract_correct_answer(quiz)
    with state_lock:
        state = load_state()
        state["pending_quiz"] = {"topic": topic, "sent_at": ts, "correct_answer": correct_answer}
        save_state(state)
    update_progress(state)
    print("Quiz sent", flush=True)

def main():
    # Prevent duplicate instances using an exclusive file lock
    lockfile = open('/tmp/az900_scheduler.lock', 'w')
    try:
        fcntl.flock(lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("ERROR: Another instance of az900_scheduler.py is already running. Exiting.", flush=True)
        sys.exit(1)
    lockfile.write(str(os.getpid()))
    lockfile.flush()

    print("AZ-900 Scheduler started on Linux", flush=True)
    last_report_date = None

    # Start Telegram polling in background
    t = threading.Thread(target=poll_loop, daemon=True)
    t.start()

    if not is_active():
        wait_secs = secs_until_active()
        print(f"Waiting for active hours ({wait_secs/3600:.1f}h)", flush=True)
        time.sleep(wait_secs)

    while True:
        now = now_cst()
        state = load_state()

        # Send daily report at 9 PM (end of active hours), once per day
        if now.hour == 21 and (last_report_date is None or last_report_date != now.date()):
            try:
                send_daily_report(state)
                last_report_date = now.date()
            except Exception as e:
                print(f"Report error: {e}", flush=True)

        if is_active():
            try:
                run_session()
            except Exception as e:
                print(f"Error: {e}", flush=True)
                tg_send(f"AZ-900 error: {e}")

            time.sleep(30)  # Check every 30 seconds for quiz answer or next session
        else:
            wait_secs = secs_until_active()
            print(f"Quiet hours, next session in {wait_secs/3600:.1f}h", flush=True)
            time.sleep(min(wait_secs, 3600))

if __name__ == "__main__":
    main()
