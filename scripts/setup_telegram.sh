#!/usr/bin/env bash
# Assistant de configuration Telegram pour OP Finder.
# Recupere le chat_id, envoie un message test, et enregistre les secrets GitHub.
#
# Usage :  bash scripts/setup_telegram.sh
set -euo pipefail

echo "=============================================="
echo "  OP Finder — configuration Telegram"
echo "=============================================="
echo

# 1) Token
read -rp "1. Colle le token donne par @BotFather : " TOKEN
TOKEN="$(echo "$TOKEN" | tr -d '[:space:]')"
if [ -z "$TOKEN" ]; then echo "Token vide, abandon."; exit 1; fi

# Verifie que le token est valide
BOTNAME="$(curl -s "https://api.telegram.org/bot${TOKEN}/getMe" \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print(d["result"]["username"]) if d.get("ok") else sys.exit("Token invalide")' )" \
  || { echo "❌ Token invalide. Recopie-le depuis @BotFather."; exit 1; }
echo "   ✅ Bot reconnu : @${BOTNAME}"
echo

# 2) L'utilisateur doit ecrire au bot en premier
echo "2. Ouvre Telegram, va sur @${BOTNAME} et envoie-lui un message"
echo "   (n'importe quoi, ex. : bonjour)."
read -rp "   Quand c'est fait, appuie sur Entree..." _
echo

# 3) Recupere le chat_id depuis getUpdates
echo "3. Recherche de ton chat_id..."
CHAT_ID=""
for i in 1 2 3 4 5; do
  CHAT_ID="$(curl -s "https://api.telegram.org/bot${TOKEN}/getUpdates" \
    | python3 -c 'import sys,json; d=json.load(sys.stdin); ids=[u["message"]["chat"]["id"] for u in d.get("result",[]) if "message" in u]; print(ids[-1] if ids else "")')"
  [ -n "$CHAT_ID" ] && break
  echo "   ...pas encore vu de message, nouvel essai ($i/5)"; sleep 2
done

if [ -z "$CHAT_ID" ]; then
  echo "❌ Aucun message trouve. Verifie que tu as bien ecrit a @${BOTNAME}, puis relance."
  exit 1
fi
echo "   ✅ chat_id trouve : ${CHAT_ID}"
echo

# 4) Message test
echo "4. Envoi d'un message test..."
curl -s "https://api.telegram.org/bot${TOKEN}/sendMessage" \
  --data-urlencode "chat_id=${CHAT_ID}" \
  --data-urlencode "text=✅ OP Finder est connecte. Les alertes displays One Piece arriveront ici." \
  >/dev/null && echo "   ✅ Regarde Telegram : tu dois avoir recu un message." || echo "   ⚠️  Envoi echoue."
echo

# 5) Enregistre les secrets GitHub (si gh dispo)
if command -v gh >/dev/null 2>&1; then
  read -rp "5. Enregistrer ces valeurs comme secrets GitHub maintenant ? [O/n] " ANS
  if [[ "${ANS:-O}" =~ ^[OoYy]?$ ]]; then
    printf '%s' "$TOKEN"   | gh secret set TELEGRAM_BOT_TOKEN
    printf '%s' "$CHAT_ID" | gh secret set TELEGRAM_CHAT_ID
    echo "   ✅ Secrets enregistres sur GitHub."
  fi
fi

# 6) Ecrit aussi un .env local (pour tester en local)
ENV_FILE="$(dirname "$0")/../.env"
{
  echo "TELEGRAM_BOT_TOKEN=${TOKEN}"
  echo "TELEGRAM_CHAT_ID=${CHAT_ID}"
  echo "CHECK_INTERVAL_SECONDS=180"
  echo "CHECK_JITTER_SECONDS=45"
} > "$ENV_FILE"
echo
echo "✅ Termine. Fichier .env cree pour les tests locaux."
echo "   Test local :  python -m src.main test"
