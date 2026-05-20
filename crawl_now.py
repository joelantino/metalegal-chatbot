"""
MetaLegal Deep Crawler - FINAL VERSION
- Uses Playwright page.inner_text() to get ALL visible text (no Trafilatura stripping)
- Splits content into granular paragraph-level sections for chatbot search
- Auto-resumes from already-saved pages
- Retries failed pages up to 3 times
- Restarts browser every 20 pages to prevent suspension
"""

import asyncio, hashlib, json, logging, os, re, sys, time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, urldefrag

TARGET_URL  = "https://www.metalegal.in"
MAX_PAGES   = 1000
CRAWL_DELAY = 2.0
TIMEOUT     = 45
MAX_DEPTH   = 10
PAGES_DIR   = Path("./data/pages")
MAX_RETRIES = 3
RESTART_EVERY = 20

PAGES_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("crawl.log", encoding="utf-8", mode="w"),
    ],
)
log = logging.getLogger("crawler")

try:
    import httpx
    from bs4 import BeautifulSoup
    from playwright.async_api import async_playwright
except ImportError as e:
    print(f"Missing: {e}\nRun: pip install playwright beautifulsoup4 httpx lxml")
    sys.exit(1)

SKIP_EXT = {".pdf",".jpg",".jpeg",".png",".gif",".svg",".webp",".zip",".mp4",
            ".mp3",".css",".js",".woff",".woff2",".ttf",".ico",".map"}

PAGE_TYPES = {
    "service": ["/service"], "blog": ["/blog","/post","/article","/news"],
    "faq": ["/faq","/help"], "contact": ["/contact"],
    "legal": ["/terms","/privacy","/disclaimer"], "about": ["/about"],
    "team": ["/team","/people","/attorney","/advocate"],
    "practice": ["/practice","/expertise"],
    "careers": ["/career","/join"],
}

def detect_type(url):
    path = urlparse(url).path.lower()
    for t, pats in PAGE_TYPES.items():
        if any(p in path for p in pats): return t
    return "general" if path.count("/") > 1 else "home"

def slug(url):
    path = urlparse(url).path.strip("/")
    return re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-") or "home"

def pid(url):
    return "page_" + hashlib.md5(url.encode()).hexdigest()[:8]

def fname(s, p):
    return s[:70] + "_" + p + ".json"

def get_already_crawled():
    done = set()
    for f in PAGES_DIR.glob("*.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            if "url" in d: done.add(d["url"])
        except: pass
    return done

# ── Content extraction from raw innerText ─────────────────────────────────────

HEADING_PAT = re.compile(
    r"^("
    r"[IVX]{1,5}\.\s+\S.{2,100}"           # I. Background  II. Analysis
    r"|[0-9]{1,2}\.\s+[A-Z]\S.{2,100}"     # 1. Facts  2. Decision
    r"|[A-Z][A-Z ]{5,70}[A-Z]"             # ALL CAPS HEADING
    r"|(?:Introduction|Background|Facts|Issue|Analysis|Held|Ratio|"
       r"Order|Judgment|Decision|Conclusion|Summary|Overview|"
       r"Appeal Mechanism|Penalty|Adjudication|Investigation|"
       r"Complaint|Final Order|Observations?|Implications?)"
       r"[:\s]?$"                           # common legal section names
    r")$"
)

def build_sections(inner_text: str, title: str) -> dict:
    """
    Split the full visible text into searchable sections.
    Strategy:
      1. Remove the page title line if it appears at the top.
      2. Detect Roman-numeral / ALL-CAPS / legal-keyword headings as section boundaries.
      3. Everything else goes into the current section's content.
      4. If no headings found, split by paragraph (each \n\n or long \n).
    Each section value = full paragraph text under that heading.
    """
    sections = {}
    seen_keys = set()

    def ukey(raw):
        k = re.sub(r"[^a-z0-9]+", "_", raw.strip().lower())[:70].strip("_") or "section"
        base, i = k, 2
        while k in seen_keys:
            k = f"{base}_{i}"; i += 1
        seen_keys.add(k)
        return k

    # Clean: remove leading/trailing whitespace, collapse 3+ blank lines
    text = re.sub(r"\n{3,}", "\n\n", inner_text.strip())

    # ── Strip Wix navigation / footer boilerplate lines ──
    # These appear on every page and pollute the introduction section
    NOISE_EXACT = {
        "skip to main content", "top of page", "back to top",
        "home", "about us", "insights & resources", "insights and resources",
        "practices areas", "practice areas", "people", "careers", "contact",
        "all articles", "court rulings", "updates", "communique",
        "follow us", "share", "like", "read more", "view all",
        "privacy policy", "terms of use", "all rights reserved",
        "© metalegal advocates", "metalegal advocates",
        "disclaimer", "bar council of india",
        "authored by editorial team, metalegal advocates.",
        "the views expressed are personal and do not constitute legal opinion.",
        "min read", "cookie policy",
    }
    NOISE_PAT = re.compile(
        r"^("
        r"[0-9]+\s+min\s+read"           # "7 min read"
        r"|[A-Z][a-z]+ [0-9]+,? [0-9]{4}"  # "Apr 2, 2025"
        r"|Updated:.*"                    # "Updated: May 8, 2025"
        r"|[A-Z][a-z]+ [0-9]{4}"         # "Apr 2025"
        r"|\d{1,2}/\d{1,2}/\d{2,4}"      # dates
        r"|www\.\S+"                      # URLs
        r"|Tel:\s*\+?[\d\s\-()]+"         # phone numbers (keep addresses separately)
        r")$", re.I
    )

    lines = text.splitlines()
    cleaned = []
    for line in lines:
        l = line.strip()
        if not l:
            cleaned.append("")
            continue
        if l.lower() in NOISE_EXACT:
            continue
        if NOISE_PAT.match(l):
            continue
        if l == title.strip():
            continue
        cleaned.append(l)

    text = "\n".join(cleaned)
    # Collapse multiple blank lines again after cleanup
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Try heading-based splitting
    current_key = "introduction"
    buf = []
    found_headings = False

    for line in text.splitlines():
        l = line.strip()
        if not l:
            if buf: buf.append("")  # preserve paragraph break
            continue
        if HEADING_PAT.match(l) and len(l) < 120:
            # flush
            content = "\n".join(buf).strip()
            if content:
                sections[ukey(current_key)] = content
            current_key = l
            buf = []
            found_headings = True
        else:
            buf.append(l)

    content = "\n".join(buf).strip()
    if content:
        sections[ukey(current_key)] = content

    # Fallback: split into paragraphs if no headings
    if not found_headings or len(sections) <= 1:
        sections = {}
        seen_keys.clear()
        paras = [p.strip() for p in re.split(r"\n{2,}", text) if len(p.strip()) > 40]
        for para in paras[:80]:
            first = re.split(r"[.!?]", para)[0].strip()[:60]
            k = ukey(first or para[:40])
            sections[k] = para

    return sections

def extract_meta(soup):
    meta = {}
    for tag in soup.find_all("meta"):
        name = (tag.get("name") or tag.get("property") or "").lower()
        content = tag.get("content", "")
        if name and content:
            meta[name] = content
    return meta

def extract_headings(soup):
    out, seen = [], set()
    for tag in soup.find_all(["h1","h2","h3","h4","h5","h6"]):
        t = tag.get_text(strip=True)
        if t and len(t) > 2 and t not in seen:
            seen.add(t)
            out.append({"level": tag.name, "text": t})
    return out

def extract_links(soup, base, domain):
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:","tel:","#")): continue
        full, _ = urldefrag(urljoin(base, href))
        p = urlparse(full)
        if p.scheme in ("http","https") and p.netloc == domain:
            links.append(full)
    return list(set(links))

def extract_faqs(soup):
    faqs = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(script.string or "")
            if isinstance(d, dict) and d.get("@type") == "FAQPage":
                for item in d.get("mainEntity", []):
                    q = item.get("name","")
                    a = (item.get("acceptedAnswer") or {}).get("text","")
                    if q: faqs.append({"question": q, "answer": a[:1000]})
        except: pass
    return faqs[:30]

def extract_contact(soup, base):
    text = soup.get_text(" ", strip=True)
    emails = list(set(re.findall(r"[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}", text)))[:5]
    phones = list(set(re.findall(r"\+?\d[\d\s\-().]{7,}\d", text)))[:5]
    clinks = list(set(urljoin(base, a["href"]) for a in soup.find_all("a", href=True)
                      if "contact" in a["href"].lower()))[:3]
    return {"email": emails, "phone": phones, "contact_links": clinks}

def extract_tables(soup):
    tables = []
    for tbl in soup.find_all("table"):
        headers = [th.get_text(strip=True) for th in tbl.find_all("th")]
        rows = [[td.get_text(" ",strip=True) for td in tr.find_all(["td","th"])]
                for tr in tbl.find_all("tr")]
        rows = [r for r in rows if any(c for c in r)]
        if rows: tables.append({"headers": headers, "rows": rows[:30]})
    return tables[:5]

def get_keywords(text, title=""):
    STOP = {"a","an","the","is","are","was","were","be","been","have","has","had","do","does",
            "did","will","would","should","could","may","might","at","by","for","with","about",
            "to","from","in","on","of","and","or","but","so","if","this","that","it","its",
            "we","our","you","they","their","i","me","us","what","how","when","where","which",
            "who","all","some","not","no","also","can","get","very","more","most","than","then",
            "such","other","each","any","many","under","over","up","here","there","as","into",
            "through","before","after","out","just","per","via","must","both","own","same","too"}
    words = re.findall(r"[a-z]{3,}", (title + " " + text).lower())
    freq = {}
    for w in words:
        if w not in STOP: freq[w] = freq.get(w, 0) + 1
    return sorted(freq, key=lambda x: -freq[x])[:40]

def build_page_json(url, html, inner_text):
    soup = BeautifulSoup(html, "lxml")
    domain = urlparse(url).netloc
    meta = extract_meta(soup)

    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else ""
    h1_tag = soup.find("h1")
    h1 = h1_tag.get_text(strip=True) if h1_tag else title

    # Build sections from full visible innerText
    sections = build_sections(inner_text, title)

    # Full text = cleaned inner_text (strip nav boilerplate)
    full_text = "\n\n".join(sections.values())

    return {
        "page_id":          pid(url),
        "url":              url,
        "slug":             slug(url),
        "title":            title,
        "h1":               h1,
        "meta_description": meta.get("description", ""),
        "meta_keywords":    meta.get("keywords", ""),
        "og_title":         meta.get("og:title", ""),
        "og_description":   meta.get("og:description", ""),
        "author":           meta.get("author", ""),
        "published_time":   meta.get("article:published_time", ""),
        "modified_time":    meta.get("article:modified_time", ""),
        "page_type":        detect_type(url),
        "headings":         extract_headings(soup),
        "content_sections": sections,
        "full_text":        full_text,
        "faq":              extract_faqs(soup),
        "tables":           extract_tables(soup),
        "keywords":         get_keywords(full_text, title),
        "contact_info":     extract_contact(soup, url),
        "internal_links":   [l for l in extract_links(soup, url, domain) if l != url][:60],
        "last_crawled":     datetime.now(timezone.utc).isoformat(),
        "word_count":       len(full_text.split()),
        "section_count":    len(sections),
        "content_hash":     hashlib.sha256(full_text.encode()).hexdigest(),
    }

# ── Sitemap ───────────────────────────────────────────────────────────────────

async def fetch_sitemap(base, domain):
    urls = []
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        try:
            r = await client.get(f"{base}/sitemap.xml")
            if r.status_code == 200 and "<loc>" in r.text:
                soup = BeautifulSoup(r.text, "lxml-xml")
                # nested sitemap index
                for nested in soup.find_all("sitemap"):
                    loc = nested.find("loc")
                    if loc:
                        try:
                            nr = await client.get(loc.get_text(strip=True))
                            if nr.status_code == 200:
                                nsoup = BeautifulSoup(nr.text, "lxml-xml")
                                for l in nsoup.find_all("loc"):
                                    u = l.get_text(strip=True)
                                    if urlparse(u).netloc == domain:
                                        urls.append(u)
                        except: pass
                for l in soup.find_all("loc"):
                    u = l.get_text(strip=True)
                    if urlparse(u).netloc == domain:
                        urls.append(u)
                log.info(f"Sitemap: {len(urls)} URLs found")
        except Exception as e:
            log.warning(f"Sitemap error: {e}")
    return list(set(urls))

# ── Page fetch ────────────────────────────────────────────────────────────────

async def fetch_page(context, url):
    for attempt in range(1, MAX_RETRIES + 1):
        page = await context.new_page()
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT * 1000)
            if resp and resp.status >= 400:
                await page.close()
                return None, None, f"HTTP {resp.status}"

            # Wait for JS to render
            try:
                await page.wait_for_load_state("networkidle", timeout=12000)
            except: pass

            # Scroll to trigger lazy loads
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(0.8)
            await page.evaluate("window.scrollTo(0, 0)")
            await asyncio.sleep(0.3)

            # Get BOTH full HTML (for meta/links) and innerText (for content)
            html = await page.content()
            inner_text = await page.inner_text("body")

            await page.close()
            return html, inner_text, None

        except Exception as e:
            await page.close()
            err = str(e)[:120]
            if attempt < MAX_RETRIES:
                log.warning(f"  [RETRY {attempt}/{MAX_RETRIES}] {err}")
                await asyncio.sleep(attempt * 3)
            else:
                log.warning(f"  [FAIL] {err}")
    return None, None, "Max retries exceeded"

# ── Main ──────────────────────────────────────────────────────────────────────

async def crawl():
    domain = urlparse(TARGET_URL).netloc
    already = get_already_crawled()
    if already:
        log.info(f"Resume: {len(already)} pages already saved")

    visited = set(already)
    queue   = deque()
    if TARGET_URL not in visited:
        queue.append((TARGET_URL, 0))

    saved   = len(already)
    skipped = 0
    errors  = 0
    start   = time.time()

    log.info("=" * 60)
    log.info(f"  MetaLegal Crawler FINAL - {TARGET_URL}")
    log.info(f"  Max: {MAX_PAGES} pages | Depth: {MAX_DEPTH} | Already: {len(already)}")
    log.info("=" * 60)

    sitemap_urls = await fetch_sitemap(TARGET_URL, domain)
    for u in sitemap_urls:
        u, _ = urldefrag(u)
        if u not in visited:
            queue.append((u, 1))
    log.info(f"Queue: {len(queue)} URLs seeded")

    pages_since_restart = 0

    async with async_playwright() as pw:
        async def new_browser():
            b = await pw.chromium.launch(headless=True, args=[
                "--no-sandbox","--disable-dev-shm-usage","--disable-gpu",
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
            ])
            c = await b.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 900},
                java_script_enabled=True,
                ignore_https_errors=True,
            )
            return b, c

        browser, ctx = await new_browser()

        try:
            while queue and saved < MAX_PAGES:
                url, depth = queue.popleft()
                url, _ = urldefrag(url)

                if url in visited: continue
                if depth > MAX_DEPTH: continue
                p = urlparse(url)
                if p.netloc != domain: continue
                if Path(p.path).suffix.lower() in SKIP_EXT: continue
                if p.scheme not in ("http","https"): continue

                # Strip trackers
                if any(x in url for x in ["?utm_","&utm_","?ref="]):
                    url = url.split("?")[0]
                    if url in visited: continue

                visited.add(url)
                log.info(f"[{saved+1:>3}/{MAX_PAGES}] depth={depth}  {url}")

                # Restart browser to avoid suspension
                if pages_since_restart >= RESTART_EVERY:
                    log.info(f"  [RESTART] browser restart")
                    await ctx.close(); await browser.close()
                    await asyncio.sleep(2)
                    browser, ctx = await new_browser()
                    pages_since_restart = 0

                html, inner_text, err = await fetch_page(ctx, url)
                pages_since_restart += 1

                if html is None:
                    errors += 1
                    continue

                # Skip pages with no real content
                if not inner_text or len(inner_text.strip()) < 100:
                    skipped += 1
                    continue

                try:
                    pjson = build_page_json(url, html, inner_text)
                except Exception as e:
                    log.warning(f"  [FAIL] build error: {e}")
                    errors += 1
                    continue

                # Skip if no content extracted
                if pjson["word_count"] < 30:
                    skipped += 1
                    continue

                # Save
                fpath = PAGES_DIR / fname(pjson["slug"], pjson["page_id"])
                fpath.write_text(json.dumps(pjson, indent=2, ensure_ascii=False), encoding="utf-8")
                saved += 1

                log.info(
                    f"  [OK] words={pjson['word_count']} "
                    f"| sections={pjson['section_count']} "
                    f"| links={len(pjson['internal_links'])} "
                    f"| type={pjson['page_type']}"
                )

                # Queue discovered links
                added = 0
                for link in pjson["internal_links"]:
                    link, _ = urldefrag(link)
                    if link not in visited:
                        queue.append((link, depth + 1))
                        added += 1
                if added:
                    log.debug(f"  + {added} new URLs (queue: {len(queue)})")

                await asyncio.sleep(CRAWL_DELAY)

        finally:
            await ctx.close()
            await browser.close()

    elapsed = round(time.time() - start, 1)
    new = saved - len(already)
    log.info("=" * 60)
    log.info("  CRAWL COMPLETE")
    log.info(f"  Total saved : {saved} ({new} new this run)")
    log.info(f"  Skipped     : {skipped}")
    log.info(f"  Errors      : {errors}")
    log.info(f"  Time        : {elapsed}s")
    log.info(f"  Output      : {PAGES_DIR.resolve()}")
    log.info("=" * 60)
    print(f"\n[DONE] {saved} pages ({new} new) -> {PAGES_DIR.resolve()}")
    print(f"Next step -> python build_kb.py")

if __name__ == "__main__":
    asyncio.run(crawl())
