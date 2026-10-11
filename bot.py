import os
import re
import csv
import io
import asyncio
import sqlite3
from urllib.parse import urlparse, urljoin, quote_plus
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes
)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
BRAVE_API_KEY = os.getenv("BRAVE_SEARCH_API_KEY", "").strip()
DB_PATH = os.getenv("DATABASE_PATH", "leads.db")
USER_AGENT = "BusinessEmailFinderBot/1.0 (contact: configure-your-contact@example.com)"
REQUEST_TIMEOUT = 12
MAX_PAGES_PER_SITE = 5
MAX_RESULTS_PER_SEARCH = 10

EMAIL_RE = re.compile(r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", re.I)
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_name TEXT,
            industry TEXT,
            location TEXT,
            email TEXT NOT NULL,
            website TEXT,
            source_url TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'New',
            UNIQUE(email, website)
        )
    """)
    conn.commit()
    return conn


def normalize_url(url):
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urlparse(url)
    if not parsed.netloc:
        raise ValueError("That doesn't look like a valid website URL.")
    return f"{parsed.scheme}://{parsed.netloc}"


def can_fetch(url):
    """Respect robots.txt where it can be checked. Fail closed on explicit disallow."""
    try:
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        rp = RobotFileParser()
        rp.set_url(robots_url)
        rp.read()
        return rp.can_fetch(USER_AGENT, url)
    except Exception:
        # If robots.txt is unavailable, proceed cautiously with a small request limit.
        return True


def fetch_page(url):
    if not can_fetch(url):
        return None
    try:
        response = SESSION.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        if response.status_code != 200:
            return None
        content_type = response.headers.get("Content-Type", "").lower()
        if "text/html" not in content_type:
            return None
        if len(response.content) > 2_000_000:
            return None
        return response.text
    except requests.RequestException:
        return None


def extract_emails_from_site(start_url, max_pages=MAX_PAGES_PER_SITE):
    """Checks the given public page and a few obvious contact/about pages."""
    root = normalize_url(start_url)
    parsed_root = urlparse(root)
    candidates = [
        root,
        urljoin(root + "/", "contact"),
        urljoin(root + "/", "contact-us"),
        urljoin(root + "/", "about"),
        urljoin(root + "/", "about-us"),
    ]
    found = []
    seen_urls = set()

    for url in candidates[:max_pages]:
        if url in seen_urls:
            continue
        seen_urls.add(url)
        html = fetch_page(url)
        if not html:
            continue

        soup = BeautifulSoup(html, "html.parser")
        for a in soup.select('a[href^="mailto:"]'):
            address = a.get("href", "").split(":", 1)[-1].split("?", 1)[0].strip()
            if EMAIL_RE.fullmatch(address):
                found.append((address.lower(), url))

        text = soup.get_text(" ", strip=True)
        for address in EMAIL_RE.findall(text):
            found.append((address.lower(), url))

        # Follow only same-domain links whose anchor suggests contact/about.
        for a in soup.find_all("a", href=True):
            label = (a.get_text(" ", strip=True) + " " + a["href"]).lower()
            if any(word in label for word in ("contact", "about", "support", "enquir", "inquiry")):
                target = urljoin(url, a["href"])
                p = urlparse(target)
                if p.netloc == parsed_root.netloc and target not in seen_urls and len(seen_urls) < max_pages:
                    seen_urls.add(target)
                    html2 = fetch_page(target)
                    if html2:
                        soup2 = BeautifulSoup(html2, "html.parser")
                        for ma in soup2.select('a[href^="mailto:"]'):
                            address = ma.get("href", "").split(":", 1)[-1].split("?", 1)[0].strip()
                            if EMAIL_RE.fullmatch(address):
                                found.append((address.lower(), target))
                        for address in EMAIL_RE.findall(soup2.get_text(" ", strip=True)):
                            found.append((address.lower(), target))
        if len(seen_urls) >= max_pages:
            break

    # Remove obvious non-contact and asset false positives.
    cleaned = []
    for email, source in found:
        if email.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")):
            continue
        if any(x in email for x in ("example.com", "yourdomain.com", "wixpress.com")):
            continue
        cleaned.append((email, source))
    return list(dict.fromkeys(cleaned))


def search_brave(query, count=10):
    if not BRAVE_API_KEY:
        return None
    try:
        response = SESSION.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": min(count, 20)},
            headers={"Accept": "application/json", "X-Subscription-Token": BRAVE_API_KEY},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        results = []
        for item in data.get("web", {}).get("results", []):
            url = item.get("url", "")
            title = item.get("title", "")
            if url.startswith(("http://", "https://")):
                results.append({"url": url, "title": title})
        return results
    except Exception:
        return []


def search_free_fallback(query, count=10):
    """Uses the DDGS package as a best-effort fallback; availability can vary."""
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            items = list(ddgs.text(query, max_results=count))
        return [
            {"url": item.get("href", ""), "title": item.get("title", "")}
            for item in items if item.get("href", "").startswith(("http://", "https://"))
        ]
    except Exception:
        try:
            from duckduckgo_search import DDGS
            with DDGS() as ddgs:
                items = list(ddgs.text(query, max_results=count))
            return [
                {"url": item.get("href", ""), "title": item.get("title", "")}
                for item in items if item.get("href", "").startswith(("http://", "https://"))
            ]
        except Exception:
            return []


def search_web(query, count=10):
    if BRAVE_API_KEY:
        results = search_brave(query, count)
        if results is not None:
            return results, "Brave Search API"
    return search_free_fallback(query, count), "free fallback search"


def save_lead(business_name, industry, location, email, website, source_url):
    conn = db()
    try:
        conn.execute(
            """INSERT OR IGNORE INTO leads
            (business_name, industry, location, email, website, source_url)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (business_name, industry, location, email, website, source_url)
        )
        conn.commit()
        return conn.total_changes > 0
    finally:
        conn.close()


def domain_root(url):
    try:
        return normalize_url(url)
    except Exception:
        return url


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🔎 Search businesses", callback_data="help_search")],
        [InlineKeyboardButton("🌐 Extract website emails", callback_data="help_website")],
        [InlineKeyboardButton("📁 Export leads CSV", callback_data="help_export")],
        [InlineKeyboardButton("✉️ Generate proposal", callback_data="help_proposal")],
    ]
    await update.message.reply_text(
        "📩 *Business Email Finder*\n\n"
        "Find publicly listed business contact emails for your digital marketing outreach.\n\n"
        "*Commands*\n"
        "`/search restaurants Lagos Nigeria`\n"
        "`/website https://business-site.com`\n"
        "`/export`\n"
        "`/leads`\n"
        "`/status 1 Contacted`\n"
        "`/proposal Restaurant Name social media management`\n\n"
        "Use only public business contact details, respect site rules and applicable laws, "
        "and avoid bulk unsolicited messaging.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    messages = {
        "help_search": "Use: `/search industry city country`\nExample: `/search restaurants Lagos Nigeria`",
        "help_website": "Use: `/website https://business-site.com`",
        "help_export": "Use `/export` to download saved leads as a CSV file.",
        "help_proposal": "Use: `/proposal Business Name service`\nExample: `/proposal Bella Restaurant social media management`",
    }
    await query.message.reply_text(messages.get(query.data, "Use /help for commands."), parse_mode="Markdown")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or len(context.args) < 2:
        await update.message.reply_text("Usage: /search industry location\nExample: /search restaurants Lagos Nigeria")
        return

    query_text = " ".join(context.args)
    query = f"{query_text} official website contact email"
    await update.message.reply_text(f"Searching for: {query_text}\nThis may take a little while...")
    results, provider = await asyncio.to_thread(search_web, query, MAX_RESULTS_PER_SEARCH)
    if not results:
        await update.message.reply_text(
            "No search results were returned. If you have not configured a search API key, "
            "the free fallback may be temporarily unavailable. Try /website with a known business website."
        )
        return

    saved = 0
    processed_domains = set()
    details = []
    for item in results[:MAX_RESULTS_PER_SEARCH]:
        url = item.get("url", "")
        if not url:
            continue
        root = domain_root(url)
        if root in processed_domains:
            continue
        processed_domains.add(root)
        emails = await asyncio.to_thread(extract_emails_from_site, root)
        title = (item.get("title") or url)[:120]
        for email, source in emails:
            if save_lead(title, query_text, query_text, email, root, source):
                saved += 1
                details.append(f"• {title}\n  {email}\n  {root}")

    if details:
        body = "\n\n".join(details[:15])
        await update.message.reply_text(
            f"Search source: {provider}\nNew leads saved: {saved}\n\n{body}\n\nUse /export to download the saved leads."
        )
    else:
        await update.message.reply_text(
            f"Search source: {provider}\nFound {len(results)} websites, but no new public emails were extracted. "
            "Some sites do not publish email addresses or may block automated access."
        )


async def website_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /website https://business-site.com")
        return
    try:
        url = normalize_url(context.args[0])
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return

    await update.message.reply_text(f"Checking a small set of public contact pages on {url}...")
    emails = await asyncio.to_thread(extract_emails_from_site, url)
    saved = 0
    for email, source in emails:
        if save_lead(urlparse(url).netloc, "Unknown", "Unknown", email, url, source):
            saved += 1

    if not emails:
        await update.message.reply_text(
            "No publicly listed email was found on the checked pages. The website may use a contact form instead."
        )
        return
    lines = [f"• {email}\n  Source: {source}" for email, source in emails[:20]]
    await update.message.reply_text(
        f"Found {len(emails)} email address(es); saved {saved} new lead(s).\n\n" + "\n\n".join(lines)
    )


async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = db()
    try:
        rows = conn.execute(
            "SELECT business_name, industry, location, email, website, source_url, created_at, status FROM leads ORDER BY id DESC"
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        await update.message.reply_text("No leads saved yet. Try /search or /website first.")
        return

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["Business", "Industry", "Location", "Email", "Website", "Source URL", "Created At", "Status"])
    for row in rows:
        writer.writerow(list(row))
    data = io.BytesIO(buffer.getvalue().encode("utf-8-sig"))
    data.name = "business_leads.csv"
    await update.message.reply_document(document=data, caption=f"Exported {len(rows)} saved lead(s).")


async def leads_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = db()
    try:
        rows = conn.execute(
            "SELECT id, business_name, email, website, status FROM leads ORDER BY id DESC LIMIT 15"
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        await update.message.reply_text("No leads saved yet.")
        return
    lines = [
        f"#{r['id']} — {r['business_name']}\n{r['email']}\n{r['website']}\nStatus: {r['status']}"
        for r in rows
    ]
    await update.message.reply_text("Latest leads:\n\n" + "\n\n".join(lines))


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /status LEAD_ID STATUS\nExample: /status 1 Contacted")
        return
    try:
        lead_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Lead ID must be a number.")
        return
    status = " ".join(context.args[1:])[:40]
    conn = db()
    try:
        cur = conn.execute("UPDATE leads SET status = ? WHERE id = ?", (status, lead_id))
        conn.commit()
    finally:
        conn.close()
    await update.message.reply_text("Lead status updated." if cur.rowcount else "Lead ID not found.")


async def proposal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /proposal Business Name service\nExample: /proposal Bella Restaurant social media management")
        return
    service = context.args[-1]
    business = " ".join(context.args[:-1])
    proposal = (
        f"Subject: Digital Marketing Proposal for {business}\n\n"
        f"Hello {business} Team,\n\n"
        f"My name is [Your Name], and I help businesses strengthen their online presence and connect with more customers. "
        f"I am reaching out to explore whether I could support {business} with {service}.\n\n"
        f"I would be glad to learn about your current goals and suggest a practical approach tailored to your business. "
        f"If you are open to it, could we arrange a brief conversation at a convenient time?\n\n"
        f"Thank you for your time.\n\n"
        f"Best regards,\n[Your Name]\nDigital Marketer\n[Your Phone / Portfolio]"
    )
    await update.message.reply_text(proposal)


def main():
    if not BOT_TOKEN:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN in your environment before running the bot.")
    db()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(CommandHandler("website", website_command))
    app.add_handler(CommandHandler("export", export_command))
    app.add_handler(CommandHandler("leads", leads_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("proposal", proposal_command))
    app.add_handler(CallbackQueryHandler(button_handler))
    print("Business Email Finder Bot is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
