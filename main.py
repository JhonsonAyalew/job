import asyncio
import requests
from bs4 import BeautifulSoup
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from datetime import datetime, timedelta
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import json
import os
from urllib.parse import quote

# ====================================
# CONFIG - RENDER DISK + 4 HOUR CRON
# ====================================
TOKEN = os.environ.get("BOT_TOKEN", "8191854029:AAFdBYDf5wqAMXEXEubrzLfmsJubF6icm1w")  # Added env var support
CHANNEL_ID = "@trytry1221"
DELAY_BETWEEN_POSTS = 4

BASE_URL = "https://geezjobs.com"
URL = "https://geezjobs.com/jobs-in-ethiopia"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

# ====================================
# RENDER DISK STORAGE - ONLY CHANGE 1
# ====================================
DISK_PATH = "/var/data"
os.makedirs(DISK_PATH, exist_ok=True)
POSTED_JOBS_FILE = os.path.join(DISK_PATH, "posted_jobs.json")

# ====================================
# LOGGER
# ====================================
def log(message):
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] {message}")

# ====================================
# POSTED JOBS TRACKING - FIXED to use URL
# ====================================
def load_posted_jobs():
    """Load previously posted job URLs from file"""
    if os.path.exists(POSTED_JOBS_FILE):
        try:
            with open(POSTED_JOBS_FILE, 'r') as f:
                data = json.load(f)
                # Clean jobs older than 7 days
                current_time = datetime.now()
                valid_jobs = {}
                for job_url, timestamp in data.items():
                    job_time = datetime.fromisoformat(timestamp)
                    if current_time - job_time < timedelta(days=7):
                        valid_jobs[job_url] = timestamp
                return valid_jobs
        except Exception as e:
            log(f"Error loading posted jobs: {e}")
    return {}

def save_posted_job(job_url):
    """Save a posted job URL with timestamp"""
    posted_jobs = load_posted_jobs()
    posted_jobs[job_url] = datetime.now().isoformat()
    try:
        with open(POSTED_JOBS_FILE, 'w') as f:
            json.dump(posted_jobs, f, indent=2)
    except Exception as e:
        log(f"Error saving posted job: {e}")

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
# TELEGRAM MINI APP GENERATOR
# ====================================
def generate_mini_app_url(job_url, job_title):
    """Generate a Telegram Mini App URL for the job"""
    # You need to host a web app that displays jobs
    # This is a placeholder - you need to deploy your own web app
    base_web_app_url = "https://your-telegram-mini-app.com/job"
    
    # Encode parameters
    params = {
        'url': job_url,
        'title': job_title[:100],
        'source': 'geezjobs'
    }
    
    query_string = '&'.join([f"{k}={quote(str(v))}" for k, v in params.items()])
    return f"{base_web_app_url}?{query_string}"

# ====================================
# SCRAPE JOB DETAIL 
# ====================================
def scrape_job_detail(job_url):
    try:
        log(f"➡ Visiting: {job_url}")

        response = requests.get(job_url, headers=HEADERS, timeout=10)
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
                if element.name in ["b", "strong", "h2", "h3", "h4", "h5"]:
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
            
            if current_section and current_content:
                section_text = "\n".join(current_content)
                words = section_text.split()
                if len(words) > 20:
                    section_text = ' '.join(words[:20]) + "..."
                all_sections.append(f"<b>{current_section}</b>\n{section_text}")
        
        if not all_sections:
            paragraphs = []
            if job_content:
                for p in job_content.find_all("p"):
                    text = p.get_text(" ", strip=True)
                    if text and len(text) > 20 and "how to apply" not in text.lower():
                        paragraphs.append(text)
            
            if paragraphs:
                fallback_text = "\n".join(paragraphs[:8])
                words = fallback_text.split()
                if len(words) > 20:
                    fallback_text = ' '.join(words[:20]) + "..."
                all_sections.append("<b>Job Description</b>\n" + fallback_text)
        
        if all_sections:
            full_description = "\n\n━━━━━━━━━━━━━━━━━━━━━━\n\n".join(all_sections)
        else:
            full_description = "ዝርዝር መረጃ አልተገኘም"
        
        job_id = extract_job_id(job_url)
        log(f"✔ Finished: {title} - ID: {job_id}")

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
        log(f"❌ Error: {e}")
        return None

# ====================================
# SCRAPE JOBS (Multi-threaded with duplicate check)
# ====================================
def scrape_new_jobs():
    log("🚀 Starting new jobs scrape...")

    try:
        response = requests.get(URL, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(response.text, "html.parser")

        job_links = [
            BASE_URL + a["href"]
            for a in soup.find_all("a", class_="color-green")
        ]

        log(f"🔎 Found {len(job_links)} total jobs")

        # Filter out already posted jobs - USING URL NOW
        new_job_links = []
        for link in job_links:
            if not is_job_posted(link):  # Now checks using URL
                new_job_links.append(link)
            else:
                job_id = extract_job_id(link)
                log(f"⏭ Skipping already posted job {job_id} - {link}")

        log(f"🆕 Found {len(new_job_links)} new jobs to post")

        if not new_job_links:
            log("📭 No new jobs found")
            return []

        jobs = []
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = [executor.submit(scrape_job_detail, link) for link in new_job_links[:10]]  # Limit to 10 per cycle

            for future in as_completed(futures):
                result = future.result()
                if result:
                    jobs.append(result)

        log(f"🎉 Scraped {len(jobs)} new jobs successfully")
        return jobs

    except Exception as e:
        log(f"❌ Error fetching job list: {e}")
        return []

# ====================================
# TELEGRAM POST FUNCTION
# ====================================
async def post_job(bot, job):
    deadline_formatted = format_deadline(job['deadline'])
    current_time = datetime.now().strftime("%I:%M %p")
    
    # Generate Mini App URL
    mini_app_url = generate_mini_app_url(job['link'], job['title'])
    
    message = f"""

     💼  የኢትዮጵያ የስራ ማስታወቂያ  💼
     
━━━━━━━━━━━━━━━━━━━━━━
<b>{job['title'].upper()}</b>
━━━━━━━━━━━━━━━━━━━━━━

🏢 <b>የስራው አይነት:</b> {job['type']}

🗺 <b>የስራው ቦታ:</b> {job['location']}

⏳ <b>የማመልከቻ ማብቂያ ቀን: </b> {deadline_formatted}

━━━━━━━━━━━━━━━━━━━━━━
📎 <b>አመልክት:</b> {job['link']}
━━━━━━━━━━━━━━━━━━━━━━

{job['detail']}

━━━━━━━━━━━━━━━━━━━━━━
🔔 ማሳሰቢያ: ዛሬ ያመልክቱ! ነገ አይዘገዩ!
"""

    # Create buttons with Mini App support
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📋 አመልክት / APPLY", url=job["link"]),
           
        ],
        [InlineKeyboardButton("📢 ሌሎች ስራዎች", url="https://t.me/trytry1221")]
    ])

    try:
        await bot.send_message(
            chat_id=CHANNEL_ID,
            text=message,
            parse_mode="HTML",
            reply_markup=keyboard,
            disable_web_page_preview=False
        )
        
        # Save the posted job - USING URL NOW
        save_posted_job(job['link'])  # Save the full URL, not the ID
            
        print(f"✅ ተለጠፈ: {job['title'][:50]}...")
        return True
    except Exception as e:
        print(f"❌ ስህተት: {e}")
        return False

# ====================================
# JOB POSTING CYCLE
# ====================================
async def job_posting_cycle(bot):
    """One complete cycle of scraping and posting"""
    print("\n" + "═"*50)
    print(f"     📊 አዲስ የስራ ማስታወቂያ ዑደት")
    print(f"     {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("═"*50)
    
    print("📡 የስራ ማስታወቂያዎችን በማምጣት ላይ...")
    new_jobs = scrape_new_jobs()
    
    if not new_jobs:
        print("\n📭 አዲስ የስራ ማስታወቂያ አልተገኘም")
        print("═"*50 + "\n")
        return
    
    print("\n" + "═"*50)
    print(f"     🚀 {len(new_jobs)} አዳዲስ ስራዎች በመለጠፍ ላይ...")
    print("═"*50 + "\n")
    
    posted_count = 0
    for index, job in enumerate(new_jobs, 1):
        print(f"📤 [{index}/{len(new_jobs)}] በመላክ ላይ...")
        success = await post_job(bot, job)
        if success:
            posted_count += 1
        
        if index < len(new_jobs):
            print(f"⏳ {DELAY_BETWEEN_POSTS} ሰከንድ ይጠብቁ...\n")
            await asyncio.sleep(DELAY_BETWEEN_POSTS)
    
    print("\n" + "═"*50)
    print(f"     ✅ {posted_count}/{len(new_jobs)} ስራዎች በተሳካ ሁኔታ ተለጥፈዋል!")
    print(f"     ⏭ ቀጣይ ማረሚያ በ{SCRAPE_INTERVAL} ሰከንድ ውስጥ...")
    print("═"*50 + "\n")

# ====================================
# MAIN - 4 HOUR CRON VERSION (ONLY CHANGE 2)
# ====================================
async def main():
    bot = Bot(token=TOKEN)
    
    print("""
    ╔════════════════════════════════════════╗
    ║     🇪🇹 የኢትዮጵያ ስራዎች - ቀጣይነት ያለው     ║
    ║       ETHIOPIAN JOBS - CONTINUOUS      ║
    ╚════════════════════════════════════════╝
    """)
    
    print(f"📊 የሚረምረው በየ 4 ሰዓት (Render Cron)")
    print(f"📁 የRender Cron)")
    print(f"📁 የተለጠፉተለጠፉ ስ ስራዎችራዎች መዝ መዝገብ: {POSTED_JOBS_FILEገብ: {POSTED_JOBS_FILE}")
    print(f"}")
    print(f"📋📋 የሚ የሚለጠፍለጠፍበትበት ቻናል: {CH ቻናል: {CHANNEL_ID}")
    print("═"ANNEL_ID}")
    print("═"*60 + "\*60 + "\n")
    
    # Run ONCE -n")
    
    # Run ONCE - not forever (for cron job)
    not forever (for cron job)
    await job_posting_cycle(bot)
 await job_posting_cycle(bot)
    
    print(f"✅    
    print(f"✅ 4 ሰ 4 ሰዓት ዑደት ተዓት ዑደት ተጠናቋጠናቋልል - { - {datetime.now()}datetime.now()}")

if __")

if __name__ == "__main__":
    tryname__ == "__main__":
:
        asyn    try:
        asyncio.run(main())
    except KeyboardInterrupt:
       cio.run(main())
    except KeyboardInterrupt:
        print("\ print("\nn\n⚠️\n⚠️ ፕ ፕሮሮግራግራምም ቆሟል ( ቆሟል (Program stoppedProgram stopped by user)")
    except Exception as e:
 by user)")
    except Exception as e:
        print(f"\n        print(f"\n❌❌ ከባ ከባድ ስድህተት: {e}")
