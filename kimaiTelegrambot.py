from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import httpx
import requests
from datetime import datetime, timedelta
import json
from dateutil import parser as dtparser
import re

# ==== Config ====
BOT_TOKEN = '7288992131:AAFspGS-i1HauVFR6v0jOokI0ep4dFop2Lg'
OPENROUTER_API_KEY = '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'
KIMAI_URL = 'http://98.83.74.209'
KIMAI_API_TOKEN = 'eebfa247a8c92a6d4e32b9f70'
ADMIN_USER_ID = 1

HEADERS = {
    "Authorization": f"Bearer {KIMAI_API_TOKEN}",
    "Content-Type": "application/json"
}

PROJECTS = {
    "exxonmobil": 1,
    "dell": 4,
    "goodyear": 3,
    "morrisons": 2,
    "pepsico": 5,
    "leave": 6
}
ACTIVITIES = {
    "ci/cd": 2,
    "devops task": 1,
    "infrastructure automation": 3,
    "itops": 4,
    "leave": 5  # Replace with your Leave activity ID if different
}

PROJECT_NAMES = {v: k.title() for k, v in PROJECTS.items()}
ACTIVITY_NAMES = {v: k.title() for k, v in ACTIVITIES.items()}

# ==== GPT Handler ====
def parse_with_gpt(text):
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",  # Correct Bearer header
        "Content-Type": "application/json"
    }
    payload = {
        "model": "openai/gpt-3.5-turbo",
        "messages": [
            {"role": "system", "content": (
                "You are a time tracking assistant. Extract project name, activity name, and a short description. "
                "Return either duration_hours (float) or start_time and end_time (ISO 8601 format). "
                "Respond in JSON with keys: project_name, activity_name, description, and either duration_hours or start_time and end_time. "
                "Valid projects: ExxonMobil, Dell, GoodYear, Morrisons, Pepsico, Leave. "
                "Valid activities: DevOps Task, CI/CD, Infrastructure automation, ITOps, Leave."
            )},
            {"role": "user", "content": text}
        ]
    }
    response = httpx.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload)
    if response.status_code == 200:
        return response.json()['choices'][0]['message']['content']
    raise Exception(f"GPT error {response.status_code}: {response.text}")

# ==== Date Extraction ====
def extract_date(text):
    try:
        match = re.search(r'\d{4}-\d{2}-\d{2}', text)
        if match:
            return dtparser.parse(match.group(0)).date()
        match = re.search(r'(\d{1,2}(st|nd|rd|th)?\s+\w+\s+\d{4})', text, re.IGNORECASE)
        if match:
            return dtparser.parse(match.group(1)).date()
        if 'today' in text.lower():
            return datetime.utcnow().date()
    except:
        pass
    return None

# ==== /start ====
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Hi Kiru! Use /logtext followed by your time entry.\n"
        "Example:\n"
        "/logtext I worked 2 hours on CI/CD for Pepsico or from 3 PM to 5 PM"
    )

# ==== /logtext ====
async def logtext(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_input = " ".join(context.args)
        if not user_input:
            await update.message.reply_text("❗ Please enter text after /logtext")
            return

        parsed = json.loads(parse_with_gpt(user_input))
        project_name = parsed.get("project_name", "").lower()
        activity_name = parsed.get("activity_name", "").lower()
        description = parsed.get("description", "Work logged via GPT")

        if "leave" in user_input.lower() or project_name == "leave" or activity_name == "leave":
            project_id = PROJECTS["leave"]
            activity_id = ACTIVITIES["leave"]
            duration = 8.0
            description = "Leave applied"
        else:
            project_id = PROJECTS.get(project_name)
            activity_id = ACTIVITIES.get(activity_name)
            duration = float(parsed.get("duration_hours", 1.0))

        if not project_id:
            await update.message.reply_text("❌ Could not match project name.")
            return

        if not activity_id:
            await update.message.reply_text("❌ Could not match activity name.")
            return

        entry_date = extract_date(user_input)
        if not entry_date:
            entry_date = datetime.utcnow().date()

        start = datetime.combine(entry_date, datetime.min.time())
        end = start + timedelta(hours=duration)

        payload = {
            "project": project_id,
            "activity": activity_id,
            "description": description,
            "begin": start.isoformat(),
            "end": end.isoformat()
        }

        res = requests.post(f"{KIMAI_URL}/api/timesheets", headers=HEADERS, json=payload)
        if res.status_code == 200:
            await update.message.reply_text(f"✅ Logged time: {description} for {start.strftime('%Y-%m-%d')}")
        else:
            await update.message.reply_text(f"❌ Kimai error: {res.status_code} - {res.text}")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error: {e}")

# ==== /report ====
async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        now = datetime.utcnow()
        period = context.args[0].lower() if context.args else "week"
        if period == "month":
            start = now - timedelta(days=30)
        elif period == "quarter":
            start = now - timedelta(days=90)
        else:
            start = now - timedelta(days=7)

        params = {
            "user": ADMIN_USER_ID,
            "begin": start.strftime("%Y-%m-%dT%H:%M:%S"),
            "end": now.strftime("%Y-%m-%dT%H:%M:%S")
        }

        res = requests	.get(f"{KIMAI_URL}/api/timesheets", headers=HEADERS, params=params)
        if res.status_code != 200:
            await update.message.reply_text(f"❌ Failed: {res.status_code} - {res.text}")
            return

        entries = res.json()
        if not entries:
            await update.message.reply_text(f"ℹ️ No entries found in {period}.")
            return

        summary = f"📋 Admin's {period.capitalize()} Report\n"
        total_hours = 0
        for e in entries:
            hrs = round(e['duration'] / 3600, 2)
            total_hours += hrs
            proj = PROJECT_NAMES.get(e['project'], str(e['project']))
            act = ACTIVITY_NAMES.get(e.get('activity', ''), '-') if e.get('activity') else "-"
            desc = e.get('description', '-')
            summary += f"- {proj} | {act} | {hrs}h | {desc}\n"
        summary += f"\n🕒 Total: {total_hours} hours"

        await update.message.reply_text(summary)
    except Exception as e:
        await update.message.reply_text(f"⚠️ Report error: {e}")

# ==== Bot Setup ====
app = ApplicationBuilder().token(BOT_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("logtext", logtext))
app.add_handler(CommandHandler("report", report))
app.run_polling()
