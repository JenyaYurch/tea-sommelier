"""Load the gitignored local .env so pytest sees the same Gemini key as the app."""

from pathlib import Path

import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


@pytest.fixture(autouse=True)
def _fx_rates_offline():
    """Shop-price tests must not open a socket. A test can install its own quote."""
    from tea_agent.fx_rates import clear_fx_cache, set_fx_provider

    set_fx_provider(lambda _now: None)
    clear_fx_cache()
    yield
    set_fx_provider(None)
    clear_fx_cache()
