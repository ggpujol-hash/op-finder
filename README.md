# 🏴‍☠️ OP Finder — Alerteur de displays One Piece TCG

Surveille les boutiques JCC (Philibert, UltraJeux, Magic Bazar, Ludikbazar,
Cardmarket…) et envoie une **alerte Telegram** dès qu'un display ou une
précommande One Piece (set OP-17, sortie août 2026) apparaît ou revient en stock.

## Architecture

```
Scheduler (APScheduler)  ──>  Adapters (1 par site, config CSS)  ──>  Detector (diff)
                                                                          │
Dashboard (FastAPI) <── SQLite (état + alertes + santé) <── Notifier (Telegram)
```

- **Adapters pilotés par config** : ajouter un site = quelques lignes dans `config.yaml`,
  pas de code. Deux types disponibles :
  - `generic_html` : boutiques en HTML server-rendered (rapide, httpx + BeautifulSoup).
  - `playwright_html` : sites rendus en JavaScript (navigateur headless Chromium).
    Mêmes sélecteurs CSS que `generic_html`, avec en plus `wait_for`, `wait_ms`, `scroll`.
- **Anti-spam** : on ne notifie que les *transitions* (apparition, retour en stock,
  changement de prix), jamais l'état stable.
- **Robustesse** : chaque site est isolé — un site cassé n'arrête pas les autres.

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env       # puis remplis le token Telegram

# Pour les sites en JavaScript (Play-In) : installer le navigateur Playwright
python -m playwright install chromium
```

### Configurer Telegram
1. Sur Telegram, parle à **@BotFather** → `/newbot` → récupère le **token**.
2. Envoie un message à ton bot, puis ouvre
   `https://api.telegram.org/bot<TOKEN>/getUpdates` et lis le `chat.id`.
3. Mets `TELEGRAM_BOT_TOKEN` et `TELEGRAM_CHAT_ID` dans `.env`.
4. Teste : `python -m src.main test`

## Utilisation

```bash
python -m src.main once          # un passage sur tous les sites (pour tester)
python -m src.main run           # monitoring en continu (toutes les ~3 min)
python -m src.main test          # message Telegram de test
python -m src.main probe "<URL>" # inspecte une page pour caler les sélecteurs CSS
```

### Dashboard

**Version hébergée (gratuite, auto-mise à jour) :** https://ggpujol-hash.github.io/op-finder/
Publiée sur GitHub Pages à chaque run du workflow (données du monitoring cloud).

**Version locale** (données locales uniquement) :
```bash
uvicorn src.dashboard.app:app --port 8000   # puis http://localhost:8000
# ou générer un snapshot statique :
python -m src.main snapshot site/index.html
```

## Caler les sélecteurs d'un site

Les sélecteurs CSS dans `config.yaml` sont des points de départ et **doivent être
vérifiés** contre le HTML réel de chaque site (les structures changent souvent) :

```bash
python -m src.main probe "https://www.philibertnet.com/fr/recherche?s=one+piece"
```

La commande liste les blocs HTML répétés (candidats `item`) et les liens produit.
Ajuste ensuite `selectors.item / title / link / price` dans `config.yaml`.

## Déploiement 24/7 — GitHub Actions (gratuit)

Le monitoring tourne gratuitement sur les runners GitHub, sans serveur ni Mac
allumé, via [`.github/workflows/monitor.yml`](.github/workflows/monitor.yml).

**Fonctionnement** : chaque exécution boucle ~25 min (check toutes les ~90 s),
relancée toutes les 30 min → cadence réelle ~1m30. L'état (base SQLite) est
conservé entre les exécutions via le cache Actions, ce qui évite les alertes en
double. Au tout premier lancement (cache vide), la base est *amorcée sans alerter*
(`seed`) pour ne pas recevoir tout le catalogue existant d'un coup.

> ⚠️ Le `cron` GitHub peut être retardé (10–30 min). Bon pour les **précommandes**
> et restocks normaux ; pour sniper un sellout en < 1 min, il faut un VPS payant.
> Les **minutes Actions sont illimitées sur un dépôt public** (en privé : ~2000/mois,
> donc cadence bien plus lente).

### Mise en place

```bash
# 1. Pousser le code (le workflow exige le scope "workflow" sur ton token gh) :
gh auth refresh -h github.com -s workflow
git push

# 2. Ajouter les secrets Telegram (récupérés via @BotFather, cf. plus haut) :
gh secret set TELEGRAM_BOT_TOKEN
gh secret set TELEGRAM_CHAT_ID
```

3. Sur GitHub → onglet **Actions** → activer les workflows si demandé → le job
   tourne automatiquement (ou bouton **Run workflow** pour lancer tout de suite).

Sans les secrets, le monitoring tourne quand même mais n'envoie pas d'alerte
(il les écrit seulement dans les logs).

## Alternative : VPS / Raspberry Pi

Pour du vrai sub-minute, un petit VPS (Oracle Cloud *Always Free*, ou Hetzner
~4 €/mois) ou un Raspberry Pi : lance `python -m src.main run` sous `systemd` ou
`tmux`, et le dashboard derrière nginx pour y accéder à distance.

## État des sites (vérifié juin 2026)

**14 sites actifs**, ~278 produits suivis.

| Site | Type | État | Note |
|---|---|---|---|
| Philibert | `generic_html` | ✅ actif | catégorie One Piece |
| UltraJeux | `generic_html` | ✅ actif | catégorie One Piece |
| Play-In (ex-Magic Bazar) | `playwright_html` | ✅ actif | catalogue JS |
| Ludotrotter | `generic_html` | ✅ actif | WooCommerce |
| Comptoir des Écoliers | `generic_html` | ✅ actif | WooCommerce |
| Maxi Rêves | `generic_html` | ✅ actif | WooCommerce/Elementor |
| Games Avenue (Displays) | `generic_html` | ✅ actif | Shopify |
| Games Avenue (Packs EN) | `generic_html` | ✅ actif | Shopify |
| Fantastik | `generic_html` | ✅ actif | PrestaShop |
| Goupiya | `generic_html` | ✅ actif | PrestaShop |
| Fantasy Sphere | `generic_html` | ✅ actif | thème maison |
| Destock TCG | `generic_html` | ✅ actif | thème maison |
| Antre Temps | `generic_html` | ✅ actif | thème maison |
| Parkage | `playwright_html` | ✅ actif | React/MUI, ciblage par URL (`:self`) |
| TCGame | `playwright_html` | ⚠️ désactivé | Wix, classes hachées instables |
| Guizette Family | `playwright_html` | ⚠️ désactivé | Cloudflare bloque |
| Cardmarket | `playwright_html` | ⚠️ désactivé | Cloudflare bloque |
| Ludikbazar | `generic_html` | ⚠️ désactivé | URL catégorie à confirmer (`probe`) |

## Limites connues / suite

- **Cardmarket** : challenge Cloudflare ("Just a moment…") qui bloque même Chromium
  headless + `playwright-stealth`. Pour le passer il faudrait **FlareSolverr**, une
  **API de scraping** (ScrapingBee, Zyte) ou un Chromium **non-headless** sur un vrai
  display. C'est une marketplace de revendeurs, moins prioritaire que les boutiques
  pour repérer les *mises en ligne / précommandes* de displays.
- **Généralistes (Amazon/Fnac/Cdiscount)** : même approche `playwright_html`, anti-bot
  variable selon les sites.
- Pas encore de notifications multi-canal (Discord, e-mail) — facile à brancher
  à côté du `TelegramNotifier`.
