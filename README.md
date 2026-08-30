# Avto agent — VW T-Cross / T-Roc

Spremlja avto.net vsakih ~15 minut. Tebi pošlje takojšnje obvestilo za dober oglas,
punci pa vsak dan ob 10:00 povzetek zadnjih 24 ur. Teče na GitHub Actions, zastonj.

---

## Namestitev — en ukaz

```bash
bash setup.sh
```

Skripta ustvari repozitorij, naloži kodo, shrani skrivnosti, vklopi urnik
in sproži prvi tek.

Prej moraš enkratno urediti dvoje. Oboje v **svojem** brskalniku oziroma računu —
tega ne more nihče namesto tebe, tudi jaz ne:

**1. GitHub CLI**
Namesti s [cli.github.com](https://cli.github.com), nato:
```bash
gh auth login
```
Odpre brskalnik, prijaviš se pri GitHubu. Geslo ne gre skozi nobeno skripto.

**2. Gmail geslo za aplikacije**
Google račun → Varnost → vklopi dvostopenjsko preverjanje → **Gesla za aplikacije**
→ ustvari novo. Dobiš 16 znakov. `setup.sh` te zanj vpraša, geslo se ne izpiše
in gre naravnost v GitHub Secrets, kjer je šifrirano.

To je vse. Iskalnih URL-jev ni treba nikjer kopirati — skripta jih sestavi sama.

### Če setup.sh ne moreš pognati

Repozitorij ustvari ročno (**javen** — glej spodaj), naloži datoteke in v
Settings → Secrets and variables → Actions dodaj `SMTP_USER`, `SMTP_PASS`,
`MAIL_TO_ALERT`, `MAIL_TO_DIGEST`.

---

## Preveri, preden zaupaš

```bash
export SMTP_USER="..." SMTP_PASS="..." MAIL_TO_ALERT="..."
python avto_agent.py --test
```

Izpiše prvih 8 zadetkov z razčlenjenimi polji in pošlje testni mail.
Če stolpci letnik/km/cena kažejo `–`, parser potrebuje popravek — poglej
razdelek Težave.

---

## Nastavitve

Vse na vrhu `avto_agent.py`:

| Nastavitev | Privzeto | Kaj počne |
|---|---|---|
| `ISKANJA` | T-Cross, T-Roc | seznam znamk in modelov |
| `CENA_MAX` | `16000` | proračun v EUR |
| `KM_MAX` | `70000` | zgornja meja kilometrov |
| `SAMO_BENCIN` | `True` | izloči dizel, hibrid, elektro |
| `KAZEN_ZA_UVOZ` | `15` | točke dol pri uvoženem — ne izloči ga |
| `DIGEST_HOUR` | `10` | ura dnevnega povzetka |
| `ALERT_THRESHOLD` | `75` | ocena za takojšnje obvestilo |

Kot rezerva sta že pripravljena **Škoda Kamiq, SEAT Arona in VW Taigo** —
isti podvozek MQB A0, isti 1.0 TSI, pogosto 1.000–2.000 € ceneje pri enaki opremi.
Odkomentiraj vrstici v `ISKANJA`.

### Formula

```
ocena = 100
      + (letnik − 2019) × 10
      − (km / 10000) × 6
      − (št. lastnikov − 1) × 12
      + (16000 − cena) / 250
      − 15 če je uvoženo
```

| Vozilo | Ocena |
|---|---|
| 2023, 45.000 km, 1 lastnik, 15.800 € | 114 |
| 2019, 30.000 km, 1 lastnik, 13.500 € | 92 |
| 2021, 60.000 km, 1 lastnik, 15.500 €, uvoz | 71 |

Če je letnik pomembnejši od kilometrov, dvigni `10` na `15`.
Če obratno, dvigni `6` na `9`.

---

## Kako je zgrajeno

**Iskalni URL se sestavi sam** iz znamke, modela, cene in kilometrov — le štirje
parametri, pri katerih sem prepričan, kaj pomenijo. Gorivo in uvoz se filtrirata
v Pythonu iz besedila oglasa, ne prek parametrov portala. Če avto.net kaj
spremeni, pade en regex, ne celotna nastavitev.

**Manjkajoči podatki oglasa ne izločijo.** Če parser ne prebere letnika, oglas
vseeno pride v mail s povezavo. Bolje odveč vrstica kot spregledan avto.

**Prvi tek ne pošlje ničesar** — samo napolni bazo. Sicer bi punca takoj dobila
50 mailov za oglase, ki visijo že tedne.

**Če ni nič novega, mail ne gre ven.** Vsakodnevni prazni mail se neha odpirati
v treh dneh.

---

## Zakaj javen repozitorij

Zasebni ima na brezplačnem paketu 2.000 minut Actions mesečno, tek vsakih 15 minut
jih porabi približno 2.900. Javni je neomejen. V kodi ni ničesar osebnega —
e-naslova in geslo so v Secrets, ki so šifrirani in nevidni tudi v javnem
repozitoriju. Če hočeš vseeno zasebnega, v `.github/workflows/avto.yml`
spremeni cron v `*/30 * * * *`.

---

## Vzdrževanje

```bash
gh run watch --repo <uporabnik>/avto-agent    # dnevnik zadnjega teka
gh workflow run avto-agent                    # ročni zagon
gh workflow disable avto-agent                # ugasni, ko je avto kupljen
```

### Težave

**Oglasi s praznimi polji, ocena točno 100.**
Avto.net je spremenil postavitev. Poženi:
```bash
DEBUG_DUMP=1 python avto_agent.py --test
```
Dobiš `debug_<id>.html` — pošlji mi ga in popravim regexe.

**Actions se ustavijo po 60 dneh.**
GitHub ugasne cron v neaktivnih repozitorijih. Naš workflow po vsakem teku
commita `data/seen.json`, zato se to ne zgodi.

**Cron zamuja.**
GitHub Actions cron ni natančen; ob obremenitvi zamuja 5–20 minut.
`*/15` realno pomeni vsakih 15–40 minut. Za avto.net še vedno dovolj hitro.

**Pogoji uporabe.**
Med zahtevki je 1,5–3 sekunde premora, glava je normalna — obnaša se kot počasen
človek. Ne dvigaj `MAX_PAGES` ali frekvence brez potrebe.

---

## Pred nakupom

- **DSG (DQ200, 7-stopenjski, suhi sklopki)** — zahtevaj dokazan servis mehatronike.
  Ročni menjalnik je pri tem modelu manj tvegana izbira.
- Servisna knjižica pri pooblaščenem, oljni servis na 15.000 km.
- Zadnja klop pri T-Crossu se premika naprej/nazaj — dober pokazatelj dejanske rabe.
- Poročilo carVertical ali autoDNA, tudi za slovensko vozilo.
- Preveri, ali je navedena cena z DDV in ali gre za "ceno s financiranjem"
  (osnovna cena je takrat višja).
