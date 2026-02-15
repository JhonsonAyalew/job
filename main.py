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

# GitHub Gist Config - REQUIRED
GIST_TOKEN = os.environ.get("GIST_TOKEN")
GIST_ID = os.environ.get("GIST_ID", "6de7206ca0a1010314e34e984d8dc78e")

BASE_URL = "https://geezjobs.com"
URL = "https://geezjobs.com/jobs-in-ethiopia"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# Global cache for posted jobs to avoid repeated Gist reads
_posted_jobs_cache = None
_last_cache_update = None

# ====================================
# LOGGER
# ====================================
def log(message):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {message}")

# ====================================
# POSTED JOBS TRACKING - GITHUB GIST ONLY
# ====================================
def load_posted_jobs(force_refresh=False):
    """Load previously posted job URLs from GitHub Gist ONLY with caching"""
    global _posted_jobs_cache, _last_cache_update
    
    # Use cache if available and not forcing refresh
    if not force_refresh and _posted_jobs_cache is not None:
        if _last_cache_update and (datetime.now() - _last_cache_update) < timedelta(minutes=5):
            log(f"📋 Using cached jobs: {len(_posted_jobs_cache)} jobs")
            return _posted_jobs_cache.copy()
    
    if not GIST_TOKEN:
        log("❌ GIST_TOKEN not set! Cannot load jobs.")
        return {}
    
    if not GIST_ID:
        log("❌ GIST_ID not set! Cannot load jobs.")
        return {}
    
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
                    
                    # Update cache
                    _posted_jobs_cache = valid_jobs
                    _last_cache_update = datetime.now()
                    
                    log(f"📂 Loaded {len(valid_jobs)} jobs from GitHub Gist")
                    return valid_jobs.copy()
            else:
                log("📁 posted_jobs.json not found in Gist, starting fresh")
                _posted_jobs_cache = {}
                _last_cache_update = datetime.now()
                return {}
        else:
            log(f"⚠️ Gist load failed: {response.status_code} - {response.text[:200]}")
            return _posted_jobs_cache.copy() if _posted_jobs_cache else {}
            
    except Exception as e:
        log(f"❌ Error loading from Gist: {str(e)}")
        return _posted_jobs_cache.copy() if _posted_jobs_cache else {}

def save_posted_jobs(posted_jobs):
    """Save all posted jobs to GitHub Gist ONLY and update cache"""
    global _posted_jobs_cache, _last_cache_update
    
    if not GIST_TOKEN:
        log("❌ GIST_TOKEN not set! Cannot save jobs.")
        return False
    
    if not GIST_ID:
        log("❌ GIST_ID not set! Cannot save jobs.")
        return False
    
    try:
        gist_url = f"https://api.github.com/gists/{GIST_ID}"
        headers = {
            "Authorization": f"token {GIST_TOKEN}",
            "Accept": "application/vnd.github.v3+json"
        }
        
        # First, get current gist to preserve other files
        get_response = requests.get(gist_url, headers=headers, timeout=15)
        current_files = {}
        
        if get_response.status_code == 200:
            current_data = get_response.json()
            current_files = current_data.get("files", {})
        
        # Prepare files for update
        files_data = {}
        
        # Copy existing files (if any)
        for filename in current_files:
            if filename != "posted_jobs.json":
                files_data[filename] = {"content": current_files[filename]["content"]}
        
        # Add/update posted_jobs.json
        files_data["posted_jobs.json"] = {
            "content": json.dumps(posted_jobs, indent=2, ensure_ascii=False)
        }
        
        data = {"files": files_data}
        
        log(f"📤 Saving to Gist: {len(posted_jobs)} jobs")
        response = requests.patch(gist_url, json=data, headers=headers, timeout=15)
        
        if response.status_code == 200:
            # Update cache after successful save
            _posted_jobs_cache = posted_jobs.copy()
            _last_cache_update = datetime.now()
            
            log(f"✅ Successfully saved to GitHub Gist")
            log(f"🔗 View at: https://gist.github.com/{GIST_ID}")
            return True
        else:
            log(f"❌ Gist save failed: {response.status_code} - {response.text[:200]}")
            return False
            
    except Exception as e:
        log(f"❌ Error saving to Gist: {str(e)}")
        return False

def save_posted_job(job_url):
    """Save a single posted job URL with timestamp to Gist"""
    # Load with force refresh to get latest data
    posted_jobs = load_posted_jobs(force_refresh=True)
    
    # Check if already exists (double-check)
    if job_url in posted_jobs:
        log(f"⚠️ Job {extract_job_id(job_url)} already exists in Gist, skipping save")
        return True
    
    posted_jobs[job_url] = datetime.now().isoformat()
    success = save_posted_jobs(posted_jobs)
    if success:
        job_id = extract_job_id(job_url)
        log(f"💾 Saved job to Gist: {job_id}")
    return success

def is_job_posted(job_url):
    """Check if job has been posted before using URL"""
    # Use cache but don't force refresh for performance
    posted_jobs = load_posted_jobs(force_refresh=False)
    result = job_url in posted_jobs
    if result:
        job_id = extract_job_id(job_url)
        log(f"🔍 Job {job_id} found in posted jobs")
    return result

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
            
            for element in job_content.find_all(["p"]):
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

        # Force refresh posted jobs before checking
        posted_jobs = load_posted_jobs(force_refresh=True)
        
        # Filter out already posted jobs
        new_job_links = []
        skipped_count = 0
        for link in job_links[:15]:  # Limit to 15 jobs per cycle
            if link not in posted_jobs:
                new_job_links.append(link)
            else:
                skipped_count += 1
                job_id = extract_job_id(link)
                log(f"⏭ Skipping already posted job {job_id}")

        log(f"🆕 Found {len(new_job_links)} new jobs to post")
        log(f"⏭ Skipped {skipped_count} already posted jobs")

        if not new_job_links:
            log("📭 No new jobs found")
            return []

        jobs = []
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(scrape_job_detail, link) for link in new_job_links[:10]]  # Limit to 10
            for future in as_completed(futures):
                result = future.result()
                if result:
                    # Double-check if job was posted while we were scraping
                    if not is_job_posted(result['link']):
                        jobs.append(result)
                    else:
                        log(f"⏭ Job {result['id']} was posted while scraping, skipping")

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
        
        # Save the posted job to Gist
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
# MAIN - CRON VERSION WITH DEBUGGING
# ====================================
async def main():
    print("""
    ╔════════════════════════════════════════════╗
    ║     🇪🇹 የኢትዮጵያ ስራዎች - ክሮን ስሪት         ║
    ║       ETHIOPIAN JOBS - CRON VERSION       ║
    ╚════════════════════════════════════════════╝
    """)
    
    # ============ DEBUG ENVIRONMENT VARIABLES ============
    log("🔍 DEBUG: Checking environment variables...")
    log(f"🔍 DEBUG: BOT_TOKEN exists: {bool(TOKEN)}")
    log(f"🔍 DEBUG: CHANNEL_ID: {CHANNEL_ID}")
    log(f"🔍 DEBUG: GIST_TOKEN exists: {bool(GIST_TOKEN)}")
    log(f"🔍 DEBUG: GIST_ID: {GIST_ID}")
    
    # ============ CHECK REQUIRED TOKENS ============
    if not TOKEN:
        log("❌ BOT_TOKEN environment variable not set!")
        return
    
    if not GIST_TOKEN:
        log("❌ GIST_TOKEN environment variable not set! Gist storage required.")
        return
    
    if not GIST_ID:
        log("❌ GIST_ID environment variable not set! Gist storage required.")
        return
    
    # ============ TEST GITHUB GIST ACCESS ============
    try:
        # First, load existing jobs to see what's there
        existing_jobs = load_posted_jobs(force_refresh=True)
        log(f"📊 Current jobs in Gist: {len(existing_jobs)}")
        
        # Show a sample of existing jobs
        if existing_jobs:
            sample_jobs = list(existing_jobs.items())[:3]
            log("📋 Sample of existing jobs:")
            for url, timestamp in sample_jobs:
                job_id = extract_job_id(url)
                log(f"   {job_id}: {timestamp}")
        
        log("✅ GitHub Gist access confirmed")
    except Exception as e:
        log(f"❌ GitHub Gist access failed: {str(e)}")
        log("🔍 Make sure your GIST_TOKEN has 'gist' scope and GIST_ID is correct")
        return
    
    # ============ TEST BOT CONNECTION ============
    try:
        bot = Bot(token=TOKEN)
        me = await bot.get_me()
        log(f"✅ Bot connected successfully: @{me.username} (ID: {me.id})")
    except Exception as e:
        log(f"❌ Bot connection failed: {str(e)}")
        log(f"🔍 Traceback: {traceback.format_exc()}")
        return
    
    # ============ TEST CHANNEL ACCESS ============
    try:
        test_message = await bot.send_message(
            chat_id=CHANNEL_ID,
            text=f"🔧 Test message from cron job - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\nIf you see this, the bot can post to the channel!\n\n📁 Using Gist storage: {GIST_ID}\n📊 Jobs in database: {len(existing_jobs) if 'existing_jobs' in locals() else 'unknown'}",
            parse_mode="HTML"
        )
        log(f"✅ Successfully sent test message to channel (Message ID: {test_message.message_id})")
    except Exception as e:
        log(f"❌ Cannot send to channel: {str(e)}")
        log(f"🔍 Make sure the bot is an admin in {CHANNEL_ID}")
        log(f"🔍 Traceback: {traceback.format_exc()}")
        return
    
    log(f"📋 Channel: {CHANNEL_ID}")
    log(f"📁 Using GitHub Gist for storage: https://gist.github.com/{GIST_ID}")
    
    print("═"*60 + "\n")
    
    # ============ RUN ONE POSTING CYCLE ============
    await job_posting_cycle(bot)
    
    # ============ VERIFY AFTER POSTING ============
    final_jobs = load_posted_jobs(force_refresh=True)
    log(f"📊 Final jobs in Gist after cycle: {len(final_jobs)}")
    
    log(f"✅ Cycle completed - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("\n⚠️ Program stopped by user")
    except Exception as e:
        log(f"\n❌ Fatal error: {str(e)}")
        traceback.print_exc()
