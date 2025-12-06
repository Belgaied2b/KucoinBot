# =====================================================================
# main.py — Entry point du bot institutionnel Bitget (Async)
# =====================================================================
import asyncio
import os
from dotenv import load_dotenv
from scanner import run_scanner


def load_env():
    load_dotenv()

    missing = []
    env_vars = ["API_KEY", "API_SECRET", "API_PASSPHRASE", "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID"]

    for v in env_vars:
        if os.getenv(v) in [None, ""]:
            missing.append(v)

    if missing:
        print("⚠️ Variables manquantes:", missing)
        print("Veuillez compléter votre fichier .env")
        exit(1)


async def main():
    print("🚀 Bot Bitget Institutionnel — Démarrage...")

    try:
        await run_scanner()
    except Exception as e:
        print("❌ ERREUR GLOBALE :", e)
        await asyncio.sleep(5)
        print("🔁 Redémarrage automatique...")
        await main()


if __name__ == "__main__":
    load_env()
    asyncio.run(main())
