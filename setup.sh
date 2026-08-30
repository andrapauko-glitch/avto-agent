#!/usr/bin/env bash
#
# Namestitev avto-agenta v enem ukazu.
#
#   bash setup.sh [ime-repozitorija]
#
# Predpogoja (oboje enkratno, oboje v tvojem brskalniku/računu):
#   1) GitHub CLI:  https://cli.github.com   ->  gh auth login
#   2) Gmail geslo za aplikacije:
#      Google račun -> Varnost -> Gesla za aplikacije
#
set -euo pipefail

REPO="${1:-avto-agent}"
zeleno() { printf "\033[32m%s\033[0m\n" "$*"; }
rdece()  { printf "\033[31m%s\033[0m\n" "$*"; }

# ---------------------------------------------------------------- preverjanja
command -v gh  >/dev/null || { rdece "Manjka GitHub CLI. Namesti: https://cli.github.com"; exit 1; }
command -v git >/dev/null || { rdece "Manjka git."; exit 1; }

if ! gh auth status >/dev/null 2>&1; then
  rdece "Nisi prijavljen v GitHub."
  echo  "Zaženi:  gh auth login    (odpre brskalnik, geslo vpišeš tam, ne tukaj)"
  exit 1
fi

UPORABNIK=$(gh api user --jq .login)
zeleno "GitHub: prijavljen kot $UPORABNIK"

# ---------------------------------------------------------------- e-naslovi
echo
read -rp "Tvoj e-naslov (Gmail, s katerega se pošilja): " SMTP_USER
read -rp "Tvoj e-naslov za TAKOJŠNJA obvestila [$SMTP_USER]: " MAIL_ALERT
MAIL_ALERT="${MAIL_ALERT:-$SMTP_USER}"
read -rp "Njen e-naslov za dnevni povzetek ob 10:00: " MAIL_DIGEST

echo
echo "Gmail geslo za aplikacije (16 znakov, se NE izpiše)."
echo "Gre naravnost v GitHub Secrets — nikamor drugam."
read -rsp "Geslo: " SMTP_PASS
echo

[ -n "$SMTP_PASS" ] || { rdece "Geslo je prazno."; exit 1; }

# ---------------------------------------------------------------- repozitorij
if [ ! -d .git ]; then
  git init -q -b main
fi
git add -A
git diff --staged --quiet || git commit -q -m "avto-agent"

if gh repo view "$UPORABNIK/$REPO" >/dev/null 2>&1; then
  zeleno "Repozitorij $REPO že obstaja — samo posodabljam."
  git remote get-url origin >/dev/null 2>&1 || \
    git remote add origin "https://github.com/$UPORABNIK/$REPO.git"
  git push -q -u origin main
else
  # javen: brezplačne Actions minute so neomejene (zasebni ima 2.000/mesec,
  # tek vsakih 15 min jih porabi ~2.900)
  gh repo create "$REPO" --public --source=. --remote=origin --push -d "Sledilnik oglasov avto.net"
  zeleno "Repozitorij ustvarjen: https://github.com/$UPORABNIK/$REPO"
fi

# ---------------------------------------------------------------- skrivnosti
set +x
printf '%s' "$SMTP_USER"   | gh secret set SMTP_USER      --repo "$UPORABNIK/$REPO"
printf '%s' "$SMTP_PASS"   | gh secret set SMTP_PASS      --repo "$UPORABNIK/$REPO"
printf '%s' "$MAIL_ALERT"  | gh secret set MAIL_TO_ALERT  --repo "$UPORABNIK/$REPO"
printf '%s' "$MAIL_DIGEST" | gh secret set MAIL_TO_DIGEST --repo "$UPORABNIK/$REPO"
unset SMTP_PASS
zeleno "Skrivnosti shranjene (šifrirane, tudi ti jih ne moreš več prebrati)."

# ---------------------------------------------------------------- zagon
gh workflow enable avto-agent --repo "$UPORABNIK/$REPO" 2>/dev/null || true
sleep 3
gh workflow run avto-agent --repo "$UPORABNIK/$REPO"

echo
zeleno "Končano. Agent teče vsakih ~15 minut."
echo "Prvi tek samo napolni bazo in NE pošlje maila — to je namerno."
echo
echo "  Dnevnik:     gh run watch --repo $UPORABNIK/$REPO"
echo "  Ročni zagon: gh workflow run avto-agent --repo $UPORABNIK/$REPO"
echo "  Ugasni:      gh workflow disable avto-agent --repo $UPORABNIK/$REPO"
