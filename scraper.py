"""Fetch a lead's website and return a compact text summary of what the business does."""
import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; OutreachResearch/1.0)"}
KEY_WORDS = ("about", "service", "product", "solution", "what-we-do", "who-we-are",
             "company", "menu", "treatment", "offer", "work")
NOISE_TAGS = ("script", "style", "noscript", "svg", "nav", "footer", "form", "iframe")


def normalize_url(url):
    url = (url or "").strip()
    if not url:
        return ""
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    return url


def _fetch(url, timeout):
    resp = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
    resp.raise_for_status()
    if "html" not in resp.headers.get("content-type", "").lower():
        raise ValueError("not an HTML page")
    return resp.text, resp.url


def _fetch_with_fallback(url, timeout):
    try:
        return _fetch(url, timeout)
    except requests.RequestException:
        if url.startswith("https://"):
            return _fetch("http://" + url[len("https://"):], timeout)
        raise


def _page_text(soup):
    for tag in soup(NOISE_TAGS):
        tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))


def _meta(soup, attr, value):
    tag = soup.find("meta", attrs={attr: value})
    return (tag.get("content") or "").strip() if tag else ""


def _candidate_links(soup, base_url):
    host = urlparse(base_url).netloc
    seen, links = {base_url.rstrip("/")}, []
    for a in soup.find_all("a", href=True):
        full = urljoin(base_url, a["href"]).split("#")[0]
        if urlparse(full).netloc != host or full.rstrip("/") in seen:
            continue
        haystack = (a["href"] + " " + a.get_text(" ", strip=True)).lower()
        if any(word in haystack for word in KEY_WORDS):
            seen.add(full.rstrip("/"))
            links.append(full)
    return links


def scrape(url, max_pages=3, max_chars=4000, timeout=15):
    """Return {"ok", "title", "description", "text", "error"}. Never raises."""
    result = {"ok": False, "title": "", "description": "", "text": "", "error": ""}
    url = normalize_url(url)
    if not url:
        result["error"] = "no website given"
        return result
    try:
        html, final_url = _fetch_with_fallback(url, timeout)
    except Exception as exc:  # network errors, bad status, non-html
        result["error"] = f"could not open website ({exc.__class__.__name__})"
        return result

    soup = BeautifulSoup(html, "html.parser")
    result["title"] = soup.title.get_text(strip=True) if soup.title else ""
    result["description"] = _meta(soup, "name", "description") or _meta(soup, "property", "og:description")
    links = _candidate_links(soup, final_url)[: max(max_pages - 1, 0)]
    chunks = [_page_text(soup)]

    for link in links:
        try:
            page_html, _ = _fetch_with_fallback(link, timeout)
            chunks.append(_page_text(BeautifulSoup(page_html, "html.parser")))
        except Exception:
            continue  # a missing sub-page is not a problem

    budget = max_chars // max(len(chunks), 1)
    result["text"] = "\n\n".join(chunk[:budget] for chunk in chunks if chunk)
    result["ok"] = len(result["text"]) > 150
    if not result["ok"]:
        result["error"] = "website had very little readable text (maybe JavaScript-only)"
    return result
