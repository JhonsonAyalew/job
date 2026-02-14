import asyncio
import requests
from bs4 import BeautifulSoup
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import TelegramError
from datetime import datetime, timedelta
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import traceback

# ====================================
# CONFIG
# ====================================
TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "@trytry1221")
GIST_TOKEN = os.environ.get("GIST_TOKEN")
GIST_ID = os.environ.get("GIST_ID")

BASE_URL = "https://geezjobs.com"
URL = "https://geezjobs.com/jobs-in-ethiopia"

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

DELAY_BETWEEN_POSTS = 4

# ====================================
# LOGGER
# ====================================
def log(message):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {message}")

# ====================================
# GIST STORAGE
# ====================================
def load_posted_jobs():
    if not GIST_TOKEN or not GIST_ID:
        log("❌ Missing GIST credentials")
        return {}

    try:
        response = requests.get(
            f"https://api.github.com/gists/{GIST_ID}",
            headers={"Authorization": f"token {GIST_TOKEN}"},
            timeout=15
        )

        if response.status_code != 200:
            log(f"❌ Failed loading Gist: {response.status_code}")
            return {}

        data = response.json()
        files = data.get("files", {})

        if "posted_jobs.json" not in files:
            return {}

        content = files["posted_jobs.json"]["content"]
        if not content.strip():
            return {}

        stored = json.loads(content)

        # Remove jobs older than 7 days
        valid = {}
        now = datetime.now()
        for url, ts in stored.items():
            try:
                if now - datetime.fromisoformat(ts) < timedelta(days=7):
                    valid[url] = ts
            except:
                pass

        return valid

    except Exception as e:
        log(f"❌ Gist load error: {e}")
        return {}

def save_posted_jobs(posted_jobs):
    if not GIST_TOKEN or not GIST_ID:
        return False

    try:
        response = requests.patch(
            f"https://api.github.com/gists/{GIST_ID}",
            headers={"Authorization": f"token {GIST_TOKEN}"},
            json={
                "files": {
                    "posted_jobs.json": {
                        "content": json.dumps(posted_jobs, indent=2)
                    }
                }
            },
            timeout=15
        )

        return response.status_code == 200

    except Exception as e:
        log(f"❌ Gist save error: {e}")
        return False

# ====================================
# HELPERS
# ====================================
def extract_job_id(url):
    match = re.search(r'/(\d+)', url)
    return f"#{match.group(1)}" if match else ""

def format_deadline(date_text):
    if date_text and date_text not in ["N/A", "Apply Now"]:
        return f"⏰ {date_text}"
    return "⚡ ፈጣን ማመልከቻ"

# ====================================
# SCRAPE JOB DETAIL
# ====================================
def scrape_job_detail(job_url):
    try:
        response = requests.get(job_url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(response.text, "html.parser")

        title = soup.find("h1", id="jobTitle")
        title = title.get_text(strip=True) if title else "N/A"

        job_type = "N/A"
        location = "N/A"
        deadline = "N/A"

        for h5 in soup.find_all("h5"):
            strong = h5.find("strong")
            if not strong:
                continue

            label = strong.get_text(strip=True)

            if "Employment:" in label:
                job_type = h5.get_text(" ", strip=True).replace("Employment:", "").strip()
            elif "Place of Work:" in label:
                location = h5.get_text(" ", strip=True).replace("Place of Work:", "").strip()
            elif "Deadline:" in label:
                deadline = h5.get_text(" ", strip=True).replace("Deadline:", "").strip()

        description = []
        content = soup.find("div", class_="job-description")

        if content:
            for p in content.find_all("p"):
                text = p.get_text(" ", strip=True)
                if text and len(text) > 20:
                    description.append(text)

        description = "\n\n".join(description[:5])
        if len(description.split()) > 60:
            description = " ".join(description.split()[:60]) + "..."

        return {
            "id": extract_job_id(job_url),
            "title": title,
            "type": job_type,
            "location": location,
            "deadline": deadline,
            "detail": description or "ዝርዝር መረጃ አልተገኘም",
            "link": job_url
        }

    except Exception as e:
        log(f"❌ Detail scrape error: {e}")
        return None

# ====================================
# SCRAPE JOB LIST
# ====================================
def scrape_new_jobs(posted_jobs):
    try:
        response = requests.get(URL, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(response.text, "html.parser")

        links = []
        for a in soup.find_all("a", class_="color-green"):
            href = a.get("href")
            if href:
                full = href if href.startswith("http") else BASE_URL + href
                if full not in posted_jobs:
                    links.append(full)

        links = links[:10]

        jobs = []
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(scrape_job_detail, l) for l in links]
            for f in as_completed(futures):
                result = f.result()
                if result:
                    jobs.append(result)

        return jobs

    except Exception as e:
        log(f"❌ List scrape error: {e}")
        return []

# ====================================
# POST JOB
# ====================================
async def post_job(bot, job):
    try:
        message = f"""
💼 <b>{job['title'].upper()}</b>

🏢 <b>Type:</b> {job['type']}
📍 <b>Location:</b> {job['location']}
{format_deadline(job['deadline'])}

━━━━━━━━━━━━━━━━━━
{job['detail']}
━━━━━━━━━━━━━━━━━━

📎 {job['link']}
"""

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 APPLY", url=job["link"])]
        ])

        await bot.send_message(
            chat_id=CHANNEL_ID,
            text=message,
            parse_mode="HTML",
            reply_markup=keyboard
        )

        return True

    except TelegramError as e:
        log(f"❌ Telegram error: {e}")
        return False

# ====================================
# MAIN CYCLE
# ====================================
async def main():
    log("🚀 Starting job bot...")

    if not TOKEN:
        log("❌ BOT_TOKEN missing")
        return

    bot = Bot(token=TOKEN)

    # Load posted jobs ONCE
    posted_jobs = load_posted_jobs()
    log(f"📂 Loaded {len(posted_jobs)} stored jobs")

    # Scrape new jobs
    new_jobs = scrape_new_jobs(posted_jobs)

    if not new_jobs:
        log("📭 No new jobs found")
        return

    log(f"🆕 Found {len(new_jobs)} new jobs")

    for index, job in enumerate(new_jobs, 1):
        success = await post_job(bot, job)
        if success:
            posted_jobs[job["link"]] = datetime.now().isoformat()
            log(f"✅ Posted {index}/{len(new_jobs)}")

        if index < len(new_jobs):
            await asyncio.sleep(DELAY_BETWEEN_POSTS)

    # Save updated list ONCE
    if save_posted_jobs(posted_jobs):
        log("💾 Gist updated successfully")

    log("✅ Cycle finished")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        log(f"❌ Fatal error: {e}")
        traceback.print_exc()
