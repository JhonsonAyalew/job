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
GIST_ID = os.environ.get("GIST_ID", "6de7206ca0a1010314e34e984d8dc78e")

BASE_URL = "https://geezjobs.com"
URL = "https://geezjobs.com/jobs-in-ethiopia"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# Local fallback file
LOCAL_JOBS_FILE = "posted_jobs.json"

# ====================================
# LOGGER WITH DETAILED OUTPUT
# ====================================
def log(message, level="INFO"):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    level_icon = {
        "INFO": "📌",
        "SUCCESS": "✅",
        "WARNING": "⚠️",
        "ERROR": "❌",
        "DEBUG": "🔍",
        "SCRAPE": "🕷️",
        "POST": "📤",
        "SAVE": "💾"
    }
    icon = level_icon.get(level, "📌")
    print(f"[{now}] {icon} {message}")

# ====================================
# POSTED JOBS TRACKING - SIMPLIFIED LOCAL STORAGE FIRST
# ====================================
def load_posted_jobs():
    """Load previously posted job URLs from local file"""
    log(f"Loading posted jobs from {LOCAL_JOBS_FILE}...", "DEBUG")
    
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
                        else:
                            log(f"Removing expired job: {extract_job_id(job_url)} - {timestamp}", "DEBUG")
                    except (ValueError, TypeError):
                        # If timestamp is invalid, keep the job but update timestamp later
                        valid_jobs[job_url] = timestamp
                        log(f"Keeping job with invalid timestamp: {extract_job_id(job_url)}", "WARNING")
                
                log(f"Loaded {len(valid_jobs)} jobs from local file", "SUCCESS")
                return valid_jobs
        except Exception as e:
            log(f"Error loading local jobs: {str(e)}", "ERROR")
    else:
        log(f"Local file {LOCAL_JOBS_FILE} not found, starting fresh", "INFO")
    
    return {}

def save_posted_jobs(posted_jobs):
    """Save all posted jobs to local file"""
    try:
        with open(LOCAL_JOBS_FILE, 'w', encoding='utf-8') as f:
            json.dump(posted_jobs, f, indent=2, ensure_ascii=False)
        log(f"Saved {len(posted_jobs)} jobs to local file", "SAVE")
    except Exception as e:
        log(f"Error saving local jobs: {str(e)}", "ERROR")

def save_posted_job(job_url):
    """Save a single posted job URL with timestamp"""
    posted_jobs = load_posted_jobs()
    posted_jobs[job_url] = datetime.now().isoformat()
    save_posted_jobs(posted_jobs)
    job_id = extract_job_id(job_url)
    log(f"Saved job: {job_id} - {job_url}", "SAVE")

def is_job_posted(job_url):
    """Check if job has been posted before using URL"""
    posted_jobs = load_posted_jobs()
    job_id = extract_job_id(job_url)
    
    if job_url in posted_jobs:
        log(f"Job {job_id} already posted on {posted_jobs[job_url]}", "DEBUG")
        return True
    
    log(f"Job {job_id} is new", "DEBUG")
    return False

# ====================================
# HELPER FUNCTION
# ====================================
def clean_text(text):
    return ' '.join(text.split()) if text else ""

def extract_job_id(url):
    match = re.search(r'/(\d+)', url)
    return f"#{match.group(1)}" if match else "#unknown"

def format_deadline(date_text):
    if date_text and date_text not in ["N/A", "Apply Now"]:
        return f"⏰ {date_text}"
    return "⚡ ፈጣን ማመልከቻ"

# ====================================
# SCRAPE JOB DETAIL WITH ENHANCED LOGGING
# ====================================
def scrape_job_detail(job_url):
    job_id = extract_job_id(job_url)
    log(f"Starting to scrape job {job_id}: {job_url}", "SCRAPE")
    start_time = time.time()
    
    try:
        # Add retry logic
        for attempt in range(3):
            try:
                log(f"Attempt {attempt + 1}/3 for job {job_id}", "DEBUG")
                response = requests.get(job_url, headers=HEADERS, timeout=15)
                
                if response.status_code == 200:
                    log(f"Successfully fetched job {job_id} (Status: {response.status_code})", "DEBUG")
                    break
                else:
                    log(f"Failed to fetch job {job_id} (Status: {response.status_code})", "WARNING")
                    
            except requests.exceptions.Timeout:
                log(f"Timeout on attempt {attempt + 1} for job {job_id}", "WARNING")
            except requests.exceptions.ConnectionError:
                log(f"Connection error on attempt {attempt + 1} for job {job_id}", "WARNING")
            except Exception as e:
                log(f"Error on attempt {attempt + 1} for job {job_id}: {str(e)}", "WARNING")
            
            if attempt < 2:  # Don't sleep on last attempt
                time.sleep(2)
        else:
            # All attempts failed
            log(f"All 3 attempts failed for job {job_id}", "ERROR")
            return None

        soup = BeautifulSoup(response.text, "html.parser")
        log(f"Parsed HTML for job {job_id} (Length: {len(response.text)} chars)", "DEBUG")

        # ============ TITLE ============
        title_tag = soup.find("h1", id="jobTitle")
        title = title_tag.get_text(strip=True) if title_tag else "N/A"
        log(f"Found title for job {job_id}: {title[:50]}...", "DEBUG")

        # ============ BASIC INFO ============
        job_type = "N/A"
        location = "N/A"
        deadline = "N/A"

        info_elements = soup.find_all("h5")
        log(f"Found {len(info_elements)} h5 elements for job {job_id}", "DEBUG")
        
        for h5 in info_elements:
            strong = h5.find("strong")
            if not strong:
                continue
            label = strong.get_text(strip=True)

            if "Employment:" in label:
                job_type = h5.get_text(" ", strip=True).replace("Employment:", "").strip()
                log(f"Found job type for {job_id}: {job_type}", "DEBUG")
            elif "Place of Work:" in label:
                location = h5.get_text(" ", strip=True).replace("Place of Work:", "").strip()
                log(f"Found location for {job_id}: {location}", "DEBUG")
            elif "Deadline:" in label:
                deadline = h5.get_text(" ", strip=True).replace("Deadline:", "").strip()
                log(f"Found deadline for {job_id}: {deadline}", "DEBUG")

        # ============ JOB DESCRIPTION ============
        all_sections = []
        job_content = soup.find("div", class_="job-description") or soup.find("article") or soup.find("main")
        
        if job_content:
            log(f"Found job content container for {job_id}", "DEBUG")
            
            # Try to find sections by looking for headers followed by content
            current_section = None
            current_content = []
            
            # Look for pattern: header (h2/h3/h4/h5/b/strong) followed by paragraphs/lists
            elements = job_content.find_all(["p"])
            log(f"Found {len(elements)} elements in job content for {job_id}", "DEBUG")
            
            for element in elements:
                if element.name in ["h2", "h3", "h4", "h5", "b", "strong"]:
                    # This is a header
                    header_text = element.get_text(strip=True)
                    if header_text and len(header_text) < 100:  # Valid header
                        if current_section and current_content:
                            section_text = "\n".join(current_content)
                            if section_text:
                                if len(section_text) > 200:
                                    section_text = section_text[:200] + "..."
                                all_sections.append(f"<b>{current_section}</b>\n{section_text}")
                                log(f"Added section '{current_section}' with {len(current_content)} items for {job_id}", "DEBUG")
                        
                        current_section = header_text
                        current_content = []
                        log(f"Found section header: {header_text} for {job_id}", "DEBUG")
                
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
                if section_text:
                    if len(section_text) > 200:
                        section_text = section_text[:200] + "..."
                    all_sections.append(f"<b>{current_section}</b>\n{section_text}")
                    log(f"Added final section '{current_section}' with {len(current_content)} items for {job_id}", "DEBUG")
        
        # If no sections found, get paragraphs
        if not all_sections and job_content:
            log(f"No sections found for {job_id}, falling back to paragraphs", "DEBUG")
            paragraphs = []
            for p in job_content.find_all("p"):
                text = p.get_text(" ", strip=True)
                if text and len(text) > 20 and "how to apply" not in text.lower():
                    paragraphs.append(text[:200])
            
            if paragraphs:
                fallback_text = "\n".join(paragraphs[:5])
                if len(fallback_text) > 500:
                    fallback_text = fallback_text[:500] + "..."
                all_sections.append("<b>Job Description</b>\n" + fallback_text)
                log(f"Added {len(paragraphs)} paragraphs as fallback for {job_id}", "DEBUG")
        
        if all_sections:
            full_description = "\n\n━━━━━━━━━━━━━━━━━━━━━━\n\n".join(all_sections)
            log(f"Successfully built description with {len(all_sections)} sections for {job_id}", "DEBUG")
        else:
            full_description = "ዝርዝር መረጃ ለማግኘት ማስፈንጠሪያውን ይጫኑ"
            log(f"No description found for {job_id}, using default", "WARNING")
        
        elapsed_time = time.time() - start_time
        log(f"✔ Finished scraping {job_id}: {title[:30]}... (took {elapsed_time:.2f}s)", "SUCCESS")

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
        elapsed_time = time.time() - start_time
        log(f"❌ Error scraping {job_id} after {elapsed_time:.2f}s: {str(e)}", "ERROR")
        log(f"Traceback: {traceback.format_exc()}", "DEBUG")
        return None

# ====================================
# SCRAPE JOBS (Multi-threaded with duplicate check) - ENHANCED LOGGING
# ====================================
def scrape_new_jobs():
    log("🚀 Starting new jobs scrape...", "INFO")
    start_time = time.time()

    try:
        log(f"Fetching main jobs page: {URL}", "DEBUG")
        response = requests.get(URL, headers=HEADERS, timeout=15)
        log(f"Main page response: Status {response.status_code}, Size: {len(response.text)} chars", "DEBUG")
        
        soup = BeautifulSoup(response.text, "html.parser")

        # Find all job links
        job_links = []
        link_elements = soup.find_all("a", class_="color-green")
        log(f"Found {len(link_elements)} link elements with class 'color-green'", "DEBUG")
        
        for a in link_elements:
            href = a.get("href")
            if href:
                if href.startswith("http"):
                    job_links.append(href)
                else:
                    job_links.append(BASE_URL + href)

        log(f"🔎 Found {len(job_links)} total job links", "INFO")

        # Show first 5 job links for debugging
        for i, link in enumerate(job_links[:5]):
            log(f"Sample job {i+1}: {extract_job_id(link)} - {link}", "DEBUG")

        # Filter out already posted jobs
        new_job_links = []
        skipped_count = 0
        
        for link in job_links[:15]:  # Limit to 15 jobs per cycle
            if not is_job_posted(link):
                new_job_links.append(link)
                log(f"New job: {extract_job_id(link)}", "DEBUG")
            else:
                skipped_count += 1
                job_id = extract_job_id(link)
                log(f"⏭ Skipping already posted job {job_id}", "DEBUG")

        log(f"⏭ Skipped {skipped_count} already posted jobs", "INFO")
        log(f"🆕 Found {len(new_job_links)} new jobs to post", "INFO")

        if not new_job_links:
            log("📭 No new jobs found", "INFO")
            return []

        # Limit to 10 jobs maximum
        jobs_to_scrape = new_job_links[:10]
        log(f"Will scrape {len(jobs_to_scrape)} jobs (limited to 10)", "INFO")

        jobs = []
        failed_urls = []
        
        with ThreadPoolExecutor(max_workers=5) as executor:
            log(f"Starting thread pool with 5 workers", "DEBUG")
            
            # Create a dictionary mapping futures to URLs
            future_to_url = {
                executor.submit(scrape_job_detail, link): link 
                for link in jobs_to_scrape
            }
            
            log(f"Submitted {len(future_to_url)} jobs to thread pool", "DEBUG")
            
            completed = 0
            for future in as_completed(future_to_url):
                completed += 1
                url = future_to_url[future]
                job_id = extract_job_id(url)
                
                try:
                    log(f"Processing result {completed}/{len(jobs_to_scrape)} for {job_id}", "DEBUG")
                    result = future.result(timeout=30)
                    
                    if result:
                        jobs.append(result)
                        log(f"✅ Successfully scraped {job_id}: {result['title'][:30]}...", "SUCCESS")
                    else:
                        failed_urls.append(url)
                        log(f"❌ Failed to scrape {job_id}", "ERROR")
                        
                except Exception as e:
                    failed_urls.append(url)
                    log(f"❌ Exception scraping {job_id}: {str(e)}", "ERROR")
                    log(f"Traceback: {traceback.format_exc()}", "DEBUG")

        elapsed_time = time.time() - start_time
        log(f"🎉 Scraping completed in {elapsed_time:.2f}s", "INFO")
        log(f"✅ Successfully scraped: {len(jobs)} jobs", "SUCCESS")
        
        if failed_urls:
            log(f"⚠️ Failed to scrape: {len(failed_urls)} jobs", "WARNING")
            for url in failed_urls:
                log(f"  - {extract_job_id(url)}: {url}", "WARNING")
        
        return jobs

    except Exception as e:
        elapsed_time = time.time() - start_time
        log(f"❌ Error fetching job list after {elapsed_time:.2f}s: {str(e)}", "ERROR")
        log(f"Traceback: {traceback.format_exc()}", "DEBUG")
        return []

# ====================================
# TELEGRAM POST FUNCTION WITH ENHANCED LOGGING
# ====================================
async def post_job(bot, job, index, total):
    try:
        log(f"📤 [{index}/{total}] Preparing to post: {job['title'][:50]}... ({job['id']})", "POST")
        
        deadline_formatted = format_deadline(job['deadline'])
        
        # Log message details
        log(f"Message details for {job['id']}:", "DEBUG")
        log(f"  - Title: {job['title']}", "DEBUG")
        log(f"  - Type: {job['type']}", "DEBUG")
        log(f"  - Location: {job['location']}", "DEBUG")
        log(f"  - Deadline: {deadline_formatted}", "DEBUG")
        log(f"  - Description length: {len(job['detail'])} chars", "DEBUG")
        
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

        log(f"Sending to channel {CHANNEL_ID}...", "DEBUG")
        sent_message = await bot.send_message(
            chat_id=CHANNEL_ID,
            text=message,
            parse_mode="HTML",
            reply_markup=keyboard,
            disable_web_page_preview=False
        )
        
        log(f"Message sent successfully! Message ID: {sent_message.message_id}", "SUCCESS")
        
        # Save the posted job
        save_posted_job(job['link'])
        
        log(f"✅ Posted [{index}/{total}]: {job['title'][:50]}...", "SUCCESS")
        return True
        
    except TelegramError as e:
        log(f"❌ Telegram error for {job['id']}: {str(e)}", "ERROR")
        log(f"Telegram error details: {traceback.format_exc()}", "DEBUG")
        return False
    except Exception as e:
        log(f"❌ Error posting {job['id']}: {str(e)}", "ERROR")
        log(f"Error details: {traceback.format_exc()}", "DEBUG")
        return False

# ====================================
# JOB POSTING CYCLE WITH ENHANCED LOGGING
# ====================================
async def job_posting_cycle(bot):
    """One complete cycle of scraping and posting"""
    cycle_start = time.time()
    
    print("\n" + "═"*70)
    print(f"     📊 አዲስ የስራ ማስታወቂያ ዑደት / NEW JOB POSTING CYCLE")
    print(f"     {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("═"*70)
    
    log("📡 Fetching job listings...", "INFO")
    new_jobs = scrape_new_jobs()
    
    if not new_jobs:
        log("📭 No new jobs found", "INFO")
        elapsed = time.time() - cycle_start
        print("═"*70)
        print(f"     Cycle completed in {elapsed:.2f}s - No jobs posted")
        print("═"*70 + "\n")
        return
    
    print("\n" + "═"*70)
    print(f"     🚀 Posting {len(new_jobs)} new jobs to Telegram...")
    print("═"*70 + "\n")
    
    posted_count = 0
    failed_count = 0
    
    for index, job in enumerate(new_jobs, 1):
        job_start_time = time.time()
        
        log(f"📤 [{index}/{len(new_jobs)}] Processing job: {job['id']} - {job['title'][:50]}...", "POST")
        success = await post_job(bot, job, index, len(new_jobs))
        
        if success:
            posted_count += 1
            job_elapsed = time.time() - job_start_time
            log(f"✅ Job {index} posted successfully in {job_elapsed:.2f}s", "SUCCESS")
        else:
            failed_count += 1
            job_elapsed = time.time() - job_start_time
            log(f"❌ Job {index} failed after {job_elapsed:.2f}s", "ERROR")
        
        if index < len(new_jobs):
            log(f"⏳ Waiting {DELAY_BETWEEN_POSTS} seconds before next job...", "INFO")
            await asyncio.sleep(DELAY_BETWEEN_POSTS)
    
    total_elapsed = time.time() - cycle_start
    
    print("\n" + "═"*70)
    print(f"     📊 CYCLE SUMMARY")
    print("═"*70)
    print(f"     ✅ Successfully posted: {posted_count}/{len(new_jobs)}")
    print(f"     ❌ Failed: {failed_count}/{len(new_jobs)}")
    print(f"     ⏱️  Total time: {total_elapsed:.2f} seconds")
    print(f"     📁 Storage file: {LOCAL_JOBS_FILE}")
    print("═"*70 + "\n")

# ====================================
# MAIN - CRON VERSION WITH DEBUGGING
# ====================================
async def main():
    print("""
    ╔══════════════════════════════════════════════════════════╗
    ║     🇪🇹 የኢትዮጵያ ስራዎች - ክሮን ስሪት                         ║
    ║       ETHIOPIAN JOBS BOT - CRON VERSION                 ║
    ║       Enhanced Logging & 10 Jobs Support                ║
    ╚══════════════════════════════════════════════════════════╝
    """)
    
    main_start = time.time()
    
    # ============ DEBUG ENVIRONMENT VARIABLES ============
    log("🔍 DEBUG: Checking environment variables...", "DEBUG")
    log(f"🔍 DEBUG: BOT_TOKEN exists: {bool(TOKEN)}", "DEBUG")
    log(f"🔍 DEBUG: CHANNEL_ID: {CHANNEL_ID}", "DEBUG")
    log(f"🔍 DEBUG: GIST_TOKEN exists: {bool(GIST_TOKEN)}", "DEBUG")
    log(f"🔍 DEBUG: GIST_ID: {GIST_ID}", "DEBUG")
    
    # ============ CHECK BOT TOKEN ============
    if not TOKEN:
        log("❌ BOT_TOKEN environment variable not set!", "ERROR")
        return
    
    # ============ TEST BOT CONNECTION ============
    try:
        log("Testing bot connection...", "DEBUG")
        bot = Bot(token=TOKEN)
        me = await bot.get_me()
        log(f"✅ Bot connected successfully: @{me.username} (ID: {me.id})", "SUCCESS")
    except Exception as e:
        log(f"❌ Bot connection failed: {str(e)}", "ERROR")
        log(f"🔍 Traceback: {traceback.format_exc()}", "DEBUG")
        return
    
    # ============ TEST CHANNEL ACCESS ============
    try:
        log(f"Testing access to channel {CHANNEL_ID}...", "DEBUG")
        test_message = await bot.send_message(
            chat_id=CHANNEL_ID,
            text=f"🔧 Test message from cron job - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\nIf you see this, the bot can post to the channel!",
            parse_mode="HTML"
        )
        log(f"✅ Successfully sent test message to channel (Message ID: {test_message.message_id})", "SUCCESS")
    except Exception as e:
        log(f"❌ Cannot send to channel: {str(e)}", "ERROR")
        log(f"🔍 Make sure the bot is an admin in {CHANNEL_ID}", "ERROR")
        log(f"🔍 Traceback: {tracebacktraceback.format_exc().format_exc()}", "}", "DEBUG")
       DEBUG")
        return return
    
   
    
    # ============ # ============ CHECK LOCAL CHECK LOCAL FILE ACCESS ========= FILE ACCESS ============
    try===
    try:
       :
        log(" log("Testing localTesting local file system file system access... access...", "DEBUG")
", "DEBUG")
        # Test writing to local file
        with open("test_write.txt",        # Test writing to local file
        with open("test_write.txt", "w") as f:
            f "w") as f:
            f.write(".write("Test writeTest write access")
        os.remove(" access")
        os.remove("test_write.txt")
        log("✅ Local filetest_write.txt")
        log("✅ Local file system: system: Read/Write access Read/Write access confirmed", confirmed", "SU "SUCCESS")
        
CCESS")
        
               # Check # Check existing posted existing posted jobs file jobs file
        if os
        if os.path.exists.path.exists(LOC(LOCAL_AL_JOBSJOBS_FILE):
            file_FILE):
            file_size =_size = os.path os.path.getsize(LOCAL_.getsize(LOCAL_JOBSJOBS_FILE)
            log_FILE)
            log(f"📁 Existing jobs(f"📁 Existing jobs file: {LOCAL_JOBS_FILE} ({file_size} bytes)", file: {LOCAL_JOBS_FILE} ({file_size} bytes)", "INFO "INFO")
        else:
")
        else:
            log            log(f"📁(f"📁 No existing jobs file, No existing jobs file, will create will create new: {LOCAL_ new: {LOCAL_JOBS_FILE}", "INFOJOBS_FILE}",")
            
    except "INFO")
            
    except Exception as Exception as e:
        log e:
        log(f"(f"⚠️⚠️ Local file Local file system issue: { system issue: {str(estr(e)}", "W)}", "WARNING")
ARNING")
    
    log(f    
    log(f""📁 Using local file📁 Using local file for storage for storage: {LOCAL: {LOCAL_JOBS_FILE_JOBS_FILE}", "INFO")
    log(f"📋 Channel: {CHANNEL_ID}", "INFO")
    log(f"}", "INFO")
    log(f"📋 Channel: {CHANNEL_ID}", "INFO")
    log(f"⏱️ ⏱️  Delay between Delay between posts: {DEL posts: {DELAY_BAY_BETWEEN_POSTSETWEEN_POSTS} seconds} seconds", "INFO")
", "INFO")
    
       
    print("═" print("═"*70*70 + "\n")
 + "\n")
    
       
    # = # ======================= RUN ONE RUN ONE POSTING CYC POSTING CYCLE ===========LE ==
    await job===========
    await job_posting_cycle(bot_posting_cycle(bot)
    
    total)
    
    total_elapsed =_elapsed = time.time time.time() - main_start() - main_start
   
    log(f"✅ log(f"✅ Main execution Main execution completed in {total completed in {total_elapsed:.2f}s - {datetime.now().strftime_elapsed:.2f}s - {datetime.now().strftime('%Y-%m-%d %H('%Y-%m-%d %H:%M:%M:%S')}", "SU:%S')}", "SUCCESS")

if __name__CCESS")

if __name__ == "__main__":
 == "__main__":
    try    try:
       :
        asyn asyncio.runcio.run(main())
   (main())
    except Keyboard except KeyboardInterruptInterrupt:
:
        log("\n⚠️ Program        log("\n⚠️ Program stopped by stopped by user", "W user", "WARNING")
ARNING")
    except    except Exception as e:
 Exception as e:
        log(f"\        log(f"\nn❌ Fatal error: {❌ Fatal error: {str(e)}", "ERROR")
       str(e)}", "ERROR")
        traceback.print_ex traceback.print_exc()
