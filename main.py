
import asyncio
import requests
from bs4 import BeautifulSoup
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from datetime import datetime, timedelta
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import time
import traceback

# ====================================
# CONFIG
# ====================================
TOKEN = "8191854029:AAFdBYDf5wqAMXEXEubrzLfmsJubF6icm1w"
CHANNEL_ID = "@trytry1221"
DELAY_BETWEEN_POSTS = 1
SCRAPE_INTERVAL = 30  # 30 seconds

# GitHub Gist Config
GIST_TOKEN = "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"
GIST_ID = "6de7206ca0a1010314e34e984d8dc78e"

BASE_URL = "https://geezjobs.com"
URL = "https://geezjobs.com/jobs-in-ethiopia"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# ====================================
# LOGGER
# ====================================
def log(message):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {message}")

# ====================================
# POSTED JOBS TRACKING - GITHUB GIST (USING URL)
# ====================================
def load_posted_jobs():
    """Load previously posted job URLs from GitHub Gist"""
    posted_jobs = {}
    
    try:
        gist_url = f"https://api.github.com/gists/{GIST_ID}"
        headers = {
            "Authorization": f"token {GIST_TOKEN}",
            "Accept": "application/vnd.github.v3+json"
        }
        
        response = requests.get(gist_url, headers=headers, timeout=15)
        
        if response.status_code == 200:
            gist_data = response.json()
            
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
                            valid_jobs[job_url] = timestamp
                    
                    log(f"📂 Loaded {len(valid_jobs)} jobs from GitHub Gist")
                    return valid_jobs
            else:
                log("📁 posted_jobs.json not found in Gist, starting fresh")
                return {}
        else:
            log(f"⚠️ Gist load failed: {response.status_code}")
            return {}
            
    except Exception as e:
        log(f"❌ Error loading from Gist: {str(e)}")
        return {}

def save_posted_jobs(posted_jobs):
    """Save all posted jobs to GitHub Gist"""
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
        
        response = requests.patch(gist_url, json=data, headers=headers, timeout=15)
        
        if response.status_code == 200:
            log(f"✅ Saved {len(posted_jobs)} jobs to GitHub Gist")
            return True
        else:
            log(f"❌ Gist save failed: {response.status_code}")
            return False
            
    except Exception as e:
        log(f"❌ Error saving to Gist: {str(e)}")
        return False

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
                
                elif element.name == "p" and current_section:
                    text = element.get_text(" ", strip=True)
                    if text and len(text) > 5:
                        current_content.append(text)
            
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
# SCRAPE JOBS (with duplicate check using Gist)
# ====================================
def scrape_new_jobs(posted_jobs):
    """Scrape jobs and filter out already posted ones using the posted_jobs dict"""
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

        # Filter out already posted jobs using URL
        new_job_links = []
        skipped_count = 0
        for link in job_links[:15]:  # Limit to 15 jobs per cycle
            if link not in posted_jobs:  # Check using the dict we loaded
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
            futures = [executor.submit(scrape_job_detail, link) for link in new_job_links[:10]]
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
<b>💼  የኢትዮጵያ የስራ ማስታወቂያ  / Ethiopian Jobs 💼</b>
     
━━━━━━━━━━━━━━━━━━━━━━
<b>{job['title'].upper()}</b>
━━━━━━━━━━━━━━━━━━━━━━

🏢 <b>የስራው አይነት:</b> {job['type']}

🗺 <b>የስራው ቦታ:</b> {job['location']}

⏳ <b>የማመልከቻ ማብቂያ ቀን:</b> {deadline_formatted}

📎 <b>አመልክት: </b> <a href="{job['link']}"><b>🔗 Click Here To Apply</b></a>
━━━━━━━━━━━━━━━━━━━━━━

{job['detail']}


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
        
        log(f"✅ Posted: {job['title'][:50]}...")
        return True
        
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
    
    # LOAD POSTED JOBS ONCE at the beginning of the cycle
    log("📡 Loading posted jobs from Gist...")
    posted_jobs = load_posted_jobs()
    log(f"📊 Found {len(posted_jobs)} already posted jobs in history")
    
    log("📡 Fetching new job listings...")
    new_jobs = scrape_new_jobs(posted_jobs)  # Pass the posted_jobs dict
    
    if not new_jobs:
        log("📭 No new jobs found")
        print("═"*60 + "\n")
        return
    
    print("\n" + "═"*60)
    print(f"     🚀 Posting {len(new_jobs)} new jobs...")
    print("═"*60 + "\n")
    
    posted_count = 0
    # Update posted_jobs dict as we go
    for index, job in enumerate(new_jobs, 1):
        log(f"📤 [{index}/{len(new_jobs)}] Posting: {job['title'][:30]}...")
        success = await post_job(bot, job)
        if success:
            posted_count += 1
            # Add to posted_jobs dict immediately
            posted_jobs[job['link']] = datetime.now().isoformat()
        
        if index < len(new_jobs):
            log(f"⏳ Waiting {DELAY_BETWEEN_POSTS} seconds...\n")
            await asyncio.sleep(DELAY_BETWEEN_POSTS)
    
    # SAVE ALL POSTED JOBS AT ONCE at the end of the cycle
    if posted_count > 0:
        log(f"💾 Saving {posted_count} new jobs to Gist...")
        save_posted_jobs(posted_jobs)
    
    print("\n" + "═"*60)
    print(f"     ✅ {posted_count}/{len(new_jobs)} jobs posted successfully!")
    print("═"*60 + "\n")

# ====================================
# MAIN LOOP - RUNS EVERY 30 SECONDS
# ====================================
async def main():
    bot = Bot(token=TOKEN)
    
    print("""
    ╔════════════════════════════════════════════╗
    ║     🇪🇹 የኢትዮጵያ ስራዎች - ቀጣይነት ያለው         ║
    ║       ETHIOPIAN JOBS - CONTINUOUS          ║
    ║         (Every 30 Seconds)                  ║
    ╚════════════════════════════════════════════╝
    """)
    
    # Test Gist connection first
    log("🔍 Testing GitHub Gist connection...")
    test_jobs = load_posted_jobs()
    if isinstance(test_jobs, dict):
        log(f"✅ GitHub Gist connected successfully! Found {len(test_jobs)} jobs in history")
    else:
        log("⚠️ GitHub Gist connection issue, but continuing...")
    
    log(f"📋 Channel: {CHANNEL_ID}")
    log(f"📁 GitHub Gist ID: {GIST_ID}")
    log(f"⏱️  Checking every {SCRAPE_INTERVAL} seconds")
    print("═"*60 + "\n")
    
    # Run forever
    cycle_count = 0
    while True:
        cycle_count += 1
        print(f"\n{'='*60}")
        print(f"🔄 CYCLE #{cycle_count} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*60}")
        
        try:
            await job_posting_cycle(bot)
        except Exception as e:
            log(f"❌ Error in cycle: {str(e)}")
            traceback.print_exc()
        
        log(f"💤 Waiting {SCRAPE_INTERVAL} seconds until next check...")
        print(f"{'='*60}\n")
        await asyncio.sleep(SCRAPE_INTERVAL)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n⚠️ Program stopped by user")
    except Exception as e:
        print(f"\n❌ Fatal error: {str(e)}")
        traceback.print_exc()
