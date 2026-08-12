from __future__ import annotations

import hashlib
import html
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlparse

import feedparser
import requests
import yaml
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

ROOT = Path(__file__).resolve().parent
CONFIG = yaml.safe_load((ROOT / "config.yml").read_text(encoding="utf-8"))
OUT = ROOT / "docs" / "feed.xml"
UA = "FiscoContabilitaRSS/1.0 (+personal RSS aggregator; official sources only)"
TIMEOUT = 25

@dataclass
class Item:
    title: str
    link: str
    summary: str
    source: str
    published: datetime
    score: int = 0
    category: str = "Fisco"


def get(url: str) -> requests.Response:
    r = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": UA, "Accept": "text/html,application/rss+xml,application/xml;q=0.9,*/*;q=0.8"})
    r.raise_for_status()
    return r


def norm_text(s: str) -> str:
    s = BeautifulSoup(s or "", "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", s).strip()


def parse_date(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        d = dateparser.parse(value)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def parse_feed(url: str, source_name: str) -> list[Item]:
    r = get(url)
    parsed = feedparser.parse(r.content)
    out: list[Item] = []
    for e in parsed.entries:
        title = norm_text(getattr(e, "title", ""))
        link = getattr(e, "link", "")
        summary = norm_text(getattr(e, "summary", "") or getattr(e, "description", ""))
        published = parse_date(getattr(e, "published", None) or getattr(e, "updated", None))
        if title and link:
            out.append(Item(title, link, summary, source_name, published))
    return out


def discover_rss(page_url: str) -> list[str]:
    r = get(page_url)
    soup = BeautifulSoup(r.text, "html.parser")
    feeds: list[str] = []
    for tag in soup.find_all(["link", "a"], href=True):
        href = tag.get("href", "")
        typ = (tag.get("type") or "").lower()
        txt = (tag.get_text(" ", strip=True) + " " + str(tag.get("title") or "")).lower()
        if "rss" in typ or "atom" in typ or "rss" in href.lower() or "feed" in href.lower() or "rss" in txt:
            u = urljoin(page_url, href)
            if urlparse(u).scheme in {"http", "https"} and u not in feeds and not u.lower().endswith(('.png','.jpg','.gif','.svg')):
                feeds.append(u)
    return feeds[:12]


def scrape_listing(url: str, source_name: str) -> list[Item]:
    """Fallback generico: estrae link notizia/atto dalla pagina elenco senza seguire pagine profonde."""
    r = get(url)
    soup = BeautifulSoup(r.text, "html.parser")
    host = urlparse(url).netloc
    out: list[Item] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        title = norm_text(a.get_text(" ", strip=True))
        if len(title) < 18:
            continue
        link = urljoin(url, a["href"])
        p = urlparse(link)
        if p.netloc != host or link in seen:
            continue
        # Evita menu, privacy, contatti, social ecc.
        low = (title + " " + link).lower()
        if any(x in low for x in ["privacy", "cookie", "contatti", "accessibil", "facebook", "linkedin", "youtube", "instagram", "javascript:"]):
            continue
        seen.add(link)
        parent = a.find_parent(["article", "li", "div", "tr"])
        summary = norm_text(parent.get_text(" ", strip=True) if parent else title)[:1000]
        # Cerca una data italiana/ISO nel contesto.
        m = re.search(r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}\s+(?:gennaio|febbraio|marzo|aprile|maggio|giugno|luglio|agosto|settembre|ottobre|novembre|dicembre)\s+\d{4})\b", summary, re.I)
        published = parse_date(m.group(1) if m else None)
        out.append(Item(title, link, summary, source_name, published))
    return out[:150]


def collect_source(src: dict) -> list[Item]:
    name = src["name"]
    items: list[Item] = []
    try:
        if src["type"] == "discover_rss":
            discovered = discover_rss(src["discovery_url"])
            for u in discovered:
                try:
                    got = parse_feed(u, name)
                    if got:
                        items.extend(got)
                except Exception as e:
                    print(f"[WARN] {name}: feed {u}: {e}", file=sys.stderr)
            if not items:
                items = scrape_listing(src["fallback_url"], name)
        elif src["type"] == "rss_or_html":
            for u in src["urls"]:
                try:
                    got = parse_feed(u, name)
                    if got:
                        items.extend(got)
                        break
                except Exception:
                    try:
                        got = scrape_listing(u, name)
                        if got:
                            items.extend(got)
                            break
                    except Exception as e:
                        print(f"[WARN] {name}: {u}: {e}", file=sys.stderr)
    except Exception as e:
        print(f"[WARN] source {name}: {e}", file=sys.stderr)
    return items


def classify(text: str) -> str:
    t = text.lower()
    groups = [
        ("IVA / Fatturazione", ["iva", "valore aggiunto", "fattur", "corrispettiv", "reverse charge", "split payment"]),
        ("Imposte dirette", ["irpef", "ires", "irap", "tuiR", "reddito d'impresa", "reddito di impresa", "forfet", "forfett"]),
        ("Dichiarazioni / Versamenti", ["730", "770", "certificazione unica", "dichiarazione", "f24", "versament", "ritenut", "compensaz"]),
        ("Crediti / Agevolazioni", ["credito d'imposta", "crediti d'imposta", "bonus", "detraz", "deduz", "agevolaz"]),
        ("Accertamento / Riscossione", ["accert", "riscoss", "cartell", "ravved", "sanzion", "contenzioso", "interpello", "codice tributo"]),
        ("Contabilità / OIC", ["oic", "principio contabile", "principi contabili", "contabil", "bilancio", "scritture contabili"]),
        ("Tributi", ["tribut", "fiscal", "registro", "bollo", "imu", "succession", "donaz"]),
    ]
    for name, keys in groups:
        if any(k.lower() in t for k in keys):
            return name
    return "Fisco"


def apply_filter(items: Iterable[Item]) -> list[Item]:
    fc = CONFIG["filter"]
    terms = {k.lower(): int(v) for k, v in fc["terms"].items()}
    neg = {k.lower(): int(v) for k, v in fc.get("negative_terms", {}).items()}
    always = set(fc.get("always_include_sources", []))
    min_score = int(fc["min_score"])
    out: list[Item] = []
    for it in items:
        text = f"{it.title} {it.summary}".lower()
        score = sum(weight for term, weight in terms.items() if term in text)
        score += sum(weight for term, weight in neg.items() if term in text)
        # Gazzetta: richiede un minimo reale di pertinenza, OIC: ammettiamo tutto ciò che è news OIC.
        if it.source in always and score < min_score:
            score = min_score
        it.score = score
        it.category = classify(text)
        if score >= min_score:
            out.append(it)
    return out


def dedupe(items: Iterable[Item]) -> list[Item]:
    seen: set[str] = set()
    out: list[Item] = []
    for it in sorted(items, key=lambda x: x.published, reverse=True):
        key = it.link.split("#", 1)[0].rstrip("/") or hashlib.sha1(it.title.encode()).hexdigest()
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def xml_escape(s: str) -> str:
    return html.escape(s or "", quote=True)


def build_rss(items: list[Item]) -> str:
    f = CONFIG["feed"]
    now = datetime.now(timezone.utc)
    chunks = ['<?xml version="1.0" encoding="UTF-8"?>', '<rss version="2.0">', '<channel>']
    chunks += [
        f"<title>{xml_escape(f['title'])}</title>",
        f"<link>{xml_escape(f['link'])}</link>",
        f"<description>{xml_escape(f['description'])}</description>",
        "<language>it-IT</language>",
        f"<lastBuildDate>{format_datetime(now)}</lastBuildDate>",
        "<ttl>60</ttl>",
    ]
    for it in items[: int(f.get("max_items", 250))]:
        guid = hashlib.sha256(it.link.encode()).hexdigest()
        desc = f"[{it.source}] [{it.category}] {it.summary}"[:3000]
        chunks += [
            "<item>",
            f"<title>{xml_escape('[' + it.source + '] ' + it.title)}</title>",
            f"<link>{xml_escape(it.link)}</link>",
            f'<guid isPermaLink="false">{guid}</guid>',
            f"<pubDate>{format_datetime(it.published)}</pubDate>",
            f"<category>{xml_escape(it.category)}</category>",
            f"<description>{xml_escape(desc)}</description>",
            "</item>",
        ]
    chunks += ["</channel>", "</rss>"]
    return "\n".join(chunks) + "\n"


def main() -> None:
    all_items: list[Item] = []
    for src in CONFIG["sources"]:
        got = collect_source(src)
        print(f"{src['name']}: {len(got)} elementi raccolti")
        all_items.extend(got)
    filtered = dedupe(apply_filter(all_items))
    print(f"Totale: {len(all_items)} | pertinenti: {len(filtered)}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build_rss(filtered), encoding="utf-8")
    print(f"Creato: {OUT}")

if __name__ == "__main__":
    main()
