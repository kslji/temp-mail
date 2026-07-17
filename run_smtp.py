"""
Run the inbound SMTP server standalone.

    python run_smtp.py

Keep this running alongside the API (uvicorn app.main:app). In production,
run it as a systemd service / supervisor process bound to port 25 (needs
root or setcap 'cap_net_bind_service') once your domain's MX record points here.
"""
import asyncio
import logging

from app.database import init_db
from app.smtp_server import start_smtp_server

logging.basicConfig(level=logging.INFO)


async def main():
    await init_db()
    controller = start_smtp_server()
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        controller.stop()


if __name__ == "__main__":
    asyncio.run(main())
