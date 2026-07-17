"""
Central configuration for the temp-mail backend.

Everything here can be overridden with environment variables (see .env.example).
"""
import os
from typing import List
from dotenv import load_dotenv

load_dotenv()


def _split_csv(value: str) -> List[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


class Settings:
    # Domains you control and that MX records point to.
    # Until you own a real domain, this placeholder is fine for local testing --
    # the SMTP server will still accept mail addressed to it if you send mail to
    # it directly (e.g. from a local test script or a dev SMTP relay).
    DOMAINS: List[str] = _split_csv(
        os.getenv("TEMPMAIL_DOMAINS", "example-temp.test")
    )

    # How long (minutes) an inbox and its messages live before being purged.
    INBOX_TTL_MINUTES: int = int(os.getenv("TEMPMAIL_INBOX_TTL_MINUTES", "3"))

    # How often (seconds) the cleanup task sweeps for expired data.
    CLEANUP_INTERVAL_SECONDS: int = int(os.getenv("TEMPMAIL_CLEANUP_INTERVAL", "5"))

    # Database
    MONGO_URL: str = os.getenv(
        "TEMPMAIL_MONGO_URL", "mongodb://localhost:27017/?authSource=admin"
    )
    MONGO_DB: str = os.getenv(
        "TEMPMAIL_MONGO_DB", "tempmail"
    )

    # SMTP server bind settings.
    # Real inbound mail arrives on port 25. Locally, unprivileged ports (e.g. 1025)
    # are easier to bind without root -- use a relay/forwarder to get real mail
    # to that port once you have a domain + server.
    SMTP_HOST: str = os.getenv("TEMPMAIL_SMTP_HOST", "0.0.0.0")
    SMTP_PORT: int = int(os.getenv("TEMPMAIL_SMTP_PORT", "1025"))

    # Max message size accepted (bytes). Guards against abuse.
    MAX_MESSAGE_SIZE: int = int(os.getenv("TEMPMAIL_MAX_MESSAGE_SIZE", str(10 * 1024 * 1024)))

    # API server settings
    API_HOST: str = os.getenv("TEMPMAIL_API_HOST", "0.0.0.0")
    API_PORT: int = int(os.getenv("TEMPMAIL_API_PORT", "8000"))


settings = Settings()
