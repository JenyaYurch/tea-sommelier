"""Load the gitignored local .env so pytest sees the same Gemini key as the app."""

from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
