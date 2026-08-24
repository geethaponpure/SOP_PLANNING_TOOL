"""App package init -- load backend/.env before anything reads configuration.

Safe no-op if python-dotenv isn't installed or no .env exists (synthetic mode
needs neither).
"""
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:  # noqa: BLE001 -- dotenv optional in synthetic mode
    pass
