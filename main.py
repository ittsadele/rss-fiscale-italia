from __future__ import annotations

import hashlib
import html
import re
import sys
import time
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


# ============================================================
# CONFIGURAZIONE GENERALE
# ============================================================

ROOT = Path(__file__).resolve().parent
CONFIG = yaml.safe_load((ROOT / "config.yml").read_text(encoding="utf-8"))

DOCS_DIR = ROOT / "docs"
ARTICLES_DIR = DOCS_DIR / "articoli"
OUT = DOCS_DIR / "feed.xml"

PUBLIC_BASE = "https://ittsadele.github.io/rss-fiscale-italia"

UA = (
    "Mozilla/5.0 (compatible; FiscoContabilitaRSS/2.0; "
    "+https://ittsadele.github.io/rss-fiscale-italia/)"
)

TIMEOUT = 30

# Quanti caratteri massimi proviamo a estrarre dalla fonte.
MAX_SOURCE_TEXT = 14000

# Lunghezza indicativa della sintesi che comparirà nel reader.
MAX_SUMMARY_CHARS = 2800

# Evita di sovraccaricare i siti ufficiali.
REQUEST_DELAY = 0.15


# ============================================================
# MODELLI DATI
# ============================================================

@dataclass
class Item:
    title: str
    link: str
    summary: str
    source: str
    published: datetime

    score: int = 0
    category: str = "Fisco"

    full_text: str = ""
    rich_summary: str = ""
    local_url: str = ""
    local_filename: str = ""


# ============================================================
# HTTP
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": UA,
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/rss+xml,application/xml;q=0.9,*/*;q=0.8"
    ),
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.7",
})


def get(url: str) -> requests.Response:
    time.sleep(REQUEST_DELAY)

    r = SESSION.get(
        url,
        timeout=TIMEOUT,
        allow_redirects=True,
    )

    r.raise_for_status()
    return r


# ============================================================
# TESTO / DATE
# ============================================================

def norm_text(value: str) -> str:
    if not value:
        return ""

    text = BeautifulSoup(value, "html.parser").get_text(" ", strip=True)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def parse_date(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)

    try:
        d = dateparser.parse(value, dayfirst=True)

        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)

        return d.astimezone(timezone.utc)

    except Exception:
        return datetime.now(timezone.utc)


def italian_date(d: datetime) -> str:
    months = [
        "",
        "gennaio",
        "febbraio",
        "marzo",
        "aprile",
        "maggio",
        "giugno",
        "luglio",
        "agosto",
        "settembre",
        "ottobre",
        "novembre",
        "dicembre",
    ]

    return f"{d.day} {months[d.month]} {d.year}"


# ============================================================
# RACCOLTA RSS
# ============================================================

def parse_feed(url: str, source_name: str) -> list[Item]:
    r = get(url)
    parsed = feedparser.parse(r.content)

    out: list[Item] = []

    for e in parsed.entries:
        title = norm_text(getattr(e, "title", ""))
        link = getattr(e, "link", "")

        summary = norm_text(
            getattr(e, "summary", "")
            or getattr(e, "description", "")
        )

        published = parse_date(
            getattr(e, "published", None)
            or getattr(e, "updated", None)
        )

        if title and link:
            out.append(
                Item(
                    title=title,
                    link=link,
                    summary=summary,
                    source=source_name,
                    published=published,
                )
            )

    return out


def discover_rss(page_url: str) -> list[str]:
    r = get(page_url)

    soup = BeautifulSoup(r.text, "html.parser")

    feeds: list[str] = []

    for tag in soup.find_all(["link", "a"], href=True):
        href = tag.get("href", "")

        typ = (tag.get("type") or "").lower()

        txt = (
            tag.get_text(" ", strip=True)
            + " "
            + str(tag.get("title") or "")
        ).lower()

        if (
            "rss" in typ
            or "atom" in typ
            or "rss" in href.lower()
            or "feed" in href.lower()
            or "rss" in txt
        ):
            u = urljoin(page_url, href)

            if (
                urlparse(u).scheme in {"http", "https"}
                and u not in feeds
                and not u.lower().endswith(
                    (".png", ".jpg", ".gif", ".svg")
                )
            ):
                feeds.append(u)

    return feeds[:12]


# ============================================================
# FALLBACK PAGINE ELENCO
# ============================================================

def scrape_listing(url: str, source_name: str) -> list[Item]:
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

        low = (title + " " + link).lower()

        if any(
            x in low
            for x in [
                "privacy",
                "cookie",
                "contatti",
                "accessibil",
                "facebook",
                "linkedin",
                "youtube",
                "instagram",
                "javascript:",
            ]
        ):
            continue

        seen.add(link)

        parent = a.find_parent(
            ["article", "li", "div", "tr"]
        )

        summary = norm_text(
            parent.get_text(" ", strip=True)
            if parent
            else title
        )[:1200]

        m = re.search(
            r"\b("
            r"\d{1,2}[/-]\d{1,2}[/-]\d{4}"
            r"|"
            r"\d{4}-\d{2}-\d{2}"
            r"|"
            r"\d{1,2}\s+"
            r"(?:gennaio|febbraio|marzo|aprile|maggio|giugno|"
            r"luglio|agosto|settembre|ottobre|novembre|dicembre)"
            r"\s+\d{4}"
            r")\b",
            summary,
            re.I,
        )

        published = parse_date(
            m.group(1) if m else None
        )

        out.append(
            Item(
                title,
                link,
                summary,
                source_name,
                published,
            )
        )

    return out[:150]


# ============================================================
# RACCOLTA FONTI
# ============================================================

def collect_source(src: dict) -> list[Item]:
    name = src["name"]

    items: list[Item] = []

    try:
        if src["type"] == "discover_rss":
            discovered = discover_rss(
                src["discovery_url"]
            )

            for u in discovered:
                try:
                    got = parse_feed(u, name)

                    if got:
                        items.extend(got)

                except Exception as e:
                    print(
                        f"[WARN] {name}: feed {u}: {e}",
                        file=sys.stderr,
                    )

            if not items:
                items = scrape_listing(
                    src["fallback_url"],
                    name,
                )

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
                        print(
                            f"[WARN] {name}: {u}: {e}",
                            file=sys.stderr,
                        )

    except Exception as e:
        print(
            f"[WARN] source {name}: {e}",
            file=sys.stderr,
        )

    return items


# ============================================================
# CLASSIFICAZIONE
# ============================================================

def classify(text: str) -> str:
    t = text.lower()

    groups = [
        (
            "IVA / Fatturazione",
            [
                "iva",
                "valore aggiunto",
                "fattur",
                "corrispettiv",
                "reverse charge",
                "split payment",
            ],
        ),

        (
            "Imposte dirette",
            [
                "irpef",
                "ires",
                "irap",
                "tuir",
                "reddito d'impresa",
                "reddito di impresa",
                "forfet",
                "forfett",
            ],
        ),

        (
            "Dichiarazioni / Versamenti",
            [
                "730",
                "770",
                "certificazione unica",
                "dichiarazione",
                "f24",
                "versament",
                "ritenut",
                "compensaz",
            ],
        ),

        (
            "Crediti / Agevolazioni",
            [
                "credito d'imposta",
                "crediti d'imposta",
                "bonus",
                "detraz",
                "deduz",
                "agevolaz",
            ],
        ),

        (
            "Accertamento / Riscossione",
            [
                "accert",
                "riscoss",
                "cartell",
                "ravved",
                "sanzion",
                "contenzioso",
                "interpello",
                "codice tributo",
            ],
        ),

        (
            "Contabilità / OIC",
            [
                "oic",
                "principio contabile",
                "principi contabili",
                "contabil",
                "bilancio",
                "scritture contabili",
            ],
        ),

        (
            "Tributi",
            [
                "tribut",
                "fiscal",
                "registro",
                "bollo",
                "imu",
                "succession",
                "donaz",
            ],
        ),
    ]

    for name, keys in groups:
        if any(k in t for k in keys):
            return name

    return "Fisco"


# ============================================================
# FILTRO
# ============================================================

def apply_filter(
    items: Iterable[Item],
) -> list[Item]:

    fc = CONFIG["filter"]

    terms = {
        k.lower(): int(v)
        for k, v in fc["terms"].items()
    }

    neg = {
        k.lower(): int(v)
        for k, v in fc.get(
            "negative_terms",
            {},
        ).items()
    }

    always = set(
        fc.get(
            "always_include_sources",
            [],
        )
    )

    min_score = int(fc["min_score"])

    out: list[Item] = []

    for it in items:
        text = (
            f"{it.title} {it.summary}"
        ).lower()

        score = sum(
            weight
            for term, weight in terms.items()
            if term in text
        )

        score += sum(
            weight
            for term, weight in neg.items()
            if term in text
        )

        if (
            it.source in always
            and score < min_score
        ):
            score = min_score

        it.score = score
        it.category = classify(text)

        if score >= min_score:
            out.append(it)

    return out


# ============================================================
# DEDUPLICAZIONE
# ============================================================

def dedupe(
    items: Iterable[Item],
) -> list[Item]:

    seen: set[str] = set()
    out: list[Item] = []

    for it in sorted(
        items,
        key=lambda x: x.published,
        reverse=True,
    ):

        key = (
            it.link
            .split("#", 1)[0]
            .rstrip("/")
        )

        if not key:
            key = hashlib.sha1(
                it.title.encode()
            ).hexdigest()

        if key in seen:
            continue

        seen.add(key)
        out.append(it)

    return out


# ============================================================
# ESTRAZIONE ARTICOLO / PROVVEDIMENTO
# ============================================================

BOILERPLATE_PATTERNS = [
    "cookie",
    "privacy",
    "accessibilità",
    "accessibilita",
    "seguici su",
    "condividi",
    "facebook",
    "twitter",
    "linkedin",
    "instagram",
    "youtube",
    "torna indietro",
    "vai al contenuto",
    "menu principale",
    "navigazione",
    "cerca nel sito",
]


def clean_page(soup: BeautifulSoup) -> None:
    for tag in soup(
        [
            "script",
            "style",
            "noscript",
            "svg",
            "form",
            "nav",
            "footer",
            "header",
            "aside",
        ]
    ):
        tag.decompose()


def extract_main_text(url: str) -> str:
    try:
        r = get(url)

    except Exception as e:
        print(
            f"[WARN] apertura articolo {url}: {e}",
            file=sys.stderr,
        )
        return ""

    content_type = (
        r.headers
        .get("Content-Type", "")
        .lower()
    )

    if "pdf" in content_type:
        return ""

    try:
        soup = BeautifulSoup(
            r.text,
            "html.parser",
        )

    except Exception:
        return ""

    clean_page(soup)

    candidates = []

    selectors = [
        "article",
        "main",
        "[role='main']",
        ".article",
        ".news",
        ".content",
        ".contenuto",
        ".testo",
        ".entry-content",
        ".post-content",
        "#content",
        "#contenuto",
        "#main",
    ]

    for selector in selectors:
        for node in soup.select(selector):
            text = norm_text(
                node.get_text(
                    " ",
                    strip=True,
                )
            )

            if len(text) >= 250:
                candidates.append(text)

    if candidates:
        text = max(
            candidates,
            key=len,
        )

    else:
        body = soup.body or soup

        text = norm_text(
            body.get_text(
                " ",
                strip=True,
            )
        )

    if not text:
        return ""

    pieces = []

    for piece in re.split(
        r"(?<=[.!?;:])\s+",
        text,
    ):
        p = piece.strip()

        if len(p) < 25:
            continue

        low = p.lower()

        if any(
            x in low
            for x in BOILERPLATE_PATTERNS
        ):
            continue

        pieces.append(p)

    cleaned = " ".join(pieces)

    cleaned = re.sub(
        r"\s+",
        " ",
        cleaned,
    ).strip()

    return cleaned[:MAX_SOURCE_TEXT]


# ============================================================
# SINTESI SENZA AI
# ============================================================

def sentence_split(text: str) -> list[str]:
    text = re.sub(
        r"\s+",
        " ",
        text,
    ).strip()

    if not text:
        return []

    sentences = re.split(
        r"(?<=[.!?])\s+(?=[A-ZÀ-ÖØ-Ý0-9])",
        text,
    )

    return [
        s.strip()
        for s in sentences
        if len(s.strip()) >= 35
    ]


def important_keywords(
    item: Item,
) -> list[str]:

    base = [
        "iva",
        "irpef",
        "ires",
        "irap",
        "imposta",
        "imposte",
        "tribut",
        "fiscal",
        "fattur",
        "dichiar",
        "credito",
        "bonus",
        "agevol",
        "detraz",
        "deduz",
        "ritenut",
        "versament",
        "accert",
        "riscoss",
        "sanzion",
        "contabil",
        "bilancio",
        "oic",
        "reddito",
        "contribuent",
        "decorren",
        "entrata in vigore",
        "modifica",
        "disposizioni",
    ]

    if item.category == "IVA / Fatturazione":
        base += [
            "valore aggiunto",
            "fattura elettronica",
            "corrispettivi",
            "reverse charge",
        ]

    elif item.category == "Contabilità / OIC":
        base += [
            "principio contabile",
            "bilancio",
            "scritture",
        ]

    return base


def create_extract_summary(
    item: Item,
) -> str:

    source_text = (
        item.full_text
        or item.summary
        or item.title
    )

    sentences = sentence_split(
        source_text
    )

    if not sentences:
        return item.summary[:MAX_SUMMARY_CHARS]

    keys = important_keywords(item)

    ranked = []

    for index, sentence in enumerate(
        sentences
    ):
        low = sentence.lower()

        score = 0

        for keyword in keys:
            if keyword in low:
                score += 3

        # Le prime frasi sono spesso descrittive.
        if index < 4:
            score += 4

        elif index < 8:
            score += 2

        if re.search(
            r"\b"
            r"(entra in vigore|"
            r"decorre|"
            r"si applica|"
            r"modifica|"
            r"prevede|"
            r"stabilisce|"
            r"introduce|"
            r"abroga|"
            r"sostituisce)"
            r"\b",
            low,
        ):
            score += 4

        ranked.append(
            (
                score,
                index,
                sentence,
            )
        )

    # Selezioniamo le frasi più informative.
    selected = sorted(
        ranked,
        key=lambda x: (
            -x[0],
            x[1],
        ),
    )[:10]

    # Poi le rimettiamo nell'ordine originale.
    selected = sorted(
        selected,
        key=lambda x: x[1],
    )

    output = []

    total = 0

    for _, _, sentence in selected:
        if sentence in output:
            continue

        if (
            total + len(sentence)
            > MAX_SUMMARY_CHARS
        ):
            break

        output.append(sentence)
        total += len(sentence) + 1

    result = " ".join(output).strip()

    if not result:
        result = item.summary

    return result[:MAX_SUMMARY_CHARS]


# ============================================================
# TAG ARGOMENTI
# ============================================================

def detect_topics(
    item: Item,
) -> list[str]:

    text = (
        item.title
        + " "
        + item.summary
        + " "
        + item.full_text
    ).lower()

    mapping = [
        ("IVA", [" iva ", "valore aggiunto"]),
        (
            "Fatturazione elettronica",
            [
                "fattura elettronica",
                "fatturazione elettronica",
            ],
        ),
        ("IRPEF", ["irpef"]),
        ("IRES", ["ires"]),
        ("IRAP", ["irap"]),
        (
            "Dichiarazioni",
            [
                "dichiarazione",
                "modello redditi",
                "730",
                "770",
            ],
        ),
        (
            "F24",
            ["f24"],
        ),
        (
            "Ritenute",
            ["ritenut"],
        ),
        (
            "Crediti d'imposta",
            [
                "credito d'imposta",
                "crediti d'imposta",
            ],
        ),
        (
            "Agevolazioni",
            [
                "agevolaz",
                "bonus",
            ],
        ),
        (
            "Accertamento",
            ["accert"],
        ),
        (
            "Riscossione",
            [
                "riscoss",
                "cartell",
            ],
        ),
        (
            "Contenzioso tributario",
            [
                "contenzioso tributario",
                "processo tributario",
            ],
        ),
        (
            "Contabilità",
            ["contabil"],
        ),
        (
            "Bilancio",
            ["bilancio"],
        ),
        (
            "OIC",
            [
                "oic",
                "principio contabile",
            ],
        ),
        (
            "Imposta di registro",
            ["imposta di registro"],
        ),
        (
            "Imposta di bollo",
            ["imposta di bollo"],
        ),
        (
            "IMU",
            ["imu"],
        ),
    ]

    topics = []

    padded = f" {text} "

    for label, keys in mapping:
        if any(
            key in padded
            for key in keys
        ):
            topics.append(label)

    return topics[:8]


# ============================================================
# ARRICCHIMENTO
# ============================================================

def enrich_item(item: Item) -> None:
    item.full_text = extract_main_text(
        item.link
    )

    item.rich_summary = create_extract_summary(
        item
    )

    key = hashlib.sha256(
        item.link.encode("utf-8")
    ).hexdigest()[:20]

    item.local_filename = (
        f"{key}.html"
    )

    item.local_url = (
        f"{PUBLIC_BASE}/articoli/"
        f"{item.local_filename}"
    )


# ============================================================
# HTML ARTICOLO INTERMEDIO
# ============================================================

def html_escape(value: str) -> str:
    return html.escape(
        value or "",
        quote=True,
    )


def build_article_page(
    item: Item,
) -> str:

    topics = detect_topics(item)

    topics_html = ""

    if topics:
        topics_html = "".join(
            f"<span class='tag'>{html_escape(t)}</span>"
            for t in topics
        )

    paragraphs = []

    text = (
        item.rich_summary
        or item.summary
    )

    sentences = sentence_split(text)

    if sentences:
        current = []

        for sentence in sentences:
            current.append(sentence)

            if len(" ".join(current)) > 450:
                paragraphs.append(
                    " ".join(current)
                )
                current = []

        if current:
            paragraphs.append(
                " ".join(current)
            )

    else:
        paragraphs = [text]

    paragraphs_html = "".join(
        f"<p>{html_escape(p)}</p>"
        for p in paragraphs
        if p
    )

    return f"""<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html_escape(item.title)}</title>

<style>
body {{
    margin: 0;
    background: #f5f6f8;
    color: #202124;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
    line-height: 1.65;
}}

.container {{
    max-width: 820px;
    margin: 0 auto;
    padding: 28px 18px 60px;
}}

.card {{
    background: white;
    border-radius: 18px;
    padding: 30px;
    box-shadow: 0 4px 22px rgba(0,0,0,.07);
}}

.source {{
    font-size: 14px;
    font-weight: 700;
    color: #636b74;
    text-transform: uppercase;
    letter-spacing: .04em;
}}

h1 {{
    font-size: 30px;
    line-height: 1.2;
    margin: 10px 0 16px;
}}

.meta {{
    color: #687078;
    margin-bottom: 20px;
}}

.tags {{
    margin: 14px 0 26px;
}}

.tag {{
    display: inline-block;
    background: #eef1f4;
    padding: 5px 10px;
    margin: 3px 5px 3px 0;
    border-radius: 999px;
    font-size: 13px;
}}

h2 {{
    margin-top: 28px;
    font-size: 20px;
}}

p {{
    font-size: 17px;
}}

.button {{
    display: inline-block;
    margin-top: 28px;
    padding: 13px 18px;
    border-radius: 10px;
    background: #202124;
    color: white;
    text-decoration: none;
    font-weight: 700;
}}

.note {{
    margin-top: 30px;
    padding-top: 18px;
    border-top: 1px solid #e2e4e7;
    color: #737980;
    font-size: 13px;
}}
</style>

</head>
<body>

<div class="container">
<div class="card">

<div class="source">
{html_escape(item.source)}
</div>

<h1>
{html_escape(item.title)}
</h1>

<div class="meta">
{html_escape(item.category)}
&nbsp; • &nbsp;
{html_escape(italian_date(item.published))}
</div>

<div class="tags">
{topics_html}
</div>

<h2>In sintesi</h2>

{paragraphs_html}

<a
class="button"
href="{html_escape(item.link)}"
target="_blank"
rel="noopener"
>
Apri la fonte ufficiale →
</a>

<div class="note">
Questa pagina contiene una sintesi automatica estratta dalla fonte ufficiale.
Per valore giuridico, completezza e aggiornamenti fare sempre riferimento
al documento originale.
</div>

</div>
</div>

</body>
</html>
"""


def write_article_pages(
    items: list[Item],
) -> None:

    ARTICLES_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    for item in items:
        if not item.local_filename:
            continue

        path = (
            ARTICLES_DIR
            / item.local_filename
        )

        path.write_text(
            build_article_page(item),
            encoding="utf-8",
        )


# ============================================================
# RSS HTML
# ============================================================

def xml_escape(value: str) -> str:
    return html.escape(
        value or "",
        quote=True,
    )


def cdata(value: str) -> str:
    """
    Evita che una sequenza ']]>' rompa il CDATA.
    """
    return (
        value
        .replace(
            "]]>",
            "]]]]><![CDATA[>",
        )
    )


def build_reader_content(
    item: Item,
) -> str:

    topics = detect_topics(item)

    topic_html = ""

    if topics:
        topic_html = (
            "<p><strong>Argomenti:</strong> "
            + " · ".join(
                html_escape(x)
                for x in topics
            )
            + "</p>"
        )

    summary = html_escape(
        item.rich_summary
        or item.summary
    )

    return f"""
<div>

<p>
<strong>{html_escape(item.source)}</strong>
<br>
{html_escape(item.category)}
<br>
{html_escape(italian_date(item.published))}
</p>

{topic_html}

<h3>In sintesi</h3>

<p style="line-height:1.6">
{summary}
</p>

<p>
<a href="{html_escape(item.link)}">
<strong>Leggi il documento completo sulla fonte ufficiale →</strong>
</a>
</p>

<hr>

<p style="font-size:12px;color:#777">
Sintesi automatica ricavata dal contenuto disponibile sulla fonte ufficiale.
Per il testo completo e avente valore ufficiale consultare sempre il documento originale.
</p>

</div>
""".strip()


# ============================================================
# RSS
# ============================================================

def build_rss(
    items: list[Item],
) -> str:

    f = CONFIG["feed"]

    now = datetime.now(
        timezone.utc
    )

    feed_url = (
        f"{PUBLIC_BASE}/feed.xml"
    )

    chunks = [
        '<?xml version="1.0" encoding="UTF-8"?>',

        '<rss version="2.0" '
        'xmlns:content="http://purl.org/rss/1.0/modules/content/">',

        "<channel>",

        f"<title>{xml_escape(f['title'])}</title>",

        f"<link>{xml_escape(PUBLIC_BASE + '/')}</link>",

        f"<description>{xml_escape(f['description'])}</description>",

        "<language>it-IT</language>",

        f"<lastBuildDate>{format_datetime(now)}</lastBuildDate>",

        f"<generator>FiscoContabilitaRSS 2.0</generator>",

        "<ttl>60</ttl>",
    ]

    max_items = int(
        f.get(
            "max_items",
            250,
        )
    )

    for item in items[:max_items]:

        guid = hashlib.sha256(
            item.link.encode()
        ).hexdigest()

        reader_content = (
            build_reader_content(item)
        )

        short_description = (
            item.rich_summary
            or item.summary
        )[:1800]

        item_link = (
            item.local_url
            or item.link
        )

        chunks.extend([
            "<item>",

            f"<title>{xml_escape('[' + item.source + '] ' + item.title)}</title>",

            f"<link>{xml_escape(item_link)}</link>",

            (
                '<guid isPermaLink="false">'
                f"{guid}"
                "</guid>"
            ),

            f"<pubDate>{format_datetime(item.published)}</pubDate>",

            f"<category>{xml_escape(item.category)}</category>",

            (
                "<description><![CDATA["
                + cdata(
                    "<p><strong>"
                    + html_escape(item.source)
                    + "</strong> · "
                    + html_escape(item.category)
                    + "</p>"
                    + "<p>"
                    + html_escape(short_description)
                    + "</p>"
                    + "<p><a href=\""
                    + html_escape(item.link)
                    + "\">Leggi la fonte ufficiale →</a></p>"
                )
                + "]]></description>"
            ),

            (
                "<content:encoded><![CDATA["
                + cdata(reader_content)
                + "]]></content:encoded>"
            ),

            "</item>",
        ])

    chunks.extend([
        "</channel>",
        "</rss>",
    ])

    return "\n".join(chunks) + "\n"


# ============================================================
# MAIN
# ============================================================

def main() -> None:

    all_items: list[Item] = []

    print(
        "=== RACCOLTA FONTI ==="
    )

    for src in CONFIG["sources"]:

        got = collect_source(src)

        print(
            f"{src['name']}: "
            f"{len(got)} elementi raccolti"
        )

        all_items.extend(got)

    filtered = dedupe(
        apply_filter(
            all_items
        )
    )

    print(
        f"Totale raccolti: "
        f"{len(all_items)}"
    )

    print(
        f"Pertinenti: "
        f"{len(filtered)}"
    )

    # Limitiamo l'arricchimento ai contenuti
    # che finiranno realmente nel feed.
    max_items = int(
        CONFIG["feed"]
        .get(
            "max_items",
            250,
        )
    )

    selected = filtered[:max_items]

    print(
        "\n=== ARRICCHIMENTO CONTENUTI ==="
    )

    for index, item in enumerate(
        selected,
        start=1,
    ):

        print(
            f"[{index}/{len(selected)}] "
            f"{item.source}: "
            f"{item.title[:80]}"
        )

        try:
            enrich_item(item)

        except Exception as e:
            print(
                f"[WARN] arricchimento: {e}",
                file=sys.stderr,
            )

            # Anche se l'estrazione fallisce,
            # il feed continua a funzionare.
            item.rich_summary = (
                item.summary
                or item.title
            )

            key = hashlib.sha256(
                item.link.encode()
            ).hexdigest()[:20]

            item.local_filename = (
                f"{key}.html"
            )

            item.local_url = (
                f"{PUBLIC_BASE}/articoli/"
                f"{item.local_filename}"
            )

    DOCS_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    write_article_pages(
        selected
    )

    OUT.write_text(
        build_rss(selected),
        encoding="utf-8",
    )

    print(
        f"\nCreato feed: {OUT}"
    )

    print(
        f"Create pagine: "
        f"{len(selected)}"
    )


if __name__ == "__main__":
    main()
