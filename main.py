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
_cache_lock = asyncio.Lock()

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
    global _posted_jobs_cache
    
    # For force_refresh, always fetch from Gist
    if not force_refresh and _posted_jobs_cache is not None:
        log(f"📋 Using cache: {len(_posted_jobs_cache)} jobs")
        return _posted_jobs_cache.copy()
    
    if not GIST_TOKEN or not GIST_ID:
        log("❌ GIST_TOKEN or GIST_ID not set!")
        return {}
    
    try:
        gist_url = f"https://api.github.com/gists/{GIST_ID}"
        headers = {
            "Authorization": f"token {GIST_TOKEN}",
            "Accept": "application/vnd.github.v3+json"
        }
        
        log(f"📡 Loading from Gist...")
        response = requests.get(gist_url, headers=headers, timeout=15)
        
        if response.status_code == 200:
            gist_data = response.json()
            
            if "files" in gist_data and "posted_jobs.json" in gist_data["files"]:
                content = gist_data["files"]["posted_jobs.json"]["content"]
                if content and content.strip():
                    data = json.loads(content)
                    
                    # Clean jobs older than 7 days
                    current_time = datetime.now()
                    valid_jobs = {}
                    removed_count = 0
                    
                    for job_url, timestamp in data.items():
                        try:
                            # Handle both ISO format and simple strings
                            if isinstance(timestamp, str):
                                try:
                                    job_time = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                                except ValueError:
                                    job_time = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
                            else:
                                job_time = datetime.now() - timedelta(days=8)
                            
                            if current_time - job_time < timedelta(days=7):
                                valid_jobs[job_url] = timestamp
                            else:
                                removed_count += 1
                        except (ValueError, TypeError, AttributeError):
                            # Keep if timestamp is unparseable
                            valid_jobs[job_url] = timestamp
                    
                    if removed_count > 0:
                        log(f"🧹 Removed {removed_count} old jobs (>7 days)")
                    
                    _posted_jobs_cache = valid_jobs
                    log(f"📂 Loaded {len(valid_jobs)} jobs from Gist")
                    return valid_jobs.copy()
            
            log("📁 Empty or new Gist, starting fresh")
            _posted_jobs_cache = {}
            return {}
        else:
            log(f"⚠️ Gist load failed: {response.status_code}")
            return {} if _posted_jobs_cache is None else _posted_jobs_cache.copy()
            
    except Exception as e:
        log(f"❌ Error loading Gist: {str(e)}")
        return {} if _posted_jobs_cache is None else _posted_jobs_cache.copy()

def save_posted_jobs(posted_jobs):
    """Save all posted jobs to GitHub Gist and update cache"""
    global _posted_jobs_cache
    
    if not GIST_TOKEN or not GIST_ID:
        log("❌ GIST_TOKEN or GIST_ID not set!")
        return False
    
    try:
        gist_url = f"https://api.github.com/gists/{GIST_ID}"
        headers = {
            "Authorization": f"token {GIST_TOKEN}",
            "Accept": "application/vnd.github.v3+json"
        }
        
        # Get current gist to preserve other files
        get_response = requests.get(gist_url, headers=headers, timeout=15)
        current_files = {}
        
        if get_response.status_code == 200:
            current_data = get_response.json()
            current_files = current_data.get("files", {})
        
        # Prepare files (preserve non-job files)
        files_data = {}
        for filename, file_info in current_files.items():
            if filename != "posted_jobs.json":
                files_data[filename] = {"content": file_info.get("content", "")}
        
        # Sort jobs by timestamp (newest first) for readability
        sorted_jobs = dict(sorted(
            posted_jobs.items(),
            key=lambda x: str(x[1]),
            reverse=True
        ))
        
        # Add/update posted_jobs.json with explicit UTF-8 encoding
        json_content = json.dumps(sorted_jobs, indent=2, ensure_ascii=False)
        files_data["posted_jobs.json"] = {"content": json_content}
        
        data = {"files": files_data}
        
        log(f"💾 Saving {len(posted_jobs)} jobs to Gist...")
        response = requests.patch(gist_url, json=data, headers=headers, timeout=15)
        
        if response.status_code == 200:
            _posted_jobs_cache = posted_jobs.copy()
            log(f"✅ Saved to Gist successfully")
            return True
        else:
            log(f"❌ Gist save failed: {response.status_code}")
            log(f"   Response: {response.text[:300]}")
            return False
            
    except Exception as e:
        log(f"❌ Error saving Gist: {str(e)}")
        traceback.print_exc()
        return False

def save_posted_job(job_url):
    """Save a single posted job URL with timestamp to Gist - with retry"""
    # Force refresh to get latest data (avoid conflicts)
    posted_jobs = load_posted_jobs(force_refresh=True)
    
    # Double-check not already exists
    if job_url in posted_jobs:
        job_id = extract_job_id(job_url)
        log(f"⚠️ Job {job_id} already exists, skipping save")
        return True, False  # Success, but not new
    
    # Add with current timestamp
    posted_jobs[job_url] = datetime.now().isoformat()
    
    # Retry once on failure
    success = save_posted_jobs(posted_jobs)
    if not success:
        log("🔄 Retrying save...")
        time.sleep(1)
        success = save_posted_jobs(posted_jobs)
    
    job_id = extract_job_id(job_url)
    log(f"💾 Saved job {job_id} to Gist")
    return success, True  # Success, and was new

def is_job_posted(job_url):
    """Check if job has been posted before using Gist (with cache refresh if needed)"""
    posted_jobs = load_posted_jobs(force_refresh=False)
    
    if job_url in posted_jobs:
        job_id = extract_job_id(job_url)
        return True
    
    # If not in cache, double-check with fresh Gist load
    # This handles the case where another instance posted while we were running
    posted_jobs = load_posted_jobs(force_refresh=True)
    
    return job_url in posted_jobs

# ====================================
# HELPER FUNCTIONS
# ====================================
def clean_text(text):
    """Clean and normalize text"""
    if not text:
        return ""
    # Remove extra whitespace and normalize
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def extract_job_id(url):
    """Extract numeric job ID from URL"""
    match = re.search(r'/(\d+)', url)
    return f"#{match.group(1)}" if match else url[-20:-1] if len(url) > 20 else url

def format_deadline(deadline_text):
    """Format deadline with appropriate emoji"""
    if not deadline_text or deadline_text in ["N/A", "Apply Now", ""]:
        return "⏰ ፈጣን ማመልከቻ / Apply Soon"
    
    # Try to extract date
    date_patterns = [
        r'(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})',
        r'(\d{4})-(\d{2})-(\d{2})',
        r'(\d{2})/(\d{2})/(\d{4})',
    ]
    
    for pattern in date_patterns:
        match = re.search(pattern, deadline_text)
        if match:
            try:
                # Calculate days remaining (simplified)
                return f"⏰ {deadline_text}"
            except:
                pass
    
    return f"⏰ {deadline_text}"

def get_urgency_indicator(deadline_text):
    """Get urgency emoji based on deadline"""
    if not deadline_text:
        return "🟢"
    
    today = datetime.now().date()
    try:
        # Try common date formats
        for fmt in ["%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%Y-%m-%d", "%d/%m/%Y"]:
            try:
                deadline_date = datetime.strptime(deadline_text.strip(), fmt).date()
                days_left = (deadline_date - today).days
                if days_left < 0:
                    return "⚫"  # Expired
                elif days_left <= 2:
                    return "🔴"  # Urgent
                elif days_left <= 5:
                    return "🟠"  # Soon
                elif days_left <= 10:
                    return "🟡"  # Upcoming
                else:
                    return "🟢"  # Plenty of time
            except ValueError:
                continue
    except:
        pass
    
    return "🟢"

def format_job_type(job_type):
    """Format job type with emoji"""
    if not job_type or job_type == "N/A":
        return "💼 ሙሉ ጊዜ / Full-time"
    
    job_type_lower = job_type.lower()
    if "full" in job_type_lower or "permanent" in job_type_lower:
        return f"💼 {job_type} / Full-time"
    elif "part" in job_type_lower:
        return f"⏳ {job_type} / Part-time"
    elif "contract" in job_type_lower:
        return f"📋 {job_type} / Contract"
    elif "intern" in job_type_lower:
        return f"🎓 {job_type} / Internship"
    elif "freelance" in job_type_lower:
        return f"🌐 {job_type} / Freelance"
    elif "remote" in job_type_lower:
        return f"🏠 {job_type} / Remote"
    else:
        return f"💼 {job_type}"

def format_location(location):
    """Format location with emoji"""
    if not location or location == "N/A":
        return "📍 Addis Ababa, Ethiopia"
    
    # Check if it's remote
    if "remote" in location.lower():
        return f"🏠 {location} / Remote Work"
    
    return f"📍 {location}"

def truncate_text(text, max_words=25):
    """Truncate text to max words with ellipsis"""
    if not text:
        return ""
    words = text.split()
    if len(words) <= max_words:
        return text
    return ' '.join(words[:max_words]) + "..."

# ====================================
# SCRAPE JOB DETAIL
# ====================================
def scrape_job_detail(job_url):
    """Scrape detailed information for a single job"""
    try:
        log(f"🔍 Scraping: {job_url[-50:]}")
        job_id = extract_job_id(job_url)

        response = requests.get(job_url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        # ============ TITLE ============
        title_tag = soup.find("h1", id="jobTitle") or soup.find("h1", class_=re.compile("job|title", re.I))
        title = clean_text(title_tag.get_text()) if title_tag else "የስራ ማስታወቂያ / Job Posting"

        # ============ BASIC INFO ============
        job_type = "N/A"
        location = "N/A"
        deadline = "N/A"

        # Try multiple selectors
        info_selectors = [
            ("h5", "strong"),
            ("div", "strong"),
            ("p", "strong"),
            ("li", None),
        ]
        
        for tag, sub_tag in info_selectors:
            for element in soup.find_all(tag):
                label_elem = element.find(sub_tag) if sub_tag else element
                if not label_elem:
                    continue
                    
                label = clean_text(label_elem.get_text())
                if not label:
                    continue

                if "Employment" in label or "Job Type" in label:
                    job_type = clean_text(element.get_text().replace("Employment:", "").replace("Job Type:", ""))
                elif "Place of Work" in label or "Location" in label:
                    location = clean_text(element.get_text().replace("Place of Work:", "").replace("Location:", ""))
                elif "Deadline" in label or "Closing" in label:
                    deadline = clean_text(element.get_text().replace("Deadline:", "").replace("Closing Date:", ""))

        # ============ JOB DESCRIPTION ============
        description_sections = []
        
        # Find main content area
        content_selectors = [
            ("div", {"class": re.compile("job-description|jobdetail|content", re.I)}),
            ("article", {}),
            ("main", {}),
            ("div", {"class": re.compile("entry-content|post-content", re.I)}),
        ]
        
        job_content = None
        for tag, attrs in content_selectors:
            job_content = soup.find(tag, attrs) if attrs else soup.find(tag)
            if job_content:
                break
        
        if not job_content:
            job_content = soup.find("body")
        
        if job_content:
            # Extract structured content
            headers = job_content.find_all(["p"])
            
            for header in headers[:4]:  # Limit sections
                section_title = clean_text(header.get_text())
                if not section_title or len(section_title) > 100:
                    continue
                    
                # Get content until next header or limit
                content_parts = []
                next_elem = header.find_next_sibling()
                word_count = 0
                
                while next_elem and next_elem.name not in ["h2", "h3", "h4", "h5"]:
                    if next_elem.name in ["p", "li", "div"]:
                        text = clean_text(next_elem.get_text())
                        if text and len(text) > 15:
                            prefix = "• " if next_elem.name == "li" else ""
                            content_parts.append(prefix + text)
                            word_count += len(text.split())
                    
                    if word_count >= 40:  # Limit words per section
                        break
                    next_elem = next_elem.find_next_sibling()
                
                if content_parts:
                    section_text = "\n".join(content_parts)
                    section_text = truncate_text(section_text, 30)
                    description_sections.append((section_title, section_text))
            
            # Fallback: get paragraphs if no sections
            if not description_sections:
                paragraphs = []
                for p in job_content.find_all(["p", "li"]):
                    text = clean_text(p.get_text())
                    if text and len(text) > 30 and "apply" not in text.lower()[:20]:
                        prefix = "• " if p.name == "li" else ""
                        paragraphs.append(prefix + text)
                        if len(paragraphs) >= 4:
                            break
                
                if paragraphs:
                    combined = " ".join(paragraphs)
                    description_sections.append(("ዋና ሀላፊነቶች / Key Responsibilities", truncate_text(combined, 30)))

        log(f"✔ Scraped: {title[:40]}... ({job_id})")
        
        return {
            "id": job_id,
            "title": title,
            "type": job_type,
            "location": location,
            "deadline": deadline,
            "deadline_raw": deadline,  # Keep original for urgency check
            "sections": description_sections,
            "link": job_url,
            "scraped_at": datetime.now().isoformat()
        }

    except Exception as e:
        log(f"❌ Error scraping {job_url}: {str(e)}")
        return None

# ====================================
# SCRAPE JOBS - WITH PROPER GIST CHECK
# ====================================
def scrape_new_jobs():
    """Scrape new jobs that haven't been posted yet"""
    log("🚀 Starting job scrape...")
    print("═" * 60)

    try:
        # Step 1: Load posted jobs from Gist FIRST
        posted_jobs = load_posted_jobs(force_refresh=True)
        initial_count = len(posted_jobs)
        log(f"📊 Jobs already in Gist: {initial_count}")

        # Step 2: Fetch job listings
        response = requests.get(URL, headers=HEADERS, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        # Extract job links
        job_links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/job/" in href or "job-detail" in href or re.search(r'/\d{4,}/', href):
                full_url = href if href.startswith("http") else BASE_URL + href
                # Deduplicate
                if full_url not in job_links:
                    job_links.append(full_url)

        log(f"🔎 Found {len(job_links)} total jobs on site")

        # Step 3: Filter against Gist (the critical check)
        new_links = []
        already_posted = 0
        
        # Normalize URLs for comparison
        posted_urls_normalized = {url.rstrip('/').split('?')[0] for url in posted_jobs.keys()}
        
        for link in job_links[:20]:  # Check top 20
            normalized = link.rstrip('/').split('?')[0]
            if normalized not in posted_urls_normalized and link not in posted_jobs:
                new_links.append(link)
            else:
                already_posted += 1
                job_id = extract_job_id(link)
                log(f"⏭ Already posted: {job_id}")

        log(f"🆕 New jobs to scrape: {len(new_links)}")
        log(f"⏭ Skipped (already posted): {already_posted}")

        if not new_links:
            log("📭 No new jobs found")
            return []

        # Step 4: Scrape details with thread pool
        jobs = []
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {executor.submit(scrape_job_detail, link): link for link in new_links[:12]}
            
            for future in as_completed(futures):
                link = futures[future]
                try:
                    job_data = future.result()
                    if job_data:
                        # FINAL CHECK: Verify not posted during scrape
                        if not is_job_posted(link):
                            jobs.append(job_data)
                        else:
                            log(f"⏭ Race condition: {job_data['id']} was posted during scrape")
                except Exception as e:
                    log(f"❌ Failed to scrape {link}: {str(e)}")

        log(f"🎉 Successfully scraped {len(jobs)} new jobs")
        return jobs

    except Exception as e:
        log(f"❌ Error in scrape_new_jobs: {str(e)}")
        traceback.print_exc()
        return []

# ====================================
# BEAUTIFUL TELEGRAM POST
# ====================================
async def post_job(bot, job):
    """Post a job to Telegram with beautiful formatting"""
    try:
        # Format components
        urgency = get_urgency_indicator(job.get('deadline_raw', ''))
        job_type_formatted = format_job_type(job['type'])
        location_formatted = format_location(job['location'])
        deadline_formatted = format_deadline(job['deadline'])
        
        # Build description sections
        description_text = ""
        if job.get('sections'):
            section_parts = []
            for title, content in job['sections'][:2]:  # Max 2 sections
                section_parts.append(f"<b>📌 {title}</b>\n{content}")
            description_text = "\n\n".join(section_parts)
        else:
            description_text = "📋 ተጨማሪ ዝርዝር ለማየት ከታች ያለውን ሊንክ ይጫኑ\n<i>See more details via link below</i>"

        # Beautiful message template
        message = f"""
╔═══════════════════════════════════════╗
║   🇪🇹  <b>የኢትዮጵያ የስራ ማስታወቂያ</b>      ║
║   <b>ETHIOPIAN JOB ALERT</b>          ║
╚═══════════════════════════════════════╝

{urgency} <b>{job['title'].upper()}</b>

┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃  📋 <b>ዝርዝር እይታ / JOB DETAILS</b>
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛

🏢 <b>የስራው አይነት</b>
   ├ {job_type_formatted}

🗺 <b>የስራው ቦታ</b>
   ├ {location_formatted}

⏳ <b>የማመልከቻ ማብቂያ ቀን</b>
   ├ {deadline_formatted}

┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃  📝 <b>ዋና መረጃ / SUMMARY</b>
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛

{description_text}

┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃  🔗 <b>ለማመልከት / HOW TO APPLY</b>
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛

👉 <a href="{job['link']}">እዚህ ይጫኑ / Click Here to Apply</a>
   <code>{job['link'][:60]}...</code>

• • • • • • • • • • • • • • • • • • • •
🔔 <b>ማሳሰቢያ:</b> ዛሬ ያመልክቱ! ነገ አይዘገዩ!
   <i>Apply today! Don't delay!</i>

📌 <b>ተጨማሪ ስራዎች:</b> @trytry1221
   <i>More jobs on our channel</i>

🆔 <code>Job ID: {job['id']}</code>
⏰ Posted: {datetime.now().strftime('%b %d, %Y %H:%M')}
"""

        # Attractive buttons
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📝 አመልክት / APPLY NOW", url=job["link"]),
            ],
            [
                InlineKeyboardButton("🔔 ቻናላችንን ይቀላቀሉ / Join Channel", url="https://t.me/trytry1221"),
                InlineKeyboardButton("📢 ለጓደኛ ያጋሩ / Share", url=f"https://t.me/share/url?url={quote(job['link'])}&text={quote('Check out this job: ' + job['title'])}"),
            ],
            [
                InlineKeyboardButton("🌐 ድህረገፃችን / Website", url=BASE_URL),
            ]
        ])

        # Send message
        sent_msg = await bot.send_message(
            chat_id=CHANNEL_ID,
            text=message,
            parse_mode="HTML",
            reply_markup=keyboard,
            disable_web_page_preview=False
        )
        
        # CRITICAL: Save to Gist AFTER successful post
        save_success, was_new = save_posted_job(job['link'])
        
        if save_success:
            log(f"✅ Posted & saved: {job['title'][:45]}... (Msg ID: {sent_msg.message_id})")
        else:
            log(f"⚠️ Posted but Gist save failed: {job['id']}")
        
        return save_success or was_new  # Return True if posted successfully
        
    except TelegramError as e:
        log(f"❌ Telegram error posting {job.get('id', 'unknown')}: {str(e)}")
        return False
    except Exception as e:
        log(f"❌ Error posting {job.get('id', 'unknown')}: {str(e)}")
        traceback.print_exc()
        return False

# ====================================
# JOB POSTING CYCLE
# ====================================
async def job_posting_cycle(bot):
    """Complete cycle: scrape and post new jobs"""
    cycle_start = datetime.now()
    print("\n" + "╔" + "═" * 58 + "╗")
    print("║" + f"{'📊 NEW JOB POSTING CYCLE':^58}" + "║")
    print("║" + f"{cycle_start.strftime('%Y-%m-%d %H:%M:%S'):^58}" + "║")
    print("╚" + "═" * 58 + "╝")

    # Scrape new jobs
    log("📡 Fetching fresh job listings...")
    new_jobs = scrape_new_jobs()

    if not new_jobs:
        print("═" * 60)
        log("📭 No new jobs to post")
        print("═" * 60 + "\n")
        return 0, 0

    # Post jobs
    print("\n" + "╔" + "═" * 58 + "╗")
    print("║" + f"{'🚀 POSTING ' + str(len(new_jobs)) + ' NEW JOBS':^58}" + "║")
    print("╚" + "═" * 58 + "╝\n")

    posted = 0
    failed = 0

    for i, job in enumerate(new_jobs, 1):
        progress = f"[{i}/{len(new_jobs)}]"
        log(f"{progress} Posting: {job['title'][:35]}...")
        
        success = await post_job(bot, job)
        if success:
            posted += 1
        else:
            failed += 1

        # Delay between posts (except last)
        if i < len(new_jobs):
            log(f"⏳ Waiting {DELAY_BETWEEN_POSTS}s...")
            await asyncio.sleep(DELAY_BETWEEN_POSTS)

    # Summary
    duration = (datetime.now() - cycle_start).total_seconds()
    print("\n" + "╔" + "═" * 58 + "╗")
    print("║" + f"{'✅ CYCLE COMPLETE':^58}" + "║")
    print("║" + f"{'Posted: ' + str(posted) + ' | Failed: ' + str(failed):^58}" + "║")
    print("║" + f"{'Duration: ' + str(int(duration)) + 's':^58}" + "║")
    print("╚" + "═" * 58 + "╝\n")

    # Verify final count
    final_jobs = load_posted_jobs(force_refresh=True)
    log(f"📊 Total jobs in Gist now: {len(final_jobs)}")

    return posted, failed

# ====================================
# MAIN
# ====================================
async def main():
    """Main entry point for cron job"""
    start_time = datetime.now()
    
    # Header
    print("""
    ╔══════════════════════════════════════════════════════════╗
    ║  🇪🇹  የኢትዮጵያ ስራዎች ቦርሳ - ክሮን የስራ ሂደት    ║
    ║  ETHIOPIAN JOBS BOT - CRON SCHEDULED TASK              ║
    ╚══════════════════════════════════════════════════════════╝
    """)

    # Debug environment
    log("🔍 Environment check...")
    env_status = {
        "BOT_TOKEN": "✅ Set" if TOKEN else "❌ Missing",
        "CHANNEL_ID": CHANNEL_ID,
        "GIST_TOKEN": "✅ Set" if GIST_TOKEN else "❌ Missing", 
        "GIST_ID": "✅ Set" if GIST_ID else "❌ Missing",
    }
    for key, val in env_status.items():
        log(f"   {key}: {val}")

    # Validate required config
    missing = []
    if not TOKEN:
        missing.append("BOT_TOKEN")
    if not GIST_TOKEN:
        missing.append("GIST_TOKEN")
    if not GIST_ID:
        missing.append("GIST_ID")
    
    if missing:
        log(f"❌ Missing required: {', '.join(missing)}")
        return 1

    # Initialize bot
    try:
        bot = Bot(token=TOKEN)
        me = await bot.get_me()
        log(f"🤖 Bot: @{me.username} (ID: {me.id})")
    except Exception as e:
        log(f"❌ Bot connection failed: {str(e)}")
        return 1

    # Test Gist access
    try:
        test_jobs = load_posted_jobs(force_refresh=True)
        log(f"📁 Gist access OK: {len(test_jobs)} existing jobs")
    except Exception as e:
        log(f"❌ Gist access failed: {str(e)}")
        return 1

    # Test channel access
    try:
        test_msg = await bot.send_message(
            chat_id=CHANNEL_ID,
            text=f"🔧 <b>Cron Test</b> - {start_time.strftime('%H:%M:%S')}\n✅ Bot & Gist operational",
            parse_mode="HTML"
        )
        await bot.delete_message(chat_id=CHANNEL_ID, message_id=test_msg.message_id)
        log(f"📢 Channel access OK: {CHANNEL_ID}")
    except Exception as e:
        log(f"❌ Channel access failed: {str(e)}")
        log("   Ensure bot is admin with post permissions")
        return 1

    print("═" * 60)
    
    # Run main cycle
    posted, failed = await job_posting_cycle(bot)
    
    # Footer
    total_time = (datetime.now() - start_time).total_seconds()
    log(f"🏁 Total runtime: {total_time:.1f}s")
    log(f"📊 Jobs posted this run: {posted}")

    return 0 if failed == 0 else 1

if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        exit(exit_code)
    except KeyboardInterrupt:
        log("\n⚠️ Interrupted by user")
        exit(130)
    except Exception as e:
        log(f"\n💥 Fatal error: {str(e)}")
        traceback.print_exc()
        exit(1)
