#!/usr/bin/env python3
"""Bouwt de statische pagina met de vrije dagen van het lopende schooljaar.

Twee bronnen:
  1. vlaanderen.be  -> schoolvakanties + wettelijke feestdagen onderwijs
  2. de Google Calendar van de school -> pedagogische studiedagen + facultatieve verlofdagen

Alles wat buiten 1 september - 30 juni valt wordt weggefilterd (de zomervakantie
is de enige uitzondering, zie INCLUDE_ZOMERVAKANTIE).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, asdict, field
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from jinja2 import Environment, FileSystemLoader, select_autoescape

# --------------------------------------------------------------------------- #
# Configuratie
# --------------------------------------------------------------------------- #

VLAANDEREN_URL = (
    "https://www.vlaanderen.be/onderwijs-en-vorming/wat-mag-en-moet-op-school/"
    "schoolvakanties-vrije-dagen-en-afwezigheden/schoolvakanties"
)

# Publieke ICS-feed van de schoolkalender. Zet CALENDAR_ICS_URL als env var om
# een geheim adres ("private address") te gebruiken.
DEFAULT_ICS_URL = (
    "https://calendar.google.com/calendar/ical/mellegbs%40gmail.com/public/basic.ics"
)

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
CACHE_FILE = ROOT / "data" / "schoolvakanties-cache.json"

# De zomervakantie begint op 1 juli en valt dus strikt genomen net buiten het
# schooljaar-venster. Zet op False als je ze niet op de pagina wil.
INCLUDE_ZOMERVAKANTIE = True

# Vanaf welke maand er naar het volgende schooljaar gerold wordt. 7 = vanaf
# 1 juli toont de pagina het schooljaar dat in september begint.
ROLLOVER_MONTH = 7

TIMEOUT = 30
USER_AGENT = "vrije-dagen-bot/1.0 (+github actions; static site build)"

# --------------------------------------------------------------------------- #
# Nederlandse datumhulpjes
# --------------------------------------------------------------------------- #

MAANDEN = {
    "januari": 1, "februari": 2, "maart": 3, "april": 4, "mei": 5, "juni": 6,
    "juli": 7, "augustus": 8, "september": 9, "oktober": 10, "november": 11,
    "december": 12,
}
MAAND_NAAM = {v: k for k, v in MAANDEN.items()}
WEEKDAGEN = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag",
             "zaterdag", "zondag"]

_MAAND_RE = "|".join(MAANDEN)
_WEEKDAG_RE = "|".join(WEEKDAGEN)


def nl_datum(d: dt.date, met_weekdag: bool = True) -> str:
    kern = f"{d.day} {MAAND_NAAM[d.month]} {d.year}"
    return f"{WEEKDAGEN[d.weekday()]} {kern}" if met_weekdag else kern


def nl_periode(start: dt.date, eind: dt.date) -> str:
    """'van maandag 2 tot en met zondag 8 november 2026' -> compact."""
    if start == eind:
        return nl_datum(start)
    if (start.month, start.year) == (eind.month, eind.year):
        return (f"{WEEKDAGEN[start.weekday()]} {start.day} t.e.m. "
                f"{WEEKDAGEN[eind.weekday()]} {eind.day} "
                f"{MAAND_NAAM[eind.month]} {eind.year}")
    return f"{nl_datum(start)} t.e.m. {nl_datum(eind)}"


WEEKDAG_KORT = ["ma", "di", "wo", "do", "vr", "za", "zo"]
MAAND_KORT = {1: "jan", 2: "feb", 3: "mrt", 4: "apr", 5: "mei", 6: "jun",
              7: "jul", 8: "aug", 9: "sep", 10: "okt", 11: "nov", 12: "dec"}


def nl_kort(start: dt.date, eind: dt.date) -> str:
    """Compacte notatie voor het schermoverzicht: 'ma 2 - zo 8 nov'."""
    def stuk(d: dt.date, met_maand: bool = True) -> str:
        kern = f"{WEEKDAG_KORT[d.weekday()]} {d.day}"
        return f"{kern} {MAAND_KORT[d.month]}" if met_maand else kern

    if start == eind:
        return stuk(start)
    if start.month == eind.month and start.year == eind.year:
        return f"{stuk(start, False)} \u2013 {stuk(eind)}"
    return f"{stuk(start)} \u2013 {stuk(eind)}"


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


# --------------------------------------------------------------------------- #
# Schooljaar
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Schooljaar:
    start_jaar: int

    @property
    def label(self) -> str:
        return f"{self.start_jaar}-{self.start_jaar + 1}"

    @property
    def eerste_dag(self) -> dt.date:
        return dt.date(self.start_jaar, 9, 1)

    @property
    def laatste_dag(self) -> dt.date:
        return dt.date(self.start_jaar + 1, 6, 30)

    @property
    def zomer_eind(self) -> dt.date:
        return dt.date(self.start_jaar + 1, 8, 31)

    def bevat(self, d: dt.date) -> bool:
        return self.eerste_dag <= d <= self.laatste_dag


def huidig_schooljaar(vandaag: dt.date) -> Schooljaar:
    return Schooljaar(vandaag.year if vandaag.month >= ROLLOVER_MONTH
                      else vandaag.year - 1)


# --------------------------------------------------------------------------- #
# Datamodel
# --------------------------------------------------------------------------- #

CATEGORIE_LABEL = {
    "vakantie": "Vakantie",
    "feestdag": "Wettelijke feestdag",
    "studiedag": "Pedagogische studiedag",
    "facultatief": "Facultatieve verlofdag",
    "vrije-dag": "Vrije dag",
}


@dataclass
class VrijeDag:
    naam: str
    start: dt.date
    eind: dt.date            # inclusief
    categorie: str           # sleutel uit CATEGORIE_LABEL
    bron: str                # "vlaanderen.be" of "schoolkalender"
    omschrijving: str = ""
    tags: list[str] = field(default_factory=list)

    @property
    def dagen(self) -> int:
        return (self.eind - self.start).days + 1

    @property
    def enkel_weekend(self) -> bool:
        return all(
            (self.start + dt.timedelta(days=i)).weekday() >= 5
            for i in range(self.dagen)
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        d["start"] = self.start.isoformat()
        d["eind"] = self.eind.isoformat()
        d["dagen"] = self.dagen
        d["categorie_label"] = CATEGORIE_LABEL[self.categorie]
        d["periode"] = nl_periode(self.start, self.eind)
        d["kort"] = nl_kort(self.start, self.eind)
        d["enkel_weekend"] = self.enkel_weekend
        d["id"] = slugify(f"{self.naam}-{self.start.isoformat()}")
        return d


# --------------------------------------------------------------------------- #
# Bron 1: vlaanderen.be
# --------------------------------------------------------------------------- #

_ENTRY_RE = re.compile(r"^([A-Za-zÀ-ÿ'’\.\- ]{3,40}):\s*(.+)$")
_PAREN_RE = re.compile(r"\([^)]*\)")

_RANGE_RE = re.compile(
    rf"\bvan\s+(?:(?:{_WEEKDAG_RE})\s+)?(\d{{1,2}})"
    rf"(?:\s+({_MAAND_RE}))?(?:\s+(\d{{4}}))?"
    rf"\s+tot en met\s+(?:(?:{_WEEKDAG_RE})\s+)?(\d{{1,2}})"
    rf"\s+({_MAAND_RE})\s+(\d{{4}})",
    re.IGNORECASE,
)
_PAIR_RE = re.compile(
    rf"(?:(?:{_WEEKDAG_RE})\s+)?(\d{{1,2}})\s+en\s+(?:(?:{_WEEKDAG_RE})\s+)?"
    rf"(\d{{1,2}})\s+({_MAAND_RE})\s+(\d{{4}})",
    re.IGNORECASE,
)
_SINGLE_RE = re.compile(
    rf"(?:(?:{_WEEKDAG_RE})\s+)?(\d{{1,2}})\s+({_MAAND_RE})\s+(\d{{4}})",
    re.IGNORECASE,
)


def _parse_regel(rest: str) -> tuple[dt.date, dt.date] | None:
    rest = _PAREN_RE.sub(" ", rest).strip()

    m = _RANGE_RE.search(rest)
    if m:
        s_dag, s_maand, s_jaar, e_dag, e_maand, e_jaar = m.groups()
        eind = dt.date(int(e_jaar), MAANDEN[e_maand.lower()], int(e_dag))
        maand = MAANDEN[(s_maand or e_maand).lower()]
        jaar = int(s_jaar) if s_jaar else int(e_jaar)
        start = dt.date(jaar, maand, int(s_dag))
        if start > eind:            # jaarwissel, bv. 21 dec -> 3 jan
            start = dt.date(jaar - 1, maand, int(s_dag))
        return start, eind

    m = _PAIR_RE.search(rest)
    if m:
        d1, d2, maand, jaar = m.groups()
        mm, yy = MAANDEN[maand.lower()], int(jaar)
        return dt.date(yy, mm, int(d1)), dt.date(yy, mm, int(d2))

    m = _SINGLE_RE.search(rest)
    if m:
        dag, maand, jaar = m.groups()
        d = dt.date(int(jaar), MAANDEN[maand.lower()], int(dag))
        return d, d

    return None


def parse_vlaanderen_tekst(tekst: str) -> list[VrijeDag]:
    """Leest elke 'Label: datum(s)'-regel van de pagina, ongeacht in welke
    sectie ze staat. Filteren op schooljaar gebeurt later."""
    gevonden: dict[tuple, VrijeDag] = {}
    for regel in tekst.splitlines():
        regel = " ".join(regel.split())
        m = _ENTRY_RE.match(regel)
        if not m:
            continue
        naam, rest = m.group(1).strip(), m.group(2)
        periode = _parse_regel(rest)
        if periode is None:
            continue
        start, eind = periode
        if eind < start or (eind - start).days > 70:
            continue
        categorie = "vakantie" if "vakantie" in naam.lower() else "feestdag"
        item = VrijeDag(naam=naam, start=start, eind=eind, categorie=categorie,
                        bron="vlaanderen.be")
        gevonden.setdefault((start, eind, naam.lower()), item)
    return list(gevonden.values())


def haal_vlaanderen(url: str = VLAANDEREN_URL) -> list[VrijeDag]:
    resp = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return parse_vlaanderen_tekst(soup.get_text("\n"))


# --------------------------------------------------------------------------- #
# Bron 2: Google Calendar (ICS)
# --------------------------------------------------------------------------- #

CLASSIFICATIE = [
    ("studiedag", re.compile(
        r"pedagogische?\s*studiedag|ped\.?\s*studiedag|\bpedagogisch\b", re.I)),
    ("facultatief", re.compile(
        r"facultatieve?\s*(verlof|vrije)?\s*dag|facultatief|\bfac\.?\s*verlof",
        re.I)),
    ("vrije-dag", re.compile(
        r"\bvrije dag\b|\bgeen school\b|\bbrugdag\b|\blesvrij", re.I)),
]


def classificeer(summary: str) -> str | None:
    for categorie, patroon in CLASSIFICATIE:
        if patroon.search(summary):
            return categorie
    return None


def _naar_date(waarde) -> dt.date:
    return waarde.date() if isinstance(waarde, dt.datetime) else waarde


def parse_ics(ics_tekst: str, sj: Schooljaar) -> list[VrijeDag]:
    import icalendar
    import recurring_ical_events

    kalender = icalendar.Calendar.from_ical(ics_tekst)
    events = recurring_ical_events.of(kalender).between(
        sj.eerste_dag, sj.laatste_dag + dt.timedelta(days=1)
    )

    items: list[VrijeDag] = []
    for ev in events:
        summary = str(ev.get("SUMMARY", "")).strip()
        categorie = classificeer(summary)
        if not categorie:
            continue

        start = _naar_date(ev["DTSTART"].dt)
        if "DTEND" in ev:
            rauw_eind = ev["DTEND"].dt
            eind = _naar_date(rauw_eind)
            # Bij hele-dag-events is DTEND exclusief.
            if not isinstance(rauw_eind, dt.datetime):
                eind -= dt.timedelta(days=1)
            if eind < start:
                eind = start
        else:
            eind = start

        items.append(VrijeDag(
            naam=summary,
            start=start,
            eind=eind,
            categorie=categorie,
            bron="schoolkalender",
            omschrijving=str(ev.get("DESCRIPTION", "")).strip()[:300],
        ))
    return items


def haal_kalender(url: str) -> list[VrijeDag] | None:
    resp = requests.get(url, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
    if resp.status_code == 404:
        raise SystemExit(
            f"ICS-feed niet gevonden ({url}).\n"
            "Zet de kalender op 'Openbaar beschikbaar maken' in de "
            "Google Calendar-instellingen, of gebruik het geheime adres "
            "via de CALENDAR_ICS_URL secret."
        )
    resp.raise_for_status()
    return resp.text


# --------------------------------------------------------------------------- #
# Samenvoegen
# --------------------------------------------------------------------------- #

def in_venster(item: VrijeDag, sj: Schooljaar) -> bool:
    if sj.bevat(item.start) or sj.bevat(item.eind):
        return True
    if INCLUDE_ZOMERVAKANTIE and "zomervakantie" in item.naam.lower():
        return item.start <= sj.zomer_eind and item.eind >= sj.laatste_dag
    return False


def overlapt(a: VrijeDag, b: VrijeDag) -> bool:
    return a.start <= b.eind and b.start <= a.eind


def combineer(overheid: list[VrijeDag], school: list[VrijeDag],
              sj: Schooljaar) -> list[VrijeDag]:
    overheid = [i for i in overheid if in_venster(i, sj)]
    school = [i for i in school if in_venster(i, sj)]

    # Een studiedag of verlofdag die de school per ongeluk ook tijdens een
    # vakantie heeft staan, hoeft niet dubbel op de pagina.
    vakanties = [i for i in overheid if i.categorie == "vakantie"]
    school = [i for i in school
              if not any(overlapt(i, v) for v in vakanties)]

    alles = overheid + school
    alles.sort(key=lambda i: (i.start, i.eind))
    return alles


# --------------------------------------------------------------------------- #
# Cache (vangnet als vlaanderen.be onbereikbaar is of het parsen faalt)
# --------------------------------------------------------------------------- #

VERWACHTE_VAKANTIES = {"herfstvakantie", "kerstvakantie", "krokusvakantie",
                       "paasvakantie"}


def controleer_overheid(items: list[VrijeDag], sj: Schooljaar) -> list[str]:
    binnen = [i for i in items if in_venster(i, sj)]
    namen = {i.naam.lower() for i in binnen}
    problemen = [f"ontbreekt: {naam}" for naam in sorted(VERWACHTE_VAKANTIES)
                 if naam not in namen]
    feestdagen = [i for i in binnen if i.categorie == "feestdag"]
    if len(feestdagen) < 4:
        problemen.append(f"slechts {len(feestdagen)} feestdagen gevonden")
    return problemen


def schrijf_cache(items: list[VrijeDag], sj: Schooljaar) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    bestaand = {}
    if CACHE_FILE.exists():
        bestaand = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    bestaand[sj.label] = [i.to_dict() for i in items if in_venster(i, sj)]
    CACHE_FILE.write_text(
        json.dumps(bestaand, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def lees_cache(sj: Schooljaar) -> list[VrijeDag]:
    if not CACHE_FILE.exists():
        return []
    data = json.loads(CACHE_FILE.read_text(encoding="utf-8")).get(sj.label, [])
    return [
        VrijeDag(
            naam=d["naam"],
            start=dt.date.fromisoformat(d["start"]),
            eind=dt.date.fromisoformat(d["eind"]),
            categorie=d["categorie"],
            bron=d["bron"],
        )
        for d in data
    ]


# --------------------------------------------------------------------------- #
# Renderen
# --------------------------------------------------------------------------- #

def render(items: list[VrijeDag], sj: Schooljaar, vandaag: dt.date,
           outdir: Path) -> None:
    """Schrijft de schermpagina plus dezelfde data als JSON.

    De pagina krijgt *alle* vrije dagen van het schooljaar mee. Welke daarvan
    op het scherm komen bepaalt de browser zelf, elke minuut opnieuw. Zo klopt
    het scherm ook als de build een paar dagen niet gedraaid heeft.
    """
    rijen = [item.to_dict() for item in items]

    outdir.mkdir(parents=True, exist_ok=True)
    # Zonder dit bestand duwt GitHub Pages alles door Jekyll, wat hier niets
    # toevoegt en de deploy alleen trager maakt.
    (outdir / ".nojekyll").write_text("", encoding="utf-8")

    payload = {"schooljaar": sj.label, "bijgewerkt_op": vandaag.isoformat(),
               "items": rijen}
    (outdir / "vrije-dagen.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    html = env.get_template("signage.html.j2").render(
        schooljaar=sj.label,
        bijgewerkt_op=nl_datum(vandaag, met_weekdag=False),
        bijgewerkt_iso=vandaag.isoformat(),
        # "<" wegschrijven als escape zodat een kalendertitel het
        # <script>-blok niet kan afsluiten.
        data_json=json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c"),
    )
    (outdir / "index.html").write_text(html, encoding="utf-8")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--vandaag", help="ISO-datum, handig om te testen")
    # Let op: een niet-ingestelde GitHub-secret levert een lege string op,
    # vandaar 'or' in plaats van een default in os.environ.get().
    p.add_argument("--ics-url",
                   default=os.environ.get("CALENDAR_ICS_URL") or DEFAULT_ICS_URL)
    p.add_argument("--ics-file", help="lokaal .ics-bestand i.p.v. downloaden")
    p.add_argument("--html-file", help="lokale kopie van de vlaanderen.be-pagina")
    # GitHub Pages serveert alleen vanuit de repo-root of vanuit /docs.
    p.add_argument("--outdir", default=str(ROOT / "docs"))
    p.add_argument("--no-cache-write", action="store_true")
    args = p.parse_args()

    vandaag = (dt.date.fromisoformat(args.vandaag) if args.vandaag
               else dt.date.today())
    sj = huidig_schooljaar(vandaag)
    print(f"Schooljaar {sj.label} ({sj.eerste_dag} t.e.m. {sj.laatste_dag})")

    # Overheid
    try:
        if args.html_file:
            soup = BeautifulSoup(Path(args.html_file).read_text(encoding="utf-8"),
                                 "html.parser")
            overheid = parse_vlaanderen_tekst(soup.get_text("\n"))
        else:
            overheid = haal_vlaanderen()
        problemen = controleer_overheid(overheid, sj)
    except Exception as exc:                        # noqa: BLE001
        print(f"!! vlaanderen.be niet gelukt: {exc}", file=sys.stderr)
        overheid, problemen = [], ["ophalen mislukt"]

    if problemen:
        print(f"!! controle vlaanderen.be: {'; '.join(problemen)}",
              file=sys.stderr)
        fallback = lees_cache(sj)
        if fallback:
            print("   -> cache uit data/ gebruikt", file=sys.stderr)
            overheid = fallback
        else:
            raise SystemExit("Geen bruikbare vakantiedata en geen cache. "
                             "Build afgebroken zodat de pagina niet leeg wordt.")
    elif not args.no_cache_write:
        schrijf_cache(overheid, sj)

    # Schoolkalender
    ics = (Path(args.ics_file).read_text(encoding="utf-8") if args.ics_file
           else haal_kalender(args.ics_url))
    school = parse_ics(ics, sj)
    print(f"Schoolkalender: {len(school)} relevante item(s) gevonden")

    items = combineer(overheid, school, sj)
    for i in items:
        print(f"  {i.start} t.e.m. {i.eind}  [{i.categorie}] {i.naam}")

    render(items, sj, vandaag, Path(args.outdir))
    print(f"Geschreven naar {args.outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
