# Business Email Finder Telegram Bot

A Python Telegram bot for finding **publicly listed business contact emails** from a search result's website or a website URL you provide. It stores leads locally in SQLite, exports CSV, tracks lead status, and drafts a proposal message.

## Features
- `/search restaurants Lagos Nigeria` — search the web for relevant sites and check a small number of public contact pages.
- `/website https://example-business.com` — inspect a limited set of public pages on a website.
- `/leads` — show the latest saved leads.
- `/export` — download saved leads as CSV.
- `/status 1 Contacted` — update a lead's status.
- `/proposal Business Name service` — draft a proposal you can review and personalize.
- Duplicate prevention via SQLite.

## Setup

### 1. Create your Telegram bot
1. Open Telegram and search for **@BotFather**.
2. Send `/newbot` and follow its instructions.
3. Copy the bot token. Keep it private.

### 2. Install Python packages
Python 3.10+ is recommended.

```bash
python -m venv .venv
```

Activate the environment:
- Windows: `.venv\\Scripts\\activate`
- macOS/Linux: `source .venv/bin/activate`

Then install dependencies:

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

Copy `.env.example` to `.env` and set `TELEGRAM_BOT_TOKEN`. This starter uses environment variables; it does not automatically load `.env`. Either set them in your shell/hosting dashboard or install `python-dotenv` and add `from dotenv import load_dotenv; load_dotenv()` near the top of `bot.py`.

Linux/macOS example:
```bash
export TELEGRAM_BOT_TOKEN="YOUR_TOKEN"
python bot.py
```

Windows PowerShell example:
```powershell
$env:TELEGRAM_BOT_TOKEN="YOUR_TOKEN"
python bot.py
```

### 4. Optional: add a search API key
Set `BRAVE_SEARCH_API_KEY` in your environment to use the Brave Search API. Review its current pricing, limits, and terms before use. If it is not set, the bot attempts a free fallback search package; this fallback can be rate-limited or unavailable.

### 5. Run
```bash
python bot.py
```
Keep the process running for the bot to remain online. On hosting, configure the environment variables in the provider dashboard and use a persistent process/worker where supported.

## Important limitations
- This is a starter project, not a guaranteed lead database. Search results and email extraction can be incomplete.
- The free fallback search method may stop working or be rate-limited. A supported search API is more reliable.
- The bot only checks a small number of likely contact/about pages. It does not bypass logins, CAPTCHAs, paywalls, or access restrictions.
- Respect each website's terms and robots.txt, use low request rates, and comply with applicable privacy, marketing, and anti-spam rules. Use the data for relevant, responsible business outreach.
- Public availability does not automatically mean an address is suitable for marketing or that the person has consented to receive messages.
- The bot does not send emails automatically.
- For real deployment, replace the example User-Agent contact in `bot.py` with a genuine contact address, add logging, and restrict access to your own Telegram user ID if you will store business leads privately.

## Privacy / security
- Never publish your Telegram bot token or API keys.
- `leads.db` contains the saved lead list. Protect it and back it up securely.
- Add user-ID access controls before allowing other Telegram users to use a private lead database.
