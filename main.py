# =====================================================================
# main.py — Entry point Railway Bitget Bot
# =====================================================================

import asyncio
import logging
from scanner import start_scanner

logging.basicConfig(level=logging.INFO)

print("🚀 Bot Bitget Institutionnel — Démarrage...")

async def main():
    try:
        await start_scanner()   # <= START SCANNER IS NOW ASYNC SAFE
    except Exception as e:
        print(f"❌ ERREUR GLOBALE : {e}")

if __name__ == "__main__":
    # IMPORTANT : we do NOT call asyncio.run() inside an already running loop
    try:
        # If no event loop is running → use asyncio.run normally
        asyncio.run(main())
    except RuntimeError:
        # If Railway or PTB already created a loop → reuse it
        loop = asyncio.get_event_loop()
        loop.create_task(main())
        loop.run_forever()
