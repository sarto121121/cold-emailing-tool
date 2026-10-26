"""Local cold-email assistant.

  python main.py check            test Ollama + email login
  python main.py draft            read leads.xlsx, visit websites, write drafts.xlsx
  python main.py send             DRY RUN: show what would be sent (status = approved)
  python main.py send --live      really send approved drafts
  python main.py unsub a@b.com    add an address to the do-not-contact list
"""
import argparse
import os
import random
import smtplib
import sys
import time
import tomllib
from datetime import datetime
from pathlib import Path

import scraper
import sender
import sheets
import writer

BASE_DIR = Path(__file__).resolve().parent


def load_config():
    with open(BASE_DIR / "config.toml", "rb") as fh:
        return tomllib.load(fh)


def read_drafts(path):
    if not Path(path).exists():
        return []
    rows = sheets.read_rows(path)
    return [{h: row.get(h, "") for h in sheets.DRAFT_HEADERS} for row in rows]


# ---------------------------------------------------------------- check
def cmd_check(cfg):
    ok = True
    if cfg["sender"]["email"] == "you@example.com" or cfg["sender"]["name"] == "Your Name":
        print("[!] config.toml still has the default sender details. Edit [sender] and [offer].")
        ok = False
    try:
        writer.check_ollama(cfg)
        print(f"[ok] Ollama is running and model '{cfg['ollama']['model']}' is installed.")
    except writer.WriterError as exc:
        print(f"[x] {exc}")
        ok = False
    try:
        server = sender.open_smtp(cfg)
        server.quit()
        print(f"[ok] Logged in to {cfg['smtp']['host']} as {cfg['smtp']['username']}.")
    except Exception as exc:
        print(f"[x] Email login failed: {exc}")
        ok = False
    return 0 if ok else 1


# ---------------------------------------------------------------- draft
def cmd_draft(cfg, limit):
    files = cfg["files"]
    if not Path(files["leads"]).exists():
        print(f"{files['leads']} not found. Create it with columns: name, email, company, website.")
        return 1
    writer.check_ollama(cfg)

    leads = sheets.read_rows(files["leads"])
    drafts = read_drafts(files["drafts"])
    done = {d["email"].lower() for d in drafts}
    todo, seen = [], set()
    for row in leads:
        email = sheets.pick(row, "email").lower()
        if email in done or email in seen:
            continue
        seen.add(email)
        todo.append(row)
    if limit:
        todo = todo[:limit]
    if not todo:
        print("Nothing new to draft.")
        return 0

    scfg = cfg["scraper"]
    for i, row in enumerate(todo, start=1):
        lead = {k: sheets.pick(row, k) for k in ("email", "name", "company", "website")}
        label = lead["company"] or lead["email"]
        print(f"[{i}/{len(todo)}] {label} ... ", end="", flush=True)
        draft = {**{h: "" for h in sheets.DRAFT_HEADERS}, **lead}

        if not sender.is_valid_email(lead["email"]):
            draft.update(status="skip", notes="invalid email address")
            print("skipped (invalid email)")
        else:
            site = scraper.scrape(lead["website"], scfg["max_pages"], scfg["max_chars"],
                                  scfg["timeout_seconds"])
            try:
                subject, body, notes = writer.generate(cfg, lead, site)
                draft.update(subject=subject, body=body, notes=notes, status="review")
                print("drafted" + ("  (check notes)" if notes else ""))
            except writer.OllamaDown:
                raise
            except writer.WriterError as exc:
                draft.update(status="skip", notes=f"AI failed: {exc}")
                print(f"failed ({exc})")
        drafts.append(draft)
        sheets.write_drafts(files["drafts"], drafts)  # save after every lead so a crash loses nothing

    print(f"\nDone. Open {files['drafts']}, read each email, edit if needed and set status to 'approved'.")
    return 0


# ---------------------------------------------------------------- send
def cmd_send(cfg, live, limit, assume_yes):
    files, lim = cfg["files"], cfg["limits"]
    if not Path(files["drafts"]).exists():
        print("No drafts yet. Run: python main.py draft")
        return 1
    drafts = read_drafts(files["drafts"])
    unsub = sender.load_unsubscribed(files["unsubscribe"])
    log = sender.load_sent_log(files["sent_log"])
    already_sent = {r["email"].lower() for r in log if r["result"] == "sent"}

    queue = []
    for d in drafts:
        if d["status"].lower() != "approved":
            continue
        email = d["email"].lower()
        if email in unsub:
            d["status"], d["notes"] = "unsubscribed", "on do-not-contact list"
        elif email in already_sent:
            d["status"], d["notes"] = "sent", "already emailed before (see sent_log.csv)"
        elif not sender.is_valid_email(email) or not d["subject"] or not d["body"]:
            d["status"], d["notes"] = "failed", "invalid email or empty subject/body"
        else:
            queue.append(d)

    remaining_today = max(lim["daily_limit"] - sender.sent_today(log), 0)
    cap = min(remaining_today, limit) if limit else remaining_today
    skipped_by_cap = max(len(queue) - cap, 0)
    queue = queue[:cap]
    print(f"Approved & ready: {len(queue)}  |  daily limit left: {remaining_today}"
          + (f"  |  {skipped_by_cap} wait for tomorrow" if skipped_by_cap else ""))
    if not queue:
        sheets.write_drafts(files["drafts"], drafts)
        return 0

    if not live:
        print("\nDRY RUN: nothing is sent. Add --live to send for real.\n")
        for d in queue:
            print("=" * 70)
            print(f"To:      {d['email']}\nSubject: {d['subject']}\n")
            print(sender.build_text(cfg, d))
        return 0

    if not assume_yes and input(f"\nSend {len(queue)} real email(s)? Type YES to continue: ").strip() != "YES":
        print("Cancelled.")
        return 0

    server = sender.open_smtp(cfg)
    try:
        for i, d in enumerate(queue, start=1):
            msg = sender.build_message(cfg, d)
            try:
                try:
                    server.send_message(msg)
                except smtplib.SMTPServerDisconnected:
                    server = sender.open_smtp(cfg)
                    server.send_message(msg)
                d["status"], d["sent_at"], d["notes"] = "sent", datetime.now().strftime("%Y-%m-%d %H:%M"), ""
                sender.append_log(files["sent_log"], d["email"], d["company"], d["subject"], "sent")
                print(f"[{i}/{len(queue)}] sent -> {d['email']}")
            except smtplib.SMTPAuthenticationError:
                print("Login was rejected by the email server. Stopping.")
                return 1
            except (smtplib.SMTPException, OSError) as exc:
                d["status"], d["notes"] = "failed", f"{exc.__class__.__name__}: {exc}"[:200]
                sender.append_log(files["sent_log"], d["email"], d["company"], d["subject"], "failed")
                print(f"[{i}/{len(queue)}] FAILED -> {d['email']} ({exc.__class__.__name__})")
            sheets.write_drafts(files["drafts"], drafts)
            if i < len(queue):
                wait = random.uniform(lim["min_delay_seconds"], lim["max_delay_seconds"])
                print(f"    waiting {wait:.0f}s ...")
                time.sleep(wait)
    except KeyboardInterrupt:
        print("\nStopped by user. Progress is saved.")
    finally:
        sheets.write_drafts(files["drafts"], drafts)
        try:
            server.quit()
        except Exception:
            pass
    return 0


# ---------------------------------------------------------------- unsub
def cmd_unsub(cfg, address):
    sender.add_unsubscribed(cfg["files"]["unsubscribe"], address)
    print(f"Added {address.lower()} to {cfg['files']['unsubscribe']}. They will be skipped from now on.")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Local AI cold-email assistant")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="test Ollama and email login")
    p_draft = sub.add_parser("draft", help="write drafts for new leads")
    p_draft.add_argument("--limit", type=int, default=0, help="only draft the first N new leads")
    p_send = sub.add_parser("send", help="send approved drafts (dry run unless --live)")
    p_send.add_argument("--live", action="store_true", help="really send emails")
    p_send.add_argument("--limit", type=int, default=0, help="send at most N this run")
    p_send.add_argument("--yes", action="store_true", help="skip the YES confirmation")
    p_unsub = sub.add_parser("unsub", help="add an address to the do-not-contact list")
    p_unsub.add_argument("address")
    args = parser.parse_args()

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    os.chdir(BASE_DIR)
    sender.load_env(BASE_DIR / ".env")
    cfg = load_config()
    try:
        if args.command == "check":
            return cmd_check(cfg)
        if args.command == "draft":
            return cmd_draft(cfg, args.limit)
        if args.command == "send":
            return cmd_send(cfg, args.live, args.limit, args.yes)
        return cmd_unsub(cfg, args.address)
    except (writer.OllamaDown, sheets.FileLocked, RuntimeError) as exc:
        print(f"\nError: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
