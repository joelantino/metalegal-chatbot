"""
MetaLegal Crawler — Deep Website Crawl
Stack: Playwright + BeautifulSoup4 + Trafilatura

Crawls:
  - Sitemap.xml
  - All internal links recursively
  - JS-rendered pages (Playwright)
  - Extracts: title, meta, headings, content, FAQs, links, contact info

Output: Raw JSON per page → ./data/pages/
"""

import asyncio
import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse, urldefrag
from urllib.robotparser import RobotFileParser

import httpx
import trafilatura
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

from src.config import (
    TARGET_URL,
    MAX_PAGES,
    CRAWL_DELAY,
    CRAWL_TIMEOUT,
    MAX_DEPTH,
    RESPECT_ROBOTS,
    PAGES_JSON_DIR,
)

logger = logging.getLogger("crawler")

# ── Noise selectors to strip ──────────────────────────────────────────────────
STRIP_SELECTORS = [
    "script", "style", "noscript", "iframe",
    ".cookie-banner", "#cookie", ".popup", ".modal",
    ".ad", ".advertisement", "[data-ad]",
    "nav", "footer", ".footer", "#footer",
    ".header-nav", ".mobile-menu",
    ".social-links", ".social-icons",
]

# ── Page type detection rules ─────────────────────────────────────────────────
PAGE_TYPE_RULES = {
    "service":  ["/services/", "/service/"],
    "blog":     ["/blog/", "/post/", "/article/", "/news/"],
    "faq":      ["/faq", "/faqs", "/help"],
    "contact":  ["/contact", "/reach-us", "/get-in-touch"],
    "legal":    ["/legal", "/terms", "/privacy", "/disclaimer"],
    "industry": ["/industry/", "/sector/"],
    "about":    ["/about"],
    "home":     ["/"],
}


def detect_page_type(url: str) -> str:
    path = urlparse(url).path.lower()
    for ptype, patterns in PAGE_TYPE_RULES.items():
        if any(p in path for p in patterns):
            return ptype
    return "general"


def url_to_slug(url: str) -> str:
    path = urlparse(url).path.strip("/")
    slug = re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-")
    return slug or "home"


def extract_contact_info(soup: BeautifulSoup, base_url: str) -> dict:
    """Extract any email, phone number, contact page links."""
    text = soup.get_text(" ", strip=True)
    emails = re.findall(r"[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}", text)
    phones = re.findall(r"\+?\d[\d\s\-().]{7,}\d", text)
    contact_links = [
        urljoin(base_url, a["href"])
        for a in soup.find_all("a", href=True)
        if "contact" in a["href"].lower() or "reach" in a["href"].lower()
    ]
    return {
        "email": list(set(emails))[:3],
        "phone": list(set(phones))[:3],
        "contact_page": list(set(contact_links))[:2],
    }


def extract_breadcrumbs(soup: BeautifulSoup) -> list[str]:
    """Extract breadcrumb navigation."""
    crumbs = []
    # Try schema.org breadcrumb
    bc = soup.find(attrs={"itemtype": re.compile("BreadcrumbList", re.I)})
    if bc:
        items = bc.find_all(attrs={"itemprop": "name"})
        crumbs = [i.get_text(strip=True) for i in items if i.get_text(strip=True)]
    # Try aria-label breadcrumb
    if not crumbs:
        nav = soup.find("nav", attrs={"aria-label": re.compile("breadcrumb", re.I)})
        if nav:
            crumbs = [a.get_text(strip=True) for a in nav.find_all("a") if a.get_text(strip=True)]
    return crumbs


def extract_headings(soup: BeautifulSoup) -> list[dict]:
    """Extract all headings h1–h4."""
    headings = []
    for tag in soup.find_all(["h1", "h2", "h3", "h4"]):
        txt = tag.get_text(strip=True)
        if txt and len(txt) > 2:
            headings.append({"level": tag.name, "text": txt})
    return headings


def extract_faqs(soup: BeautifulSoup) -> list[dict]:
    """Extract FAQ pairs from structured markup or heuristic patterns."""
    faqs = []
    # Schema.org FAQPage
    for item in soup.find_all(attrs={"itemtype": re.compile("Question", re.I)}):
        q_el = item.find(attrs={"itemprop": "name"})
        a_el = item.find(attrs={"itemprop": "acceptedAnswer"})
        if q_el and a_el:
            faqs.append({
                "question": q_el.get_text(strip=True),
                "answer": a_el.get_text(" ", strip=True)[:500],
            })
    # Accordion / details-summary patterns
    if not faqs:
        for details in soup.find_all("details"):
            summary = details.find("summary")
            if summary:
                answer_txt = details.get_text(" ", strip=True).replace(
                    summary.get_text(strip=True), "", 1
                ).strip()
                faqs.append({
                    "question": summary.get_text(strip=True),
                    "answer": answer_txt[:500],
                })
    return faqs[:20]


def extract_keywords_from_text(text: str, title: str = "") -> list[str]:
    """Simple keyword extraction: high-freq meaningful words."""
    stop = {
        "a","an","the","is","are","was","were","be","been","being","have","has","had",
        "do","does","did","will","would","should","could","may","might","shall",
        "at","by","for","with","about","against","between","into","through",
        "during","before","after","above","below","to","from","up","down","in","out",
        "on","off","over","under","again","further","then","once","of","and","or",
        "but","so","if","this","that","these","those","it","its","we","our","your",
        "you","he","she","they","their","i","my","me","us","what","how","when","where",
        "which","who","all","both","each","few","more","most","other","some","such",
    }
    words = re.findall(r"[a-z]{3,}", (title + " " + text).lower())
    freq: dict[str, int] = {}
    for w in words:
        if w not in stop:
            freq[w] = freq.get(w, 0) + 1
    sorted_kw = sorted(freq, key=lambda x: -freq[x])
    return sorted_kw[:30]


def clean_html(html: str, base_url: str) -> BeautifulSoup:
    """Parse HTML and strip all noise elements."""
    soup = BeautifulSoup(html, "lxml")
    for sel in STRIP_SELECTORS:
        for el in soup.select(sel):
            el.decompose()
    return soup


def extract_internal_links(soup: BeautifulSoup, base_url: str, domain: str) -> list[str]:
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        full = urljoin(base_url, href)
        full, _ = urldefrag(full)
        parsed = urlparse(full)
        if parsed.scheme in ("http", "https") and parsed.netloc == domain:
            links.append(full)
    return list(set(links))


def build_page_json(
    page_id: str,
    url: str,
    html: str,
    clean_text: str,
) -> dict:
    """Construct the full page JSON from raw HTML."""
    soup = clean_html(html, url)
    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else ""
    meta_desc_tag = soup.find("meta", attrs={"name": re.compile("description", re.I)})
    meta_desc = meta_desc_tag.get("content", "").strip() if meta_desc_tag else ""
    h1 = soup.find("h1")
    h1_text = h1.get_text(strip=True) if h1 else title

    domain = urlparse(url).netloc
    internal_links = extract_internal_links(soup, url, domain)
    headings = extract_headings(soup)
    faqs = extract_faqs(soup)
    breadcrumbs = extract_breadcrumbs(soup)
    contact_info = extract_contact_info(soup, url)
    keywords = extract_keywords_from_text(clean_text, title)
    slug = url_to_slug(url)
    page_type = detect_page_type(url)

    # Section-based content split by h2 headings
    content_sections: dict[str, str] = {}
    current_section = "introduction"
    buffer: list[str] = []
    for el in soup.find_all(["h2", "h3", "p", "li", "td"]):
        if el.name in ("h2", "h3"):
            if buffer:
                content_sections[current_section] = " ".join(buffer).strip()
            current_section = re.sub(r"\s+", "_", el.get_text(strip=True).lower())[:40]
            buffer = []
        else:
            txt = el.get_text(" ", strip=True)
            if txt:
                buffer.append(txt)
    if buffer:
        content_sections[current_section] = " ".join(buffer).strip()

    content_hash = hashlib.sha256(clean_text.encode()).hexdigest()

    return {
        "page_id": page_id,
        "url": url,
        "slug": slug,
        "title": title,
        "h1": h1_text,
        "meta_description": meta_desc,
        "page_type": page_type,
        "breadcrumbs": breadcrumbs,
        "headings": headings,
        "content": content_sections,
        "full_text": clean_text,
        "faq": faqs,
        "keywords": keywords,
        "contact_info": contact_info,
        "internal_links": [lnk for lnk in internal_links if lnk != url][:30],
        "outbound_links": [],
        "last_crawled": datetime.now(timezone.utc).isoformat(),
        "language": "en",
        "content_hash": content_hash,
    }


class MetaLegalCrawler:
    """
    Async crawler using Playwright for JS rendering +
    Trafilatura for clean text extraction.
    """

    def __init__(self, target_url: str = TARGET_URL):
        self.target_url = target_url.rstrip("/")
        self.domain = urlparse(target_url).netloc
        self.visited: set[str] = set()
        self.queue: list[tuple[str, int]] = [(target_url, 0)]  # (url, depth)
        self.pages_saved = 0
        self.robot_parser = RobotFileParser()
        self._robot_loaded = False

    def _load_robots(self):
        robots_url = f"{self.target_url}/robots.txt"
        try:
            self.robot_parser.set_url(robots_url)
            self.robot_parser.read()
            self._robot_loaded = True
            logger.info("Loaded robots.txt")
        except Exception:
            logger.warning("Could not load robots.txt — proceeding without it")

    def _is_allowed(self, url: str) -> bool:
        if not RESPECT_ROBOTS or not self._robot_loaded:
            return True
        return self.robot_parser.can_fetch("*", url)

    def _is_crawlable(self, url: str) -> bool:
        parsed = urlparse(url)
        # Skip non-HTML resources
        skip_ext = {".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg",
                    ".zip", ".mp4", ".mp3", ".css", ".js", ".xml", ".json"}
        if any(parsed.path.lower().endswith(ext) for ext in skip_ext):
            return False
        return parsed.netloc == self.domain

    async def _extract_sitemap_urls(self) -> list[str]:
        """Parse sitemap.xml for additional URLs."""
        sitemap_url = f"{self.target_url}/sitemap.xml"
        urls = []
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(sitemap_url)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, "lxml-xml")
                    for loc in soup.find_all("loc"):
                        u = loc.get_text(strip=True)
                        if urlparse(u).netloc == self.domain:
                            urls.append(u)
            logger.info(f"Sitemap: found {len(urls)} URLs")
        except Exception as e:
            logger.warning(f"Sitemap error: {e}")
        return urls

    async def _render_page(self, context: BrowserContext, url: str) -> Optional[str]:
        """Use Playwright to render JS-heavy pages."""
        page: Page = await context.new_page()
        try:
            await page.goto(url, wait_until="networkidle", timeout=CRAWL_TIMEOUT * 1000)
            # Scroll to trigger lazy loading
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(0.8)
            html = await page.content()
            return html
        except Exception as e:
            logger.debug(f"Playwright error for {url}: {e}")
            return None
        finally:
            await page.close()

    def _generate_page_id(self, url: str) -> str:
        return "page_" + hashlib.md5(url.encode()).hexdigest()[:8]

    def _save_page(self, page_data: dict):
        slug = page_data["slug"] or "home"
        filename = PAGES_JSON_DIR / f"{slug[:80]}.json"
        # Avoid collisions
        if filename.exists():
            filename = PAGES_JSON_DIR / f"{slug[:70]}_{page_data['page_id']}.json"
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(page_data, f, indent=2, ensure_ascii=False)
        self.pages_saved += 1
        logger.info(f"[{self.pages_saved}] Saved: {page_data['url']}")

    async def crawl(self):
        """Main crawl entry point."""
        self._load_robots()
        sitemap_urls = await self._extract_sitemap_urls()
        for u in sitemap_urls:
            u, _ = urldefrag(u)
            if u not in self.visited:
                self.queue.append((u, 1))

        async with async_playwright() as pw:
            browser: Browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (compatible; MetaLegalBot/1.0; "
                    "+https://metalegal.in/bot)"
                ),
                java_script_enabled=True,
            )

            while self.queue and self.pages_saved < MAX_PAGES:
                url, depth = self.queue.pop(0)
                url, _ = urldefrag(url)

                if url in self.visited:
                    continue
                if depth > MAX_DEPTH:
                    continue
                if not self._is_crawlable(url):
                    continue
                if not self._is_allowed(url):
                    logger.debug(f"Blocked by robots: {url}")
                    continue

                self.visited.add(url)
                logger.info(f"Crawling [{depth}]: {url}")

                html = await self._render_page(context, url)
                if not html:
                    continue

                # Trafilatura clean text
                clean_text = trafilatura.extract(
                    html,
                    include_comments=False,
                    include_tables=True,
                    no_fallback=False,
                    favor_recall=True,
                ) or ""

                if len(clean_text.strip()) < 50:
                    logger.debug(f"Skipping low-content page: {url}")
                    continue

                page_id = self._generate_page_id(url)
                page_data = build_page_json(page_id, url, html, clean_text)
                self._save_page(page_data)

                # Enqueue new links
                soup = clean_html(html, url)
                new_links = extract_internal_links(soup, url, self.domain)
                for link in new_links:
                    link, _ = urldefrag(link)
                    if link not in self.visited:
                        self.queue.append((link, depth + 1))

                await asyncio.sleep(CRAWL_DELAY)

            await context.close()
            await browser.close()

        logger.info(f"Crawl complete. Total pages saved: {self.pages_saved}")
        return self.pages_saved


async def run_crawler():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )
    crawler = MetaLegalCrawler()
    total = await crawler.crawl()
    print(f"\n✅ Crawl finished. {total} pages saved to {PAGES_JSON_DIR}")


if __name__ == "__main__":
    asyncio.run(run_crawler())
