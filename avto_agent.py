#!/usr/bin/env python3
"""
Avto.net agent
--------------
Spremlja nove oglase na avto.net, jih oceni in obvesti po e-posti.

  TAKOJSNJE OBVESTILO  -> ob novem oglasu z oceno >= ALERT_THRESHOLD (tebi)
  DNEVNI POVZETEK      -> ob DIGEST_HOUR, vse novo iz zadnjih 24 ur (njej)

Iskalni URL-ji se sestavijo sami iz konfiguracije spodaj.
Fina sita (gorivo, uvoz) tecejo v Pythonu, ne prek parametrov avto.neta --
tako se ne zanasamo na parametre, ki jih portal lahko kadarkoli spremeni.

Diagnostika:  python avto_agent.py --test
"""

import json
import os
import random
import re
import smtplib
import sys
import time
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

# =====================================================================
#  KONFIGURACIJA
# =====================================================================

TZ = ZoneInfo("Europe/Ljubljana")

ISKANJA = [
    {"znamka": "Volkswagen", "model": "T-Cross"},
    {"znamka": "Volkswagen", "model": "T-Roc"},
    # Isti podvozek MQB A0, isti 1.0 TSI, pogosto 1-2k ceneje.
    # Odkomentiraj, ce hocesta razsiriti izbiro:
    # {"znamka": "Skoda",      "model": "Kamiq"},
    # {"znamka": "Seat",       "model": "Arona"},
    # {"znamka": "Volkswagen", "model": "Taigo"},
]

CENA_MAX = 16000          # EUR
KM_MAX = 70000            # kilometri
SAMO_BENCIN = True        # izloci dizel / hibrid / elektro
KAZEN_ZA_UVOZ = 15        # tocke dol, ce oglas omenja uvoz (ne izloci ga)

DIGEST_HOUR = 10          # ura dnevnega povzetka (lokalni cas)
ALERT_THRESHOLD = 75      # ocena za takojsnje obvestilo
MAX_PAGES = 3             # koliko strani rezultatov na iskanje

# =====================================================================
#  SESTAVLJANJE URL-JA
# =====================================================================

# Osnova je pravi avto.net URL za osebna vozila. Spreminjamo samo
# znamko, model, ceno in kilometre -- ostalo pustimo pri miru.
OSNOVA = {
    "modelID": "", "tip": "katerikoli tip",
    "znamka2": "", "model2": "", "tip2": "",
    "znamka3": "", "model3": "", "tip3": "",
    "cenaMin": "0", "letnikMin": "0", "letnikMax": "2090",
    "bencin": "0", "starost2": "999", "oblika": "0",
    "ccmMin": "0", "ccmMax": "99999", "mocMin": "", "mocMax": "",
    "kmMin": "0", "kwMin": "0", "kwMax": "999",
    "motortakt": "0", "motorvalji": "0", "lokacija": "0",
    "sirina": "0", "dolzina": "", "dolzinaMIN": "0", "dolzinaMAX": "100",
    "nosilnostMIN": "0", "nosilnostMAX": "999999",
    "lezisc": "", "presek": "0", "premer": "0", "col": "0", "vijakov": "0",
    "EToznaka": "0", "vozilo": "", "airbag": "", "barva": "", "barvaint": "",
    "EQ1": "1000000000", "EQ2": "1000000000", "EQ3": "1000000000",
    "EQ4": "1000000000", "EQ5": "1000000000", "EQ6": "1000000000",
    "EQ7": "1110100120", "EQ8": "1000000001", "EQ9": "1000000000",
    "KAT": "1010000000",
    "PIA": "", "PIAzero": "", "PSLO": "",
    "akcija": "0", "paketgarancije": "", "broker": "0",
    "prikazkategorije": "0", "kategorija": "0",
    "zaloga": "1", "arhiv": "0",
    "presort": "3", "tipsort": "DESC",   # novejsi oglasi naprej
}


def zgradi_url(znamka: str, model: str, stran: int = 1) -> str:
    p = dict(OSNOVA)
    p.update({
        "znamka": znamka,
        "model": model,
        "SUBmodelsearch": model,
        "cenaMax": str(CENA_MAX),
        "kmMax": str(KM_MAX),
        "stran": str(stran),
    })
    return "https://www.avto.net/Ads/results.asp?" + urlencode(p)


# =====================================================================
#  OCENJEVANJE
# =====================================================================

def oceni(ad: dict) -> float:
    """
    ocena = 100
          + (letnik - 2019) * 10
          - (km / 10000) * 6
          - (st. lastnikov - 1) * 12
          + (CENA_MAX - cena) / 250
          - KAZEN_ZA_UVOZ, ce je uvozeno

    Manjkajoci podatki se ne kaznujejo: raje oglas pokazemo,
    kot da bi zaradi neuspesnega parsanja avto usel.
    """
    o = 100.0
    if ad.get("letnik"):
        o += (ad["letnik"] - 2019) * 10
    if ad.get("km") is not None:
        o -= (ad["km"] / 10000) * 6
    if ad.get("lastniki"):
        o -= (ad["lastniki"] - 1) * 12
    if ad.get("cena"):
        o += (CENA_MAX - ad["cena"]) / 250
    if ad.get("uvoz"):
        o -= KAZEN_ZA_UVOZ
    return round(o, 1)


# =====================================================================
#  PRENOS IN PARSANJE
# =====================================================================

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept-Language": "sl-SI,sl;q=0.9,en;q=0.8",
})

ID_RE = re.compile(r"details\.asp\?id=(\d+)", re.I)
TITLE_RE = re.compile(
    r"^(?P<naziv>.+?),\s*letnik:\s*(?P<letnik>\d{4})\s*,\s*(?P<cena>[\d.\s]+)\s*EUR", re.I)
KM_RE = re.compile(r"(\d{1,3}(?:[.\s]\d{3})+|\d{4,6})\s*km\b", re.I)
LASTNIKI_RE = re.compile(r"lastnik\w*[^0-9]{0,25}(\d{1,2})", re.I)
MENJALNIK_RE = re.compile(r"\b(avtomatski|avtomatik|DSG|ročni|rocni)\b", re.I)
DIZEL_RE = re.compile(r"\b(dizel|diesel|TDI|hibrid|elektri|plug-?in)\w*", re.I)
BENCIN_RE = re.compile(r"\b(bencin\w*|TSI|TFSI|MPI)\b", re.I)
UVOZ_RE = re.compile(r"\buvo[zž]\w*", re.I)


def _num(s):
    if not s:
        return None
    s = re.sub(r"[^\d]", "", s)
    return int(s) if s else None


def spanje():
    time.sleep(random.uniform(1.5, 3.0))


def poberi_ide(znamka, model):
    ids = []
    for stran in range(1, MAX_PAGES + 1):
        try:
            r = SESSION.get(zgradi_url(znamka, model, stran), timeout=30)
            r.raise_for_status()
        except Exception as e:
            print(f"  ! stran {stran}: {e}", file=sys.stderr)
            break
        novi = [i for i in ID_RE.findall(r.text) if i not in ids]
        ids.extend(novi)
        if not novi:
            break
        spanje()
    return ids


def parsaj(ad_id):
    url = f"https://www.avto.net/Ads/details.asp?id={ad_id}"
    ad = {"id": ad_id, "url": url}
    try:
        r = SESSION.get(url, timeout=30)
        r.raise_for_status()
    except Exception as e:
        print(f"  ! oglas {ad_id}: {e}", file=sys.stderr)
        ad["naziv"] = f"Oglas {ad_id} (branje ni uspelo)"
        return ad

    soup = BeautifulSoup(r.text, "html.parser")
    naslov = soup.title.get_text(strip=True) if soup.title else ""
    telo = soup.get_text(separator="\n", strip=True)

    m = TITLE_RE.search(naslov)
    if m:
        ad["naziv"] = m.group("naziv").strip()
        ad["letnik"] = int(m.group("letnik"))
        ad["cena"] = _num(m.group("cena"))
    else:
        ad["naziv"] = naslov.split("::")[0].strip() or f"Oglas {ad_id}"

    if (m := KM_RE.search(telo)):
        ad["km"] = _num(m.group(1))
    if (m := LASTNIKI_RE.search(telo)):
        if 1 <= (n := int(m.group(1))) <= 10:
            ad["lastniki"] = n
    if (m := MENJALNIK_RE.search(telo)):
        ad["menjalnik"] = m.group(1).lower()

    vzorec = f"{naslov}\n{telo[:4000]}"
    ad["dizel"] = bool(DIZEL_RE.search(vzorec)) and not BENCIN_RE.search(naslov)
    ad["uvoz"] = bool(UVOZ_RE.search(vzorec))

    if os.environ.get("DEBUG_DUMP"):
        Path(f"debug_{ad_id}.html").write_text(r.text, encoding="utf-8")
    return ad


def obdrzi(ad):
    """Fina sita. Ob manjkajocih podatkih PUSTIMO oglas noter."""
    if SAMO_BENCIN and ad.get("dizel"):
        return False
    if ad.get("km") is not None and ad["km"] > KM_MAX * 1.05:
        return False
    if ad.get("cena") and ad["cena"] > CENA_MAX:
        return False
    return True


# =====================================================================
#  E-POSTA
# =====================================================================

def _f(x, enota=""):
    return f"{x:,}".replace(",", ".") + enota if isinstance(x, int) else "–"


def vrstica(ad):
    barva = "#1a7f37" if ad["ocena"] >= ALERT_THRESHOLD else "#57606a"
    znacke = []
    if ad.get("menjalnik"):
        znacke.append(ad["menjalnik"])
    if ad.get("uvoz"):
        znacke.append("uvoz")
    pod = " · ".join(znacke)
    c = "padding:10px 8px;border-bottom:1px solid #e6e6e6;"
    return f"""<tr>
<td style="{c}"><a href="{ad['url']}" style="color:#0b5cad;font-weight:600;text-decoration:none;">{ad.get('naziv','?')}</a>
<div style="color:#8b949e;font-size:12px;">{pod}</div></td>
<td style="{c}white-space:nowrap;">{ad.get('letnik','–')}</td>
<td style="{c}white-space:nowrap;">{_f(ad.get('km'),' km')}</td>
<td style="{c}white-space:nowrap;">{ad.get('lastniki','–')}</td>
<td style="{c}white-space:nowrap;"><b>{_f(ad.get('cena'),' €')}</b></td>
<td style="{c}color:{barva};font-weight:700;">{ad['ocena']}</td></tr>"""


def html_mail(naslov, uvod, ads):
    return f"""<!doctype html><html><body style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#1a1a1a;">
<h2 style="margin:0 0 4px;">{naslov}</h2>
<p style="margin:0 0 18px;color:#57606a;">{uvod}</p>
<table style="border-collapse:collapse;width:100%;font-size:14px;">
<thead><tr style="text-align:left;background:#f4f5f7;">
<th style="padding:8px;">Vozilo</th><th style="padding:8px;">Letnik</th>
<th style="padding:8px;">Kilometri</th><th style="padding:8px;">Last.</th>
<th style="padding:8px;">Cena</th><th style="padding:8px;">Ocena</th>
</tr></thead><tbody>{''.join(vrstica(a) for a in ads)}</tbody></table>
<p style="margin-top:20px;font-size:12px;color:#8b949e;">
Ocena upošteva letnik, kilometre, število lastnikov in razliko do proračuna
{_f(CENA_MAX)} €. Višje je bolje. Pošlje avto-agent.</p></body></html>"""


def poslji(prejemnik, zadeva, html):
    user, geslo = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASS")
    if not (user and geslo and prejemnik):
        print(f"  ! manjkajo SMTP podatki -> {zadeva}", file=sys.stderr)
        return False
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = zadeva, formataddr(("Avto agent", user)), prejemnik
    msg.set_content("Potrebuješ HTML pogled.")
    msg.add_alternative(html, subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(user, geslo)
        s.send_message(msg)
    print(f"  → poslano: {zadeva} ({prejemnik})")
    return True


# =====================================================================
#  GLAVNI TEK
# =====================================================================

STATE = Path("data/seen.json")


def main(test=False):
    zdaj = datetime.now(TZ)
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() \
        else {"ads": {}, "zadnji_povzetek": None}
    znani = state["ads"]
    prvi_tek = not znani and not test
    novi = []

    for s in ISKANJA:
        print(f"[{s['znamka']} {s['model']}]")
        ide = poberi_ide(s["znamka"], s["model"])
        print(f"  {len(ide)} oglasov ustreza osnovnim filtrom")
        for ad_id in ide:
            if ad_id in znani and not test:
                continue
            ad = parsaj(ad_id)
            ad.update(iskanje=f"{s['znamka']} {s['model']}",
                      najden=zdaj.isoformat())
            ad["ocena"] = oceni(ad)
            spanje()
            if not obdrzi(ad):
                print(f"  - izločen: {ad.get('naziv','?')[:60]}")
                znani[ad_id] = {"najden": zdaj.isoformat(), "izlocen": True}
                continue
            znani[ad_id] = ad
            novi.append(ad)
            if test:
                print(f"  ✓ {ad['ocena']:>6} | {ad.get('letnik','?')} | "
                      f"{_f(ad.get('km'),'km'):>10} | {ad.get('lastniki','?')} last. | "
                      f"{_f(ad.get('cena'),'€'):>9} | {ad.get('naziv','?')[:50]}")
            if test and len(novi) >= 8:
                break
        if test and len(novi) >= 8:
            break

    if test:
        print(f"\n[test] {len(novi)} oglasov uspešno razčlenjenih")
        brez = [a for a in novi if not a.get("letnik") or a.get("km") is None]
        if brez:
            print(f"[test] OPOZORILO: {len(brez)} oglasov s praznimi polji — "
                  "parser potrebuje popravek")
        if novi and os.environ.get("SMTP_USER"):
            poslji(os.environ.get("MAIL_TO_ALERT", os.environ["SMTP_USER"]),
                   "TEST — avto agent deluje",
                   html_mail("Testno sporočilo",
                             "Če to vidiš, pošiljanje deluje. Spodaj vzorec zadetkov.",
                             sorted(novi, key=lambda a: -a["ocena"])))
        return

    print(f"[skupaj] {len(novi)} novih")

    if prvi_tek:
        print("[prvi tek] baza napolnjena, obvestila preskočena")
        state["zadnji_povzetek"] = zdaj.date().isoformat()
    else:
        vroci = sorted([a for a in novi if a["ocena"] >= ALERT_THRESHOLD],
                       key=lambda a: -a["ocena"])
        if vroci:
            poslji(os.environ.get("MAIL_TO_ALERT", ""),
                   f"🚗 {len(vroci)} nov(ih) zanimiv(ih) oglas(ov) — pokliči zdaj",
                   html_mail("Nov oglas nad pragom",
                             f"Ocena {ALERT_THRESHOLD}+. Dobri oglasi gredo v urah, ne dnevih.",
                             vroci))

        danes = zdaj.date().isoformat()
        if zdaj.hour >= DIGEST_HOUR and state.get("zadnji_povzetek") != danes:
            meja = (zdaj - timedelta(hours=24)).isoformat()
            zadnjih24 = sorted(
                [a for a in znani.values()
                 if a.get("najden", "") >= meja and not a.get("izlocen")],
                key=lambda a: -a.get("ocena", 0))[:10]
            if zadnjih24:
                poslji(os.environ.get("MAIL_TO_DIGEST", ""),
                       f"Novi avti danes — {len(zadnjih24)} zadetkov",
                       html_mail("Kaj je novega v zadnjih 24 urah",
                                 "Urejeno po oceni. Klikni na ime za oglas.", zadnjih24))
            else:
                print("[povzetek] nič novega, mail ni poslan")
            state["zadnji_povzetek"] = danes

    meja30 = (zdaj - timedelta(days=30)).isoformat()
    state["ads"] = {k: v for k, v in znani.items() if v.get("najden", "") >= meja30}
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main(test="--test" in sys.argv)
