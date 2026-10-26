"""Build and send emails over SMTP, plus the unsubscribe list and sent log."""
import csv
import os
import re
import smtplib
import ssl
from datetime import date, datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def load_env(path=".env"):
    """Minimal .env loader so the password never has to live in config.toml."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def is_valid_email(address):
    return bool(EMAIL_RE.match(address or ""))


# ---------- unsubscribe list ----------
def load_unsubscribed(path):
    p = Path(path)
    if not p.exists():
        return set()
    return {ln.strip().lower() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()}


def add_unsubscribed(path, address):
    address = address.strip().lower()
    if address and address not in load_unsubscribed(path):
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(address + "\n")


# ---------- sent log ----------
LOG_HEADERS = ["sent_at", "email", "company", "subject", "result"]


def load_sent_log(path):
    p = Path(path)
    if not p.exists():
        return []
    with open(p, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def append_log(path, email, company, subject, result):
    new_file = not Path(path).exists()
    with open(path, "a", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if new_file:
            writer.writerow(LOG_HEADERS)
        writer.writerow([datetime.now().isoformat(timespec="seconds"), email, company, subject, result])


def sent_today(log_rows):
    today = date.today().isoformat()
    return sum(1 for r in log_rows if r["result"] == "sent" and r["sent_at"].startswith(today))


# ---------- message ----------
def first_name(full_name):
    return (full_name or "").strip().split(" ")[0] if full_name else ""


def build_text(cfg, draft):
    sender = cfg["sender"]
    greeting = f"Hi {first_name(draft['name'])}," if first_name(draft["name"]) else "Hi,"
    return (
        f"{greeting}\n\n{draft['body'].strip()}\n\nBest regards,\n{sender['name']}\n{sender['company']}\n\n"
        f"--\nIf you'd rather not hear from me, just reply with \"unsubscribe\" and I won't email you again.\n"
        f"{sender['company']}, {sender['postal_address']}"
    )


def build_message(cfg, draft):
    sender = cfg["sender"]
    msg = EmailMessage()
    msg["From"] = f"{sender['name']} <{sender['email']}>"
    msg["To"] = draft["email"]
    msg["Subject"] = draft["subject"]
    msg["Reply-To"] = sender.get("reply_to") or sender["email"]
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender["email"].split("@")[-1])
    msg["List-Unsubscribe"] = f"<mailto:{sender.get('reply_to') or sender['email']}?subject=unsubscribe>"
    msg.set_content(build_text(cfg, draft))
    return msg


# ---------- SMTP ----------
def open_smtp(cfg):
    scfg = cfg["smtp"]
    password = os.environ.get(scfg["password_env"], "")
    if not password:
        raise RuntimeError(f"Password not found. Put {scfg['password_env']}=... in the .env file.")
    context = ssl.create_default_context()
    if scfg["security"] == "ssl":
        server = smtplib.SMTP_SSL(scfg["host"], scfg["port"], context=context, timeout=30)
    else:
        server = smtplib.SMTP(scfg["host"], scfg["port"], timeout=30)
        server.starttls(context=context)
    server.login(scfg["username"], password)
    return server
