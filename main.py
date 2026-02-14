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
from urllib.parse import quote
import time
import traceback

# ====================================
# CONFIG - ENVIRONMENT VARIABLES ONLY
# ====================================
TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "@trytry1221")
DELAY_BETWEEN_POSTS = 4

# GitHub Gist Config
GIST_TOKEN = os.environ.get("GIST_TOKEN")
GIST_ID = os.environ.get("GIST_ID", "6de7206ca0a1010314e34e984d8dc78e")  # Your Gist ID

BASE_URL = "https://geezjobs.com"
URL = "https://geezjobs.com/jobs-in-ethiopia"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# Local fallback file
LOCAL_JOBS_FILE = "posted_jobs.json"

# ====================================
# LOGGER
# ====================================
def log(message):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {message}")

# ====================================
# POSTED JOBS TRACKING - GITHUB GIST STORAGE
# ====================================
def load_posted_jobs():
    """Load previously posted job URLs from GitHub Gist (with local fallback)"""
    posted_jobs = {}
    
    # Try to load from Gist first
    if GIST_TOKEN and GIST_ID:
        try:
            gist_url = f"https://api.github.com/gists/{GIST_ID}"
            headers = {
                "Authorization": f"token {GIST_TOKEN}",
                "Accept": "application/vnd.github.v3+json"
            }
            
            log(f"📡 Loading from Gist: {gist_url}")
            response = requests.get(gist_url, headers=headers, timeout=15)
            
            if response.status_code == 200:
                gist_data = response.json()
                
                # Check if file exists in gist
                if "files" in gist_data and "posted_jobs.json" in gist_data["files"]:
                    content = gist_data["files"]["posted_jobs.json"]["content"]
                    if content.strip():
                        data = json.loads(content)
                        
                        # Clean jobs older than 7 days
                        current_time = datetime.now()
                        valid_jobs = {}
                        for job_url, timestamp in data.items():
                            try:
                                job_time = datetime.fromisoformat(timestamp)
                                if current_time - job_time < timedelta(days=7):
                                    valid_jobs[job_url] = timestamp
                            except (ValueError, TypeError):
                                # If timestamp is invalid, keep the job but update timestamp later
                                valid_jobs[job_url] = timestamp
                        
                        log(f"📂 Loaded {len(valid_jobs)} jobs from GitHub Gist")
                        return valid_jobs
                else:
                    log("📁 posted_jobs.json not found in Gist, will create new")
            else:
                log(f"⚠️ Gist load failed: {response.status_code} - {response.text[:200]}")
                
        except Exception as e:
            log(f"⚠️ Error loading from Gist: {str(e)}")
    
    # Fallback to local file
    return load_local_posted_jobs()

def save_posted_jobs(posted_jobs):
    """Save all posted jobs to GitHub Gist and local file"""
    # Always save locally first
    save_local_posted_jobs(posted_jobs)
    
    # Try to save to Gist
    if GIST_TOKEN and GIST_ID:
        try:
            gist_url = f"https://api.github.com/gists/{GIST_ID}"
            headers = {
                "Authorization": f"token {GIST_TOKEN}",
                "Accept": "application/vnd.github.v3+json"
            }
            
            data = {
                "files": {
                    "posted_jobs.json": {
                        "content": json.dumps(posted_jobs, indent=2, ensure_ascii=False)
                    }
                }
            }
            
            log(f"📤 Saving to Gist: {len(posted_jobs)} jobs")
            response = requests.patch(gist_url, json=data, headers=headers, timeout=15)
            
            if response.status_code == 200:
                log(f"✅ Successfully saved to GitHub Gist")
            else:
                log(f"⚠️ Gist save failed: {response.status_code} - {response.text[:200]}")
                
        except Exception as e:
            log(f"⚠️ Error saving to Gist: {str(e)}")

def save_posted_job(job_url):
    """Save a single posted job URL with timestamp"""
    posted_jobs = load_posted_jobs()
    posted_jobs[job_url] = datetime.now().isoformat()
    save_posted_jobs(posted_jobs)
    log(f"💾 Saved job: {extract_job_id(job_url)}")

def load_local_posted_jobs():
    """Fallback: load from local file"""
    if os.path.exists(LOCAL_JOBS_FILE):
        try:
            with open(LOCAL_JOBS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # Clean jobs older than 7 days
                current_time = datetime.now()
                valid_jobs = {}
                for job_url, timestamp in data.items():
                    try:
                        job_time = datetime.fromisoformat(timestamp)
                        if current_time - job_time < timedelta(days=7):
                            valid_jobs[job_url] = timestamp
                    except (ValueError, TypeError):
                        valid_jobs[job_url] = timestamp
                log(f"📂 Loaded {len(valid_jobs)} jobs from local file")
                return valid_jobs
        except Exception as e:
            log(f"⚠️ Error loading local jobs: {str(e)}")
    return {}

def save_local_posted_jobs(posted_jobs):
    """Save to local file"""
    try:
        with open(LOCAL_JOBS_FILE, 'w', encoding='utf-8') as f:
            json.dump(posted_jobs, f, indent=2, ensure_ascii=False)
        log(f"💾 Saved {len(posted_jobs)} jobs to local file")
    except Exception as e:
        log(f"❌ Error saving local jobs: {str(e)}")

def is_job_posted(job_url):
    """Check if job has been posted before using URL"""
    posted_jobs = load_posted_jobs()
    return job_url in posted_jobs

# ====================================
# HELPER FUNCTION
# ====================================
def clean_text(text):
    return ' '.join(text.split()) if text else ""

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
        log(f"➡ Visiting: {job_url}")

        response = requests.get(job_url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(response.text, "html.parser")

        # ============ TITLE ============
        title_tag = soup.find("h1", id="jobTitle")
        title = title_tag.get_text(strip=True) if title_tag else "N/A"

        # ============ BASIC INFO ============
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

        # ============ JOB DESCRIPTION ============
        all_sections = []
        job_content = soup.find("div", class_="job-description") or soup.find("article") or soup.find("main")
        
        if job_content:
            current_section = None
            current_content = []
            
            for element in job_content.find_all(["p", "li", "ul", "h2", "h3", "h4", "h5"]):
                if element.name in ["h2", "h3", "h4", "h5"]:
                    header_text = element.get_text(strip=True)
                    
                    if current_section and current_content:
                        section_text = "\n".join(current_content)
                        words = section_text.split()
                        if len(words) > 20:
                            section_text = ' '.join(words[:20]) + "..."
                        all_sections.append(f"<b>{current_section}</b>\n{section_text}")
                    
                    current_section = header_text
                    current_content = []
                
                elif element.name in ["p", "li"] and current_section:
                    text = element.get_text(" ", strip=True)
                    if text and len(text) > 5:
                        if element.name == "li":
                            text = f"• {text}"
                        current_content.append(text)
                
                elif element.name == "ul" and current_section:
                    for li in element.find_all("li"):
                        text = li.get_text(" ", strip=True)
                        if text:
                            current_content.append(f"• {text}")
            
            # Add the last section
            if current_section and current_content:
                section_text = "\n".join(current_content)
                words = section_text.split()
                if len(words) > 20:
                    section_text = ' '.join(words[:20]) + "..."
                all_sections.append(f"<b>{current_section}</b>\n{section_text}")
        
        # If no sections found, get paragraphs
        if not all_sections and job_content:
            paragraphs = []
            for p in job_content.find_all("p"):
                text = p.get_text(" ", strip=True)
                if text and len(text) > 20 and "how to apply" not in text.lower():
                    paragraphs.append(text[:200])
            
            if paragraphs:
                fallback_text = "\n".join(paragraphs[:5])
                words = fallback_text.split()
                if len(words) > 20:
                    fallback_text = ' '.join(words[:20]) + "..."
                all_sections.append("<b>Job Description</b>\n" + fallback_text)
        
        if all_sections:
            full_description = "\n\n━━━━━━━━━━━━━━━━━━━━━━\n\n".join(all_sections)
        else:
            full_description = "ዝርዝር መረጃ አልተገኘም"
        
        job_id = extract_job_id(job_url)
        log(f"✔ Finished: {title[:30]}... - ID: {job_id}")

        return {
            "id": job_id,
            "title": title,
            "type": job_type,
            "location": location,
            "deadline": deadline,
            "detail": full_description,
            "link": job_url
        }

    except Exception as e:
        log(f"❌ Error scraping {job_url}: {str(e)}")
        return None

# ====================================
# SCRAPE JOBS (Multi-threaded with duplicate check)
# ====================================
def scrape_new_jobs():
    log("🚀 Starting new jobs scrape...")

    try:
        response = requests.get(URL, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(response.text, "html.parser")

        job_links = []
        for a in soup.find_all("a", class_="color-green"):
            href = a.get("href")
            if href:
                if href.startswith("http"):
                    job_links.append(href)
                else:
                    job_links.append(BASE_URL + href)

        log(f"🔎 Found {len(job_links)} total jobs")

        # Filter out already posted jobs
        new_job_links = []
        for link in job_links[:15]:  # Limit to 15 jobs per cycle
            if not is_job_posted(link):
                new_job_links.append(link)
            else:
                job_id = extract_job_id(link)
                log(f"⏭ Skipping already posted job {job_id}")

        log(f"🆕 Found {len(new_job_links)} new jobs to post")

        if not new_job_links:
            log("📭 No new jobs found")
            return []

        jobs = []
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(scrape_job_detail, link) for link in new_job_links]
            for future in as_completed(futures):
                result = future.result()
                if result:
                    jobs.append(result)

        log(f"🎉 Scraped {len(jobs)} new jobs successfully")
        return jobs

    except Exception as e:
        log(f"❌ Error fetching job list: {str(e)}")
        return []

# ====================================
# TELEGRAM POST FUNCTION
# ====================================
async def post_job(bot, job):
    try:
        deadline_formatted = format_deadline(job['deadline'])
        
        message = f"""
💼  የኢትዮጵያ የስራ ማስታወቂያ  💼
     
━━━━━━━━━━━━━━━━━━━━━━
<b>{job['title'].upper()}</b>
━━━━━━━━━━━━━━━━━━━━━━
🏢 <b>የስራው አይነት:</b> {job['type']}
🗺 <b>የስራው ቦታ:</b> {job['location']}
⏳ <b>የማመልከቻ ማብቂያ ቀን:</b> {deadline_formatted}
━━━━━━━━━━━━━━━━━━━━━━
📎 <b>ማስፈንጠሪያ:</b> {job['link']}
━━━━━━━━━━━━━━━━━━━━━━
{job['detail']}
━━━━━━━━━━━━━━━━━━━━━━
🔔 ማሳሰቢያ: ዛሬ ያመልክቱ! ነገ አይዘገዩ!
"""

        # Create buttons
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 አመልክት / APPLY", url=job["link"])],
            [InlineKeyboardButton("📢 ሌሎች ስራዎች", url="https://t.me/trytry1221")]
        ])

        await bot.send_message(
            chat_id=CHANNEL_ID,
            text=message,
            parse_mode="HTML",
            reply_markup=keyboard,
            disable_web_page_preview=False
        )
        
        # Save the posted job
        save_posted_job(job['link'])
        
        log(f"✅ Posted: {job['title'][:50]}...")
        return True
        
    except TelegramError as e:
        log(f"❌ Telegram error: {str(e)}")
        return False
    except Exception as e:
        log(f"❌ Error posting: {str(e)}")
        return False

# ====================================
# JOB POSTING CYCLE
# ====================================
async def job_posting_cycle(bot):
    """One complete cycle of scraping and posting"""
    print("\n" + "═"*60)
    print(f"     📊 አዲስ የስራ ማስታወቂያ ዑደት")
    print(f"     {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("═"*60)
    
    log("📡 Fetching job listings...")
    new_jobs = scrape_new_jobs()
    
    if not new_jobs:
        log("📭 No new jobs found")
        print("═"*60 + "\n")
        return
    
    print("\n" + "═"*60)
    print(f"     🚀 Posting {len(new_jobs)} new jobs...")
    print("═"*60 + "\n")
    
    posted_count = 0
    for index, job in enumerate(new_jobs, 1):
        log(f"📤 [{index}/{len(new_jobs)}] Posting: {job['title'][:30]}...")
        success = await post_job(bot, job)
        if success:
            posted_count += 1
        
        if index < len(new_jobs):
            log(f"⏳ Waiting {DELAY_BETWEEN_POSTS} seconds...\n")
            await asyncio.sleep(DELAY_BETWEEN_POSTS)
    
    print("\n" + "═"*60)
    print(f"     ✅ {posted_count}/{len(new_jobs)} jobs posted successfully!")
    print("═"*60 + "\n")

# ====================================
# MAIN - CRON VERSION
# ====================================
async def main():
    # Check required environment variables
    if not TOKEN:
        log("❌ BOT_TOKEN environment variable not set!")
        return
    
    bot = Bot(token=TOKEN)
    
    print("""
    ╔════════════════════════════════════════════╗
    ║     🇪🇹 የኢትዮጵያ ስራዎች - ክሮን ስሪት         ║
    ║       ETHIOPIAN JOBS - CRON VERSION       ║
    ╚════════════════════════════════════════════╝
    """)
    
    log(f"📊 Running once (Render Cron)")
    log(f"📁 Posted jobs storage: GitHub Gist + Local")
    log(f"📋 Channel: {CHANNEL_ID}")
    
    if GIST_TOKEN and GIST_ID:
        log(f"✅ GitHub Gist storage: ACTIVE (ID: {GIST_ID})")
    else:
        log(f"⚠️ GitHub Gist storage: DISABLED (using local file only)")
    
    print("═"*60 + "\n")
    
    # Run once
    await job_posting_cycle(bot)
    
    log(f"✅ Cycle completed - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("\n⚠️ Program stopped by user")
    except Exception as e:
        log(f"\n❌ Fatal error: {str(e)}")
        traceback.print_exc()
