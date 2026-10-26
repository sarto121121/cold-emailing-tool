"""Ask the local Ollama model to write a personalised email for one lead."""
import json
import re

import requests

SYSTEM_PROMPT = """You write short, honest, personalised cold outreach emails for a real person.

Rules:
- Use ONLY facts that appear in the WEBSITE TEXT. Never invent clients, awards, numbers, locations or claims.
- Mention ONE specific, true detail about the business so the reader sees you actually looked.
- If the website text is missing or thin, stay general and do not pretend you know details.
- No greeting line ("Hi ...") and no sign-off or signature: they are added automatically.
- No hype, no fake urgency, no emojis, no ALL CAPS, no "I hope this finds you well".
- Explain the sender's offer in one or two sentences, tied to this business's situation.
- End with the requested call to action as a soft question.
- Write in {language}. Tone: {tone}. Maximum {max_words} words for the body.
- Reply with JSON only: {{"subject": "...", "body": "..."}}
- The subject is under 8 words, specific, and not clickbait."""

USER_PROMPT = """SENDER
Name: {sender_name}
Company: {sender_company}
What the sender offers:
{offer}
Call to action: {cta}

LEAD
Business: {company}
Contact name: {contact}
Website title: {title}
Website description: {description}

WEBSITE TEXT:
{text}

Write the email now."""

PLACEHOLDER = re.compile(r"\[[^\]]{1,40}\]|\{[^}]{1,40}\}")


class WriterError(Exception):
    """The model gave unusable output for one lead; the run can continue."""


class OllamaDown(WriterError):
    """Ollama is unreachable or the model is missing; the whole run should stop."""


def _call_ollama(cfg, system, user):
    ocfg = cfg["ollama"]
    payload = {
        "model": ocfg["model"],
        "stream": False,
        "format": "json",
        "options": {"temperature": ocfg.get("temperature", 0.7)},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    try:
        resp = requests.post(f"{ocfg['host']}/api/chat", json=payload,
                             timeout=ocfg.get("timeout_seconds", 240))
    except requests.ConnectionError as exc:
        raise OllamaDown("Cannot reach Ollama. Is it running? (start the Ollama app or run `ollama serve`)") from exc
    except requests.Timeout as exc:
        raise WriterError("model took too long to answer") from exc
    if resp.status_code == 404:
        raise OllamaDown(f"Model '{ocfg['model']}' not found. Run: ollama pull {ocfg['model']}")
    resp.raise_for_status()
    return resp.json()["message"]["content"]


def check_ollama(cfg):
    """Raise OllamaDown unless Ollama is up and the configured model is installed."""
    ocfg = cfg["ollama"]
    try:
        tags = requests.get(f"{ocfg['host']}/api/tags", timeout=10).json()
    except (requests.RequestException, ValueError) as exc:
        raise OllamaDown("Cannot reach Ollama at " + ocfg["host"]) from exc
    names = [m["name"] for m in tags.get("models", [])]
    wanted = ocfg["model"]
    if not any(n == wanted or n.startswith(wanted + ":") for n in names):
        raise OllamaDown(f"Model '{wanted}' is not installed. Run: ollama pull {wanted}")


def generate(cfg, lead, site, retries=2):
    """Return (subject, body, notes). Raises WriterError if the model never gives usable output."""
    offer, sender = cfg["offer"], cfg["sender"]
    system = SYSTEM_PROMPT.format(language=offer["language"], tone=offer["tone"],
                                  max_words=offer["max_words"])
    user = USER_PROMPT.format(
        sender_name=sender["name"], sender_company=sender["company"],
        offer=offer["description"].strip(), cta=offer["call_to_action"],
        company=lead["company"] or "(unknown)", contact=lead["name"] or "(unknown)",
        title=site.get("title") or "(none)", description=site.get("description") or "(none)",
        text=site.get("text") or "(website text unavailable)")

    last_error = "unknown"
    for _ in range(retries + 1):
        raw = _call_ollama(cfg, system, user)
        try:
            data = json.loads(raw)
            subject, body = str(data["subject"]).strip(), str(data["body"]).strip()
        except (ValueError, KeyError, TypeError):
            last_error = "model did not return valid JSON"
            continue
        if not subject or len(body.split()) < 15:
            last_error = "model returned an empty or too short email"
            continue
        notes = []
        if not site.get("ok"):
            notes.append(f"WEBSITE NOT READ ({site.get('error')}): email is generic, check it")
        if PLACEHOLDER.search(subject + " " + body):
            notes.append("contains a [placeholder]: fix before approving")
        if len(body.split()) > offer["max_words"] * 1.4:
            notes.append("longer than requested")
        return subject, body, "; ".join(notes)
    raise WriterError(last_error)
