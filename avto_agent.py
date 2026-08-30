#!/usr/bin/env python3
"""Avto.net agent -- spremlja nove oglase, oceni jih in obvesti po e-posti."""
import json, os, random, re, smtplib, sys, time
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from zoneinfo import ZoneInfo
from curl_cffi import requests
from bs4 import BeautifulSoup

TZ = ZoneInfo("Europe/Ljubljana")
CENA_MAX, KM_MAX = 16000, 75000
SAMO_BENCIN, KAZEN_ZA_UVOZ = True, 15
DIGEST_HOUR, ALERT_THRESHOLD, MAX_PAGES = 10, 75, 3

ISKANJA = [{"ime": "VW T-Cross", "url": "https://www.avto.net/Ads/results.asp?znamka=Volkswagen&model=T%2DCross&modelID=&tip=katerikoli%20tip&znamka2=&model2=&tip2=katerikoli%20tip&znamka3=&model3=&tip3=katerikoli%20tip&cenaMin=0&cenaMax=16000&letnikMin=0&letnikMax=2090&bencin=201&starost2=999&oblika=0&ccmMin=0&ccmMax=99999&mocMin=&mocMax=&kmMin=0&kmMax=75000&kwMin=0&kwMax=999&motortakt=&motorvalji=&lokacija=0&sirina=&dolzina=&dolzinaMIN=&dolzinaMAX=&nosilnostMIN=&nosilnostMAX=&sedezevMIN=&sedezevMAX=&lezisc=&presek=&premer=&col=&vijakov=&EToznaka=&vozilo=&airbag=&barva=&barvaint=&doseg=&BkType=&BkOkvir=&BkOkvirType=&Bk4=&EQ1=1000000000&EQ2=1000000000&EQ3=1000000000&EQ4=100000000&EQ5=1000000000&EQ6=1000000000&EQ7=1000000120&EQ8=1010000000&EQ9=100000002&EQ10=100000000&EQ11=1000000000&EQ12=122000000&KAT=1010000000&PIA=&PIAzero=&PIAOut=&PSLO=&akcija=&paketgarancije=&broker=&prikazkategorije=&kategorija=&ONLvid=&ONLnak=&zaloga=&arhiv=&presort=&tipsort=&stran="}]

def oceni(a):
    o = 100.0
    if a.get("letnik"):   o += (a["letnik"] - 2019) * 10
    if a.get("km") is not None: o -= (a["km"] / 10000) * 6
    if a.get("lastniki"): o -= (a["lastniki"] - 1) * 12
    if a.get("cena"):     o += (CENA_MAX - a["cena"]) / 250
    if a.get("uvoz"):     o -= KAZEN_ZA_UVOZ
    return round(o, 1)

S = requests.Session(impersonate="chrome")
REF = {"Referer": "https://www.avto.net/"}

ID_RE = re.compile(r"details\.asp\?id=(\d+)", re.I)
TITLE_RE = re.compile(r"^(?P<naziv>.+?),\s*letnik:\s*(?P<letnik>\d{4})\s*,\s*(?P<cena>[\d.\s]+)\s*EUR", re.I)
KM_RE = re.compile(r"(\d{1,3}(?:[.\s]\d{3})+|\d{4,6})\s*km\b", re.I)
LAST_RE = re.compile(r"lastnik\w*[^0-9]{0,25}(\d{1,2})", re.I)
MENJ_RE = re.compile(r"\b(avtomatski|avtomatik|DSG|ročni|rocni)\b", re.I)
DIZ_RE = re.compile(r"\b(dizel|diesel|TDI|hibrid|elektri|plug-?in)\w*", re.I)
BEN_RE = re.compile(r"\b(bencin\w*|TSI|TFSI|MPI)\b", re.I)
UVOZ_RE = re.compile(r"\buvo[zž]\w*", re.I)

def num(s):
    s = re.sub(r"[^\d]", "", s or "")
    return int(s) if s else None

def pavza(): time.sleep(random.uniform(2.0, 4.0))

def stran_url(u, n):
    if re.search(r"[?&]stran=\d*", u):
        return re.sub(r"([?&])stran=\d*", r"\g<1>stran=" + str(n), u, count=1)
    return u + ("&" if "?" in u else "?") + f"stran={n}"

def poberi_ide(url):
    ids = []
    for n in range(1, MAX_PAGES + 1):
        try:
            r = S.get(stran_url(url, n), headers=REF, timeout=30)
            if r.status_code != 200:
                print(f"  ! stran {n}: HTTP {r.status_code}", file=sys.stderr); break
        except Exception as e:
            print(f"  ! stran {n}: {e}", file=sys.stderr); break
        if "error.asp" in str(r.url):
            print(f"  ! stran {n}: preusmerjeno na error.asp", file=sys.stderr); break
        novi = [i for i in ID_RE.findall(r.text) if i not in ids]
        ids += novi
        if not novi: break
        pavza()
    return ids

def parsaj(i):
    url = f"https://www.avto.net/Ads/details.asp?id={i}"
    a = {"id": i, "url": url}
    try:
        r = S.get(url, headers=REF, timeout=30)
        if r.status_code != 200: raise RuntimeError(f"HTTP {r.status_code}")
    except Exception as e:
        print(f"  ! oglas {i}: {e}", file=sys.stderr)
        a["naziv"] = f"Oglas {i} (branje ni uspelo)"; return a
    soup = BeautifulSoup(r.text, "html.parser")
    nas = soup.title.get_text(strip=True) if soup.title else ""
    telo = soup.get_text(separator="\n", strip=True)
    if (m := TITLE_RE.search(nas)):
        a["naziv"], a["letnik"], a["cena"] = m.group("naziv").strip(), int(m.group("letnik")), num(m.group("cena"))
    else:
        a["naziv"] = nas.split("::")[0].strip() or f"Oglas {i}"
    if (m := KM_RE.search(telo)):   a["km"] = num(m.group(1))
    if (m := LAST_RE.search(telo)) and 1 <= int(m.group(1)) <= 10: a["lastniki"] = int(m.group(1))
    if (m := MENJ_RE.search(telo)): a["menjalnik"] = m.group(1).lower()
    v = f"{nas}\n{telo[:4000]}"
    a["dizel"] = bool(DIZ_RE.search(v)) and not BEN_RE.search(nas)
    a["uvoz"] = bool(UVOZ_RE.search(v))
    if os.environ.get("DEBUG_DUMP"): Path(f"debug_{i}.html").write_text(r.text, encoding="utf-8")
    return a

def obdrzi(a):
    if SAMO_BENCIN and a.get("dizel"): return False
    if a.get("km") is not None and a["km"] > KM_MAX * 1.05: return False
    if a.get("cena") and a["cena"] > CENA_MAX: return False
    return True

def f(x, e=""):
    return f"{x:,}".replace(",", ".") + e if isinstance(x, int) else "–"

def html_mail(naslov, uvod, ads):
    c = "padding:10px 8px;border-bottom:1px solid #e6e6e6;"
    vr = "".join(f"""<tr><td style="{c}"><a href="{a['url']}" style="color:#0b5cad;font-weight:600;text-decoration:none;">{a.get('naziv','?')}</a>
<div style="color:#8b949e;font-size:12px;">{' · '.join(x for x in [a.get('menjalnik'), 'uvoz' if a.get('uvoz') else None] if x)}</div></td>
<td style="{c}white-space:nowrap;">{a.get('letnik','–')}</td><td style="{c}white-space:nowrap;">{f(a.get('km'),' km')}</td>
<td style="{c}white-space:nowrap;">{a.get('lastniki','–')}</td><td style="{c}white-space:nowrap;"><b>{f(a.get('cena'),' €')}</b></td>
<td style="{c}color:{'#1a7f37' if a['ocena']>=ALERT_THRESHOLD else '#57606a'};font-weight:700;">{a['ocena']}</td></tr>""" for a in ads)
    return f"""<!doctype html><html><body style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#1a1a1a;">
<h2 style="margin:0 0 4px;">{naslov}</h2><p style="margin:0 0 18px;color:#57606a;">{uvod}</p>
<table style="border-collapse:collapse;width:100%;font-size:14px;"><thead><tr style="text-align:left;background:#f4f5f7;">
<th style="padding:8px;">Vozilo</th><th style="padding:8px;">Letnik</th><th style="padding:8px;">Kilometri</th>
<th style="padding:8px;">Last.</th><th style="padding:8px;">Cena</th><th style="padding:8px;">Ocena</th></tr></thead>
<tbody>{vr}</tbody></table><p style="margin-top:20px;font-size:12px;color:#8b949e;">
Ocena upošteva letnik, kilometre, število lastnikov in razliko do proračuna {f(CENA_MAX)} €.
Višje je bolje. Pošlje avto-agent.</p></body></html>"""

def poslji(kam, zadeva, html):
    u, g = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASS")
    if not (u and g and kam):
        print(f"  ! manjkajo SMTP podatki -> {zadeva}", file=sys.stderr); return
    m = EmailMessage()
    m["Subject"], m["From"], m["To"] = zadeva, formataddr(("Avto agent", u)), kam
    m.set_content("Potrebuješ HTML pogled."); m.add_alternative(html, subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(u, g); s.send_message(m)
    print(f"  → poslano: {zadeva} ({kam})")

STATE = Path("data/seen.json")

def main(test=False):
    zdaj = datetime.now(TZ)
    st = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"ads": {}, "zadnji_povzetek": None}
    znani, novi = st["ads"], []
    prvi = not znani and not test
    try:
        r = S.get("https://www.avto.net/", timeout=30)
        print(f"[seja] naslovnica HTTP {r.status_code}")
        time.sleep(2.0)
    except Exception as e:
        print(f"  ! naslovnica: {e}", file=sys.stderr)
    for s in ISKANJA:
        print(f"[{s['ime']}]")
        ide = poberi_ide(s["url"])
        print(f"  {len(ide)} oglasov ustreza osnovnim filtrom")
        for i in ide:
            if i in znani and not test: continue
            a = parsaj(i)
            a.update(iskanje=s["ime"], najden=zdaj.isoformat())
            a["ocena"] = oceni(a); pavza()
            if not obdrzi(a):
                print(f"  - izločen: {a.get('naziv','?')[:60]}")
                znani[i] = {"najden": zdaj.isoformat(), "izlocen": True}; continue
            znani[i] = a; novi.append(a)
            if test:
                print(f"  ✓ {a['ocena']:>6} | {a.get('letnik','?')} | {f(a.get('km'),'km'):>10} | "
                      f"{a.get('lastniki','?')} last. | {f(a.get('cena'),'€'):>9} | {a.get('naziv','?')[:50]}")
                if len(novi) >= 8: break
        if test and len(novi) >= 8: break
    if test:
        print(f"\n[test] {len(novi)} oglasov uspešno razčlenjenih")
        if (b := [a for a in novi if not a.get("letnik") or a.get("km") is None]):
            print(f"[test] OPOZORILO: {len(b)} oglasov s praznimi polji — parser potrebuje popravek")
        if novi and os.environ.get("SMTP_USER"):
            poslji(os.environ.get("MAIL_TO_ALERT", os.environ["SMTP_USER"]), "TEST — avto agent deluje",
                   html_mail("Testno sporočilo", "Če to vidiš, pošiljanje deluje.",
                             sorted(novi, key=lambda a: -a["ocena"])))
        return
    print(f"[skupaj] {len(novi)} novih")
    if prvi:
        print("[prvi tek] baza napolnjena, obvestila preskočena")
        st["zadnji_povzetek"] = zdaj.date().isoformat()
    else:
        if (vroci := sorted([a for a in novi if a["ocena"] >= ALERT_THRESHOLD], key=lambda a: -a["ocena"])):
            poslji(os.environ.get("MAIL_TO_ALERT", ""),
                   f"{len(vroci)} nov(ih) zanimiv(ih) oglas(ov) — pokliči zdaj",
                   html_mail("Nov oglas nad pragom",
                             f"Ocena {ALERT_THRESHOLD}+. Dobri oglasi gredo v urah, ne dnevih.", vroci))
        danes = zdaj.date().isoformat()
        if zdaj.hour >= DIGEST_HOUR and st.get("zadnji_povzetek") != danes:
            meja = (zdaj - timedelta(hours=24)).isoformat()
            z24 = sorted([a for a in znani.values() if a.get("najden", "") >= meja and not a.get("izlocen")],
                         key=lambda a: -a.get("ocena", 0))[:10]
            if z24:
                poslji(os.environ.get("MAIL_TO_DIGEST", ""), f"Novi avti danes — {len(z24)} zadetkov",
                       html_mail("Kaj je novega v zadnjih 24 urah", "Urejeno po oceni. Klikni na ime za oglas.", z24))
            else:
                print("[povzetek] nič novega, mail ni poslan")
            st["zadnji_povzetek"] = danes
    m30 = (zdaj - timedelta(days=30)).isoformat()
    st["ads"] = {k: v for k, v in znani.items() if v.get("najden", "") >= m30}
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")

if __name__ == "__main__":
    main(test="--test" in sys.argv)
