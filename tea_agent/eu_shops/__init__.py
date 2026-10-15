"""Open-catalog readers for the PL/DE Chinese green price index.

The refresh job writes ``data/eu_green_price_index.json``. A later
``find_best_price`` tool should read that file and not call shops itself.
"""
