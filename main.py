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
# CONFIGURAZIONE
# ============================================================

ROOT = Path(__file__).resolve().parent

CONFIG = yaml.safe_load(
    (ROOT / "config.yml").read_text(encoding="utf-8")
)

DOCS_DIR = ROOT / "docs"
ARTICLES_DIR = DOCS_DIR / "articoli"
OUT = DOCS_DIR / "feed.xml"

PUBLIC_BASE = "https://ittsadele.github.io/rss-fiscale-italia"

UA = (
    "Mozilla/5.0 "
    "(compatible; FiscoContabilitaRSS/3.0; "
    "+https://ittsadele.github.io/rss-fiscale-italia/)"
)

TIMEOUT = 30
REQUEST_DELAY = 0.15

MAX_SOURCE_TEXT = 16000
MAX_SUMMARY_CHARS = 3000


# ============================================================
# FILTRO FISCALE/CONTABILE STRETTO
# ============================================================

# Termini "forti":
# se compaiono, indicano con buona probabilità una notizia
# pertinente al lavoro fiscale/contabile.

STRONG_TERMS = {
    # IVA
    "iva": 8,
    "imposta sul valore aggiunto": 10,
    "dpr 633": 10,
    "d.p.r. 633": 10,
    "fattura elettronica": 10,
    "fatturazione elettronica": 10,
    "corrispettivi telematici": 9,
    "reverse charge": 9,
    "split payment": 9,
    "esterometro": 9,
    "liquidazione iva": 9,
    "dichiarazione iva": 10,
    "registro iva": 8,

    # Imposte dirette
    "irpef": 10,
    "ires": 10,
    "irap": 10,
    "tuir": 10,
    "dpr 917": 10,
    "d.p.r. 917": 10,
    "reddito d'impresa": 8,
    "reddito di impresa": 8,
    "reddito di lavoro autonomo": 8,
    "redditi diversi": 6,

    # Regimi fiscali
    "regime forfetario": 10,
    "regime forfettario": 10,
    "regime dei minimi": 8,
    "regime fiscale": 5,

    # Dichiarazioni
    "modello redditi": 10,
    "modello 730": 10,
    "730/202": 8,
    "modello 770": 10,
    "certificazione unica": 10,
    "dichiarazione dei redditi": 10,
    "dichiarazione fiscale": 8,

    # Versamenti / ritenute
    "f24": 9,
    "ritenuta d'acconto": 10,
    "ritenute d'acconto": 10,
    "sostituto d'imposta": 9,
    "sostituti d'imposta": 9,
    "compensazione crediti": 8,
    "codice tributo": 10,

    # Crediti / agevolazioni fiscali
    "credito d'imposta": 10,
    "crediti d'imposta": 10,
    "agevolazione fiscale": 8,
    "agevolazioni fiscali": 8,
    "detrazione fiscale": 8,
    "detrazioni fiscali": 8,
    "deduzione fiscale": 8,

    # Accertamento
    "accertamento tributario": 10,
    "accertamento fiscale": 10,
    "avviso di accertamento": 10,
    "ravvedimento operoso": 10,
    "sanzioni tributarie": 10,
    "sanzione tributaria": 10,

    # Riscossione
    "riscossione": 7,
    "cartella di pagamento": 10,
    "cartella esattoriale": 10,
    "definizione agevolata": 8,
    "rottamazione": 8,

    # Contenzioso
    "contenzioso tributario": 10,
    "processo tributario": 10,
    "giustizia tributaria": 10,
    "corte di giustizia tributaria": 10,

    # Interpelli / prassi
    "interpello": 8,
    "risposta a interpello": 10,
    "risoluzione agenzia delle entrate": 10,
    "circolare agenzia delle entrate": 10,
    "provvedimento agenzia delle entrate": 10,

    # Contabilità / bilanci / OIC
    "oic": 12,
    "organismo italiano di contabilità": 12,
    "principio contabile": 10,
    "principi contabili": 10,
    "bilancio d'esercizio": 10,
    "bilancio consolidato": 8,
    "scritture contabili": 9,
    "libri contabili": 8,

    # Imposte indirette
    "imposta di registro": 10,
    "imposta di bollo": 10,
    "imposta sulle successioni": 10,
    "imposta sulle donazioni": 10,
    "successioni e donazioni": 8,
    "imu": 8,

    # Operazioni societarie fiscalmente rilevanti
    "fusione societaria": 7,
    "scissione societaria": 7,
    "conferimento d'azienda": 8,
    "cessione d'azienda": 8,
    "trasformazione societaria": 7,

    # Adempimenti
    "adempimenti fiscali": 9,
    "adempimento fiscale": 9,
    "scadenze fiscali": 8,

    # Agenzia Entrate
    "agenzia delle entrate": 4,

    # Tributi molto specifici
    "imposta sostitutiva": 8,
    "imposte sui redditi": 8,
}


# Termini medi.
# Da soli NON bastano.
# Servono insieme ad almeno un altro segnale fiscale.

MEDIUM_TERMS = {
    "contabilità": 3,
    "contabile": 2,
    "bilancio": 3,
    "tributario": 3,
    "tributaria": 3,
    "tributi": 2,
    "fiscale": 2,
    "fisco": 3,
    "imposta": 2,
    "imposte": 2,
    "dichiarazione": 2,
    "versamento": 2,
    "versamenti": 2,
    "detrazione": 2,
    "deduzione": 2,
    "agevolazione": 2,
    "agevolazioni": 2,
    "bonus": 1,
    "reddito": 2,
    "redditi": 2,
    "contribuente": 2,
    "contribuenti": 2,
    "fattura": 2,
    "fatturazione": 2,
    "ritenuta": 2,
    "aliquota": 2,
    "aliquote": 2,
}


# Termini che indicano spesso notizie non pertinenti.
NEGATIVE_TERMS = {
    "concorso pubblico": -12,
    "concorsi pubblici": -12,
    "personale sanitario": -10,
    "servizio sanitario": -10,
    "sanità": -8,
    "sanitario": -6,

    "difesa": -8,
    "forze armate": -8,

    "pesca": -8,
    "acquacoltura": -8,

    "infrastrutture": -6,
    "trasporti": -6,

    "protezione civile": -6,
    "emergenza": -3,

    "università": -6,
    "scuola": -6,
    "istruzione": -5,

    "cultura": -5,
    "beni culturali": -6,

    "sport": -7,
    "sportivo": -5,

    "turismo": -6,
    "turistico": -5,

    "ambiente": -5,
    "ambientale": -5,

    "energia": -4,
    "energetico": -4,

    "agricoltura": -3,
    "agricolo": -3,

    "forestale": -5,

    "medicinale": -6,
    "farmaco": -6,
    "farmaceutico": -6,

    "universitario": -5,
    "ricerca scientifica": -4,

    "militare": -7,

    "immigrazione": -5,
    "asilo": -5,

    "protezione internazionale": -5,

    "appalti pubblici": -3,
    "opera pubblica": -5,
}


# Fonti dove vogliamo essere un po' più permissivi,
# ma NON includere tutto indiscriminatamente.
TRUSTED_FISCAL_SOURCES = {
    "Agenzia delle Entrate",
    "Dipartimento delle Finanze",
    "MEF",
    "OIC",
    "Fondazione OIC",
}


# Soglia finale.
MIN_SCORE = 8


# Almeno uno di questi deve essere vero.
MIN_STRONG_MATCHES = 1


# ============================================================
# MODELLO DATI
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
# SESSIONE HTTP
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": UA,
    "Accept": (
        "text/html,"
        "application/xhtml+xml,"
        "application/rss+xml,"
        "application/xml;q=0.9,"
        "*/*;q=0.8"
    ),
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.6",
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
# NORMALIZZAZIONE TESTO
# ============================================================

def norm_text(value: str) -> str:
    if not value:
        return ""

    soup = BeautifulSoup(
        value,
        "html.parser"
    )

    text = soup.get_text(
        " ",
        strip=True
    )

    text = html.unescape(text)

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


# ============================================================
# DATE
# ============================================================

def parse_date(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)

    try:
        d = dateparser.parse(
            value,
            dayfirst=True
        )

        if d.tzinfo is None:
            d = d.replace(
                tzinfo=timezone.utc
            )

        return d.astimezone(
            timezone.utc
        )

    except Exception:
        return datetime.now(
            timezone.utc
        )


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

    return (
        f"{d.day} "
        f"{months[d.month]} "
        f"{d.year}"
    )


# ============================================================
# PARSING RSS
# ============================================================

def parse_feed(
    url: str,
    source_name: str
) -> list[Item]:

    r = get(url)

    parsed = feedparser.parse(
        r.content
    )

    out: list[Item] = []

    for e in parsed.entries:

        title = norm_text(
            getattr(
                e,
                "title",
                ""
            )
        )

        link = getattr(
            e,
            "link",
            ""
        )

        summary = norm_text(
            getattr(
                e,
                "summary",
                ""
            )
            or getattr(
                e,
                "description",
                ""
            )
        )

        published = parse_date(
            getattr(
                e,
                "published",
                None
            )
            or getattr(
                e,
                "updated",
                None
            )
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


# ============================================================
# SCOPERTA RSS
# ============================================================

def discover_rss(
    page_url: str
) -> list[str]:

    r = get(page_url)

    soup = BeautifulSoup(
        r.text,
        "html.parser"
    )

    feeds: list[str] = []

    for tag in soup.find_all(
        ["link", "a"],
        href=True
    ):

        href = tag.get(
            "href",
            ""
        )

        typ = (
            tag.get("type")
            or ""
        ).lower()

        txt = (
            tag.get_text(
                " ",
                strip=True
            )
            + " "
            + str(
                tag.get("title")
                or ""
            )
        ).lower()

        if (
            "rss" in typ
            or "atom" in typ
            or "rss" in href.lower()
            or "feed" in href.lower()
            or "rss" in txt
        ):

            u = urljoin(
                page_url,
                href
            )

            if (
                urlparse(u).scheme
                in {"http", "https"}
                and u not in feeds
                and not u.lower().endswith(
                    (
                        ".png",
                        ".jpg",
                        ".jpeg",
                        ".gif",
                        ".svg",
                    )
                )
            ):
                feeds.append(u)

    return feeds[:12]


# ============================================================
# FALLBACK HTML
# ============================================================

def scrape_listing(
    url: str,
    source_name: str
) -> list[Item]:

    r = get(url)

    soup = BeautifulSoup(
        r.text,
        "html.parser"
    )

    host = urlparse(url).netloc

    out: list[Item] = []
    seen: set[str] = set()

    for a in soup.find_all(
        "a",
        href=True
    ):

        title = norm_text(
            a.get_text(
                " ",
                strip=True
            )
        )

        if len(title) < 18:
            continue

        link = urljoin(
            url,
            a["href"]
        )

        p = urlparse(link)

        if (
            p.netloc != host
            or link in seen
        ):
            continue

        low = (
            title
            + " "
            + link
        ).lower()

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
            [
                "article",
                "li",
                "div",
                "tr"
            ]
        )

        summary = norm_text(
            parent.get_text(
                " ",
                strip=True
            )
            if parent
            else title
        )[:1500]

        published = datetime.now(
            timezone.utc
        )

        out.append(
            Item(
                title=title,
                link=link,
                summary=summary,
                source=source_name,
                published=published,
            )
        )

    return out[:150]


# ============================================================
# RACCOLTA FONTI
# ============================================================

def collect_source(
    src: dict
) -> list[Item]:

    name = src["name"]

    items: list[Item] = []

    try:

        if src["type"] == "discover_rss":

            feeds = discover_rss(
                src["discovery_url"]
            )

            for feed_url in feeds:

                try:

                    got = parse_feed(
                        feed_url,
                        name
                    )

                    if got:
                        items.extend(
                            got
                        )

                except Exception as e:

                    print(
                        f"[WARN] "
                        f"{name}: "
                        f"{feed_url}: "
                        f"{e}",
                        file=sys.stderr
                    )

            if not items:

                items = scrape_listing(
                    src["fallback_url"],
                    name
                )

        elif src["type"] == "rss_or_html":

            for u in src["urls"]:

                try:

                    got = parse_feed(
                        u,
                        name
                    )

                    if got:
                        items.extend(
                            got
                        )

                        break

                except Exception:

                    try:

                        got = scrape_listing(
                            u,
                            name
                        )

                        if got:
                            items.extend(
                                got
                            )

                            break

                    except Exception as e:

                        print(
                            f"[WARN] "
                            f"{name}: "
                            f"{u}: "
                            f"{e}",
                            file=sys.stderr
                        )

    except Exception as e:

        print(
            f"[WARN] "
            f"source {name}: "
            f"{e}",
            file=sys.stderr
        )

    return items


# ============================================================
# MATCH TERMINI
# ============================================================

def term_matches(
    text: str,
    term: str
) -> bool:

    text = text.lower()
    term = term.lower()

    # Per termini molto corti come IVA, IMU, OIC
    # evitiamo match dentro altre parole.

    if len(term) <= 4 and term.isalnum():

        pattern = (
            r"(?<![a-z0-9])"
            + re.escape(term)
            + r"(?![a-z0-9])"
        )

        return bool(
            re.search(
                pattern,
                text
            )
        )

    return term in text


# ============================================================
# FILTRO DI RILEVANZA
# ============================================================

def relevance_score(
    item: Item
) -> tuple[int, int, int]:

    title = item.title.lower()

    text = (
        item.title
        + " "
        + item.summary
    ).lower()

    score = 0
    strong_matches = 0
    medium_matches = 0

    # --------------------------------------------
    # TERMINI FORTI
    # --------------------------------------------

    for term, weight in STRONG_TERMS.items():

        if term_matches(
            text,
            term
        ):

            score += weight
            strong_matches += 1

            # Se appare nel titolo,
            # vale ancora di più.

            if term_matches(
                title,
                term
            ):
                score += 3

    # --------------------------------------------
    # TERMINI MEDI
    # --------------------------------------------

    for term, weight in MEDIUM_TERMS.items():

        if term_matches(
            text,
            term
        ):

            score += weight
            medium_matches += 1

    # --------------------------------------------
    # TERMINI NEGATIVI
    # --------------------------------------------

    for term, penalty in NEGATIVE_TERMS.items():

        if term_matches(
            text,
            term
        ):

            score += penalty

    # --------------------------------------------
    # BONUS PER COMBINAZIONI FISCALI
    # --------------------------------------------

    tax_context = any(
        term_matches(
            text,
            term
        )
        for term in [
            "iva",
            "irpef",
            "ires",
            "irap",
            "tuir",
            "tributario",
            "tributaria",
            "fiscale",
            "agenzia delle entrate",
            "credito d'imposta",
            "imposta di registro",
            "oic",
        ]
    )

    accounting_context = any(
        term_matches(
            text,
            term
        )
        for term in [
            "contabilità",
            "bilancio",
            "scritture contabili",
            "principio contabile",
            "oic",
        ]
    )

    if tax_context and medium_matches >= 2:
        score += 3

    if accounting_context:
        score += 2

    # Agenzia Entrate/MEF/OIC:
    # leggero bonus ma mai inclusione automatica.

    if item.source in TRUSTED_FISCAL_SOURCES:
        score += 2

    return (
        score,
        strong_matches,
        medium_matches,
    )


# ============================================================
# FILTRO FINALE
# ============================================================

def apply_filter(
    items: Iterable[Item]
) -> list[Item]:

    out: list[Item] = []

    for item in items:

        (
            score,
            strong_matches,
            medium_matches
        ) = relevance_score(
            item
        )

        item.score = score

        # --------------------------------------------
        # REGOLA PRINCIPALE
        # --------------------------------------------

        if score < MIN_SCORE:
            continue

        # Deve esserci almeno un termine forte,
        # salvo casi contabili molto evidenti.

        if strong_matches < MIN_STRONG_MATCHES:

            text = (
                item.title
                + " "
                + item.summary
            ).lower()

            obvious_accounting = any(
                term_matches(
                    text,
                    x
                )
                for x in [
                    "bilancio d'esercizio",
                    "principio contabile",
                    "principi contabili",
                    "scritture contabili",
                ]
            )

            if not obvious_accounting:
                continue

        item.category = classify(
            item.title
            + " "
            + item.summary
        )

        out.append(item)

    return out


# ============================================================
# CLASSIFICAZIONE
# ============================================================

def classify(
    text: str
) -> str:

    t = text.lower()

    groups = [

        (
            "IVA / Fatturazione",
            [
                "iva",
                "imposta sul valore aggiunto",
                "fattura elettronica",
                "fatturazione elettronica",
                "reverse charge",
                "split payment",
                "corrispettivi telematici",
            ],
        ),

        (
            "IRPEF / IRES / IRAP",
            [
                "irpef",
                "ires",
                "irap",
                "tuir",
                "reddito d'impresa",
                "reddito di impresa",
            ],
        ),

        (
            "Dichiarazioni / Versamenti",
            [
                "730",
                "770",
                "modello redditi",
                "certificazione unica",
                "f24",
                "ritenuta",
                "compensazione",
            ],
        ),

        (
            "Crediti / Agevolazioni",
            [
                "credito d'imposta",
                "crediti d'imposta",
                "agevolazione fiscale",
                "detrazione fiscale",
                "bonus fiscale",
            ],
        ),

        (
            "Accertamento / Riscossione",
            [
                "accertamento tributario",
                "accertamento fiscale",
                "riscossione",
                "cartella",
                "ravvedimento operoso",
                "sanzioni tributarie",
            ],
        ),

        (
            "Contenzioso tributario",
            [
                "contenzioso tributario",
                "processo tributario",
                "giustizia tributaria",
                "corte di giustizia tributaria",
            ],
        ),

        (
            "Contabilità / OIC",
            [
                "oic",
                "principio contabile",
                "principi contabili",
                "bilancio d'esercizio",
                "scritture contabili",
            ],
        ),

        (
            "Altri tributi",
            [
                "imposta di registro",
                "imposta di bollo",
                "successioni",
                "donazioni",
                "imu",
            ],
        ),
    ]

    for name, terms in groups:

        if any(
            term_matches(
                t,
                term
            )
            for term in terms
        ):
            return name

    return "Fisco / Tributi"


# ============================================================
# DEDUPLICAZIONE
# ============================================================

def dedupe(
    items: Iterable[Item]
) -> list[Item]:

    seen: set[str] = set()

    out: list[Item] = []

    for item in sorted(
        items,
        key=lambda x: x.published,
        reverse=True
    ):

        key = (
            item.link
            .split("#", 1)[0]
            .rstrip("/")
        )

        if not key:

            key = hashlib.sha1(
                item.title.encode(
                    "utf-8"
                )
            ).hexdigest()

        if key in seen:
            continue

        seen.add(key)
        out.append(item)

    return out


# ============================================================
# PULIZIA PAGINE
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


def clean_page(
    soup: BeautifulSoup
) -> None:

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


# ============================================================
# ESTRAZIONE TESTO ARTICOLO
# ============================================================

def extract_main_text(
    url: str
) -> str:

    try:

        r = get(url)

    except Exception as e:

        print(
            f"[WARN] "
            f"apertura articolo "
            f"{url}: {e}",
            file=sys.stderr
        )

        return ""

    content_type = (
        r.headers
        .get(
            "Content-Type",
            ""
        )
        .lower()
    )

    if "pdf" in content_type:
        return ""

    try:

        soup = BeautifulSoup(
            r.text,
            "html.parser"
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

        for node in soup.select(
            selector
        ):

            text = norm_text(
                node.get_text(
                    " ",
                    strip=True
                )
            )

            if len(text) >= 250:

                candidates.append(
                    text
                )

    if candidates:

        text = max(
            candidates,
            key=len
        )

    else:

        body = (
            soup.body
            or soup
        )

        text = norm_text(
            body.get_text(
                " ",
                strip=True
            )
        )

    pieces = []

    for piece in re.split(
        r"(?<=[.!?;:])\s+",
        text
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

    cleaned = " ".join(
        pieces
    )

    cleaned = re.sub(
        r"\s+",
        " ",
        cleaned
    ).strip()

    return cleaned[
        :MAX_SOURCE_TEXT
    ]


# ============================================================
# SUDDIVISIONE FRASI
# ============================================================

def sentence_split(
    text: str
) -> list[str]:

    text = re.sub(
        r"\s+",
        " ",
        text
    ).strip()

    if not text:
        return []

    sentences = re.split(
        r"(?<=[.!?])\s+(?=[A-ZÀ-ÖØ-Ý0-9])",
        text
    )

    return [
        s.strip()
        for s in sentences
        if len(
            s.strip()
        ) >= 35
    ]


# ============================================================
# RIASSUNTO AUTOMATICO
# ============================================================

def create_extract_summary(
    item: Item
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

        return (
            item.summary[
                :MAX_SUMMARY_CHARS
            ]
        )

    keywords = list(
        STRONG_TERMS.keys()
    )

    ranked = []

    for index, sentence in enumerate(
        sentences
    ):

        low = sentence.lower()

        score = 0

        for keyword in keywords:

            if term_matches(
                low,
                keyword
            ):
                score += 3

        # Inizio articolo normalmente
        # contiene oggetto e contesto.

        if index < 3:
            score += 5

        elif index < 7:
            score += 2

        if re.search(
            r"\b("
            r"entra in vigore|"
            r"decorre|"
            r"si applica|"
            r"modifica|"
            r"prevede|"
            r"stabilisce|"
            r"introduce|"
            r"abroga|"
            r"sostituisce|"
            r"disciplina"
            r")\b",
            low
        ):
            score += 5

        ranked.append(
            (
                score,
                index,
                sentence
            )
        )

    selected = sorted(
        ranked,
        key=lambda x: (
            -x[0],
            x[1]
        )
    )[:10]

    selected = sorted(
        selected,
        key=lambda x: x[1]
    )

    output = []

    total = 0

    for _, _, sentence in selected:

        if sentence in output:
            continue

        if (
            total
            + len(sentence)
            > MAX_SUMMARY_CHARS
        ):
            break

        output.append(sentence)

        total += (
            len(sentence)
            + 1
        )

    result = " ".join(
        output
    ).strip()

    return (
        result
        or item.summary
    )[:MAX_SUMMARY_CHARS]


# ============================================================
# TAG
# ============================================================

def detect_topics(
    item: Item
) -> list[str]:

    text = (
        item.title
        + " "
        + item.summary
        + " "
        + item.full_text
    ).lower()

    mapping = [

        (
            "IVA",
            [
                "iva",
                "imposta sul valore aggiunto"
            ]
        ),

        (
            "Fatturazione elettronica",
            [
                "fattura elettronica",
                "fatturazione elettronica"
            ]
        ),

        (
            "IRPEF",
            ["irpef"]
        ),

        (
            "IRES",
            ["ires"]
        ),

        (
            "IRAP",
            ["irap"]
        ),

        (
            "Dichiarazioni",
            [
                "modello redditi",
                "730",
                "770",
                "dichiarazione fiscale"
            ]
        ),

        (
            "F24",
            ["f24"]
        ),

        (
            "Ritenute",
            ["ritenuta d'acconto"]
        ),

        (
            "Crediti d'imposta",
            ["credito d'imposta"]
        ),

        (
            "Accertamento",
            ["accertamento"]
        ),

        (
            "Riscossione",
            ["riscossione"]
        ),

        (
            "Contenzioso tributario",
            [
                "contenzioso tributario",
                "processo tributario"
            ]
        ),

        (
            "Contabilità",
            [
                "contabilità",
                "scritture contabili"
            ]
        ),

        (
            "Bilancio",
            ["bilancio d'esercizio"]
        ),

        (
            "OIC",
            [
                "oic",
                "principio contabile"
            ]
        ),
    ]

    topics = []

    for label, keys in mapping:

        if any(
            term_matches(
                text,
                key
            )
            for key in keys
        ):

            topics.append(
                label
            )

    return topics[:8]


# ============================================================
# ARRICCHIMENTO
# ============================================================

def enrich_item(
    item: Item
) -> None:

    item.full_text = extract_main_text(
        item.link
    )

    item.rich_summary = create_extract_summary(
        item
    )

    key = hashlib.sha256(
        item.link.encode(
            "utf-8"
        )
    ).hexdigest()[:20]

    item.local_filename = (
        f"{key}.html"
    )

    item.local_url = (
        f"{PUBLIC_BASE}"
        f"/articoli/"
        f"{item.local_filename}"
    )


# ============================================================
# HTML
# ============================================================

def html_escape(
    value: str
) -> str:

    return html.escape(
        value or "",
        quote=True
    )


def build_article_page(
    item: Item
) -> str:

    topics = detect_topics(
        item
    )

    topics_html = "".join(
        (
            "<span class='tag'>"
            + html_escape(t)
            + "</span>"
        )
        for t in topics
    )

    summary = (
        item.rich_summary
        or item.summary
    )

    paragraphs = []

    sentences = sentence_split(
        summary
    )

    current = []

    for sentence in sentences:

        current.append(sentence)

        if len(
            " ".join(current)
        ) > 450:

            paragraphs.append(
                " ".join(current)
            )

            current = []

    if current:

        paragraphs.append(
            " ".join(current)
        )

    if not paragraphs:

        paragraphs = [
            summary
        ]

    paragraphs_html = "".join(
        (
            "<p>"
            + html_escape(p)
            + "</p>"
        )
        for p in paragraphs
        if p
    )

    return f"""<!doctype html>

<html lang="it">

<head>

<meta charset="utf-8">

<meta
name="viewport"
content="width=device-width,initial-scale=1"
>

<title>
{html_escape(item.title)}
</title>

<style>

body {{
    margin: 0;
    background: #f5f6f8;
    color: #202124;
    font-family:
        -apple-system,
        BlinkMacSystemFont,
        "Segoe UI",
        Arial,
        sans-serif;
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
    box-shadow:
        0 4px 22px
        rgba(0,0,0,.07);
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

{html_escape(
    italian_date(
        item.published
    )
)}

</div>

<div class="tags">
{topics_html}
</div>

<h2>
In sintesi
</h2>

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

Sintesi automatica ricavata dal contenuto
disponibile sulla fonte ufficiale.

Per completezza, aggiornamenti e valore
giuridico fare sempre riferimento al
documento originale.

</div>

</div>

</div>

</body>

</html>
"""


# ============================================================
# SCRITTURA PAGINE HTML
# ============================================================

def write_article_pages(
    items: list[Item]
) -> None:

    ARTICLES_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    for item in items:

        if not item.local_filename:
            continue

        path = (
            ARTICLES_DIR
            / item.local_filename
        )

        path.write_text(
            build_article_page(
                item
            ),
            encoding="utf-8"
        )


# ============================================================
# XML / RSS
# ============================================================

def xml_escape(
    value: str
) -> str:

    return html.escape(
        value or "",
        quote=True
    )


def cdata(
    value: str
) -> str:

    return value.replace(
        "]]>",
        "]]]]><![CDATA[>"
    )


# ============================================================
# CONTENUTO RSS ESTESO
# ============================================================

def build_reader_content(
    item: Item
) -> str:

    topics = detect_topics(
        item
    )

    topic_html = ""

    if topics:

        topic_html = (
            "<p>"
            "<strong>Argomenti:</strong> "
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

<strong>
{html_escape(item.source)}
</strong>

<br>

{html_escape(item.category)}

<br>

{html_escape(
    italian_date(
        item.published
    )
)}

</p>

{topic_html}

<h3>
In sintesi
</h3>

<p style="line-height:1.65">

{summary}

</p>

<p>

<a href="{html_escape(item.link)}">

<strong>
Leggi il documento completo sulla fonte ufficiale →
</strong>

</a>

</p>

<hr>

<p style="font-size:12px;color:#777">

Sintesi automatica ricavata dal contenuto disponibile
sulla fonte ufficiale.

Per il testo completo e avente valore ufficiale
consultare sempre il documento originale.

</p>

</div>

""".strip()


# ============================================================
# GENERAZIONE RSS
# ============================================================

def build_rss(
    items: list[Item]
) -> str:

    feed_config = CONFIG["feed"]

    now = datetime.now(
        timezone.utc
    )

    chunks = [

        '<?xml version="1.0" encoding="UTF-8"?>',

        (
            '<rss version="2.0" '
            'xmlns:content="'
            'http://purl.org/rss/1.0/modules/content/'
            '">'
        ),

        "<channel>",

        (
            "<title>"
            + xml_escape(
                feed_config["title"]
            )
            + "</title>"
        ),

        (
            "<link>"
            + xml_escape(
                PUBLIC_BASE + "/"
            )
            + "</link>"
        ),

        (
            "<description>"
            + xml_escape(
                feed_config["description"]
            )
            + "</description>"
        ),

        "<language>it-IT</language>",

        (
            "<lastBuildDate>"
            + format_datetime(now)
            + "</lastBuildDate>"
        ),

        "<generator>FiscoContabilitaRSS 3.0</generator>",

        "<ttl>60</ttl>",
    ]

    max_items = int(
        feed_config.get(
            "max_items",
            150
        )
    )

    for item in items[:max_items]:

        guid = hashlib.sha256(
            item.link.encode(
                "utf-8"
            )
        ).hexdigest()

        content = build_reader_content(
            item
        )

        description = (
            item.rich_summary
            or item.summary
        )[:1800]

        item_link = (
            item.local_url
            or item.link
        )

        chunks.extend([

            "<item>",

            (
                "<title>"
                + xml_escape(
                    "["
                    + item.source
                    + "] "
                    + item.title
                )
                + "</title>"
            ),

            (
                "<link>"
                + xml_escape(
                    item_link
                )
                + "</link>"
            ),

            (
                '<guid isPermaLink="false">'
                + guid
                + "</guid>"
            ),

            (
                "<pubDate>"
                + format_datetime(
                    item.published
                )
                + "</pubDate>"
            ),

            (
                "<category>"
                + xml_escape(
                    item.category
                )
                + "</category>"
            ),

            (
                "<description><![CDATA["
                + cdata(
                    "<p><strong>"
                    + html_escape(
                        item.source
                    )
                    + "</strong> · "
                    + html_escape(
                        item.category
                    )
                    + "</p>"
                    + "<p>"
                    + html_escape(
                        description
                    )
                    + "</p>"
                    + '<p><a href="'
                    + html_escape(
                        item.link
                    )
                    + '">'
                    + "Leggi la fonte ufficiale →"
                    + "</a></p>"
                )
                + "]]></description>"
            ),

            (
                "<content:encoded><![CDATA["
                + cdata(content)
                + "]]></content:encoded>"
            ),

            "</item>",
        ])

    chunks.extend([
        "</channel>",
        "</rss>",
    ])

    return (
        "\n".join(chunks)
        + "\n"
    )


# ============================================================
# MAIN
# ============================================================

def main() -> None:

    all_items: list[Item] = []

    print(
        "\n"
        "=============================="
    )

    print(
        " RACCOLTA FONTI"
    )

    print(
        "=============================="
    )

    for src in CONFIG["sources"]:

        got = collect_source(
            src
        )

        print(
            f"{src['name']}: "
            f"{len(got)} elementi"
        )

        all_items.extend(
            got
        )

    print(
        "\n"
        "=============================="
    )

    print(
        " FILTRO RILEVANZA"
    )

    print(
        "=============================="
    )

    filtered = apply_filter(
        all_items
    )

    filtered = dedupe(
        filtered
    )

    print(
        f"Totale raccolti: "
        f"{len(all_items)}"
    )

    print(
        f"Selezionati: "
        f"{len(filtered)}"
    )

    print(
        f"Scartati: "
        f"{len(all_items) - len(filtered)}"
    )

    print(
        "\n"
        "ARTICOLI SELEZIONATI:"
    )

    for item in filtered[:30]:

        print(
            f"  [{item.score}] "
            f"[{item.category}] "
            f"{item.title[:110]}"
        )

    max_items = int(
        CONFIG["feed"].get(
            "max_items",
            150
        )
    )

    selected = filtered[
        :max_items
    ]

    print(
        "\n"
        "=============================="
    )

    print(
        " ARRICCHIMENTO"
    )

    print(
        "=============================="
    )

    for index, item in enumerate(
        selected,
        start=1
    ):

        print(
            f"[{index}/{len(selected)}] "
            f"{item.source}: "
            f"{item.title[:80]}"
        )

        try:

            enrich_item(
                item
            )

        except Exception as e:

            print(
                f"[WARN] "
                f"arricchimento: "
                f"{e}",
                file=sys.stderr
            )

            item.rich_summary = (
                item.summary
                or item.title
            )

            key = hashlib.sha256(
                item.link.encode(
                    "utf-8"
                )
            ).hexdigest()[:20]

            item.local_filename = (
                f"{key}.html"
            )

            item.local_url = (
                f"{PUBLIC_BASE}"
                f"/articoli/"
                f"{item.local_filename}"
            )

    DOCS_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    write_article_pages(
        selected
    )

    OUT.write_text(
        build_rss(
            selected
        ),
        encoding="utf-8"
    )

    print(
        "\n"
        "=============================="
    )

    print(
        " COMPLETATO"
    )

    print(
        "=============================="
    )

    print(
        f"Feed creato: "
        f"{OUT}"
    )

    print(
        f"Articoli nel feed: "
        f"{len(selected)}"
    )

    print(
        f"URL pubblico: "
        f"{PUBLIC_BASE}/feed.xml"
    )


if __name__ == "__main__":
    main()
