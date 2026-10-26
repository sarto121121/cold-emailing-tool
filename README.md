# Local AI Cold-Email Assistant

Reads leads from Excel, visits each lead's website, asks a **local Ollama model** to write a unique
email for that business, lets you review the drafts, then sends the approved ones with safe limits.

## One-time setup

1. Install Ollama from https://ollama.com and pull a model that fits your 8 GB GPU:
   ```
   ollama pull qwen2.5:7b
   ```
2. In this folder (a virtual environment is already created in `.venv`):
   ```
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```
3. Edit `config.toml`: `[sender]`, `[offer]` (what you sell), `[smtp]`.
4. Copy `.env.example` to `.env` and put your email password in it
   (Gmail needs an *App Password*, not your normal password).
5. Test everything: `.\.venv\Scripts\python.exe main.py check`

## Daily workflow

1. Put leads in `leads.xlsx` with columns **name, email, company, website**
   (`make_sample_leads.py` creates an example).
2. `python main.py draft` : scrapes sites, writes `drafts.xlsx`. Re-running only drafts *new* leads.
3. Open `drafts.xlsx`. Read each email, fix anything, set `status` to **approved** (or `skip`).
   Check the `notes` column: it flags unreadable websites and leftover `[placeholders]`.
4. `python main.py send` : dry run, prints what would go out.
5. `python main.py send --live` : sends approved emails, with random delays and the daily limit.

Other: `python main.py unsub someone@x.com` adds an address to the do-not-contact list.
When someone replies "unsubscribe", add them here.

## Safety built in

- Dry run by default; `--live` plus a typed `YES` to send for real.
- Daily limit (`[limits]`), random delay between emails, never emails the same address twice.
- `unsubscribe.txt` is always respected. Every email gets an opt-out line, your name and address.
- Progress is saved after every lead/email, so a crash or Ctrl+C loses nothing.
- Close `drafts.xlsx` in Excel before running a command (Excel locks the file).

## Tips

- Start with 5-10 leads and read every draft. Small local models can occasionally misread a website.
- Start at ~20 emails/day and raise slowly. Use a real domain with SPF/DKIM/DMARC for best deliverability.
- Only contact businesses where outreach is relevant and lawful in their country.
- Better quality later? Swap `writer._call_ollama` for the Claude API; nothing else changes.
