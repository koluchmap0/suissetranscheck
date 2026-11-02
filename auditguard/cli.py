"""
BUST GO — Sender Bot CLI.
Only the sender/spoof bot command.
"""
from __future__ import annotations
import typer

app = typer.Typer(help="BUST GO — Sender Bot (spoof + campaigns).")


@app.command()
def version():
    """Afficher la version."""
    print("BUST GO Sender 1.0.0")


@app.command("sender")
def sender():
    """Lancer le bot Telegram SENDER (campagnes/spoof)."""
    from .sender_bot import run_sender
    run_sender()


if __name__ == "__main__":
    app()
