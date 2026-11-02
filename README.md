# BUST GO Sender v1.0.0

Bot Telegram dédié à l'envoi d'emails spoofés et campagnes.

## Features

- **MES SMTP** — Liste et gestion des SMTP AUTH OK
- **ADD SMTP** — Ajout manuel de SMTP (host:port|user:pass|LABEL)
- **SPOOF TEST** — Test rapide d'envoi spoofé avec un SMTP
- **MASS SPOOF TEST** — Test en masse d'une liste de SMTP avec envoi spoofé concurrent
- **TEMPLATES** — Templates pré-configurés (Amazon, Swiss Post, Binance, OKX)
- **CAMPAGNES** — Envoi en masse avec rotation multi-SMTP et rate limiting
- **DMARC Check** — Vérification automatique de la politique DMARC avant envoi

## Architecture

```
auditguard/
  cli.py              # CLI Typer (commande sender)
  sender_bot.py        # Bot Telegram Sender
  campaign_sender.py   # Engine d'envoi, DMARC, quick_spoof_test
  spoof_templates.py   # Templates de spoof (Amazon, Swiss Post, Binance, OKX)
  spoof_engine.py      # Techniques anti-détection (unicode, spinning, headers)
  db.py               # SQLite DB layer (found_creds, settings)
  settings.py          # Settings file-based
```

## Installation

```bash
pip install -e .
```

## Lancement

```bash
# Variables d'environnement requises
export SENDER_BOT_TOKEN="ton_token_telegram"
export SENDER_ADMIN_IDS="ton_chat_id"

# Lancer le bot
bust-go sender
# ou
bust-go-sender
```

## Format SMTP

```
host:port|user:password|LABEL
```

Exemples:
```
smtp.hostinger.mx:587|user@domain.com:mypass|HOSTINGER1
smtp-relay.brevo.com:587|api@brevo.com:xkeysib-xxx|BREVO1
smtp.sendgrid.net:587|apikey:SG.xxx|SENDGRID1
```
