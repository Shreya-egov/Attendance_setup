"""
Campaigns created by test_10, per campaign type, in output/campaigns.json -
so the later stages (registers, mapping, payment) run against them, in the
same session or a later one (``pytest tests/test_30_user_mapping.py``).
Mirrors save_campaign_result / load_campaign_result in api_automation_project.
"""
import json
import os

from utils.config import OUTPUT_DIR

STORE = os.path.join(OUTPUT_DIR, "campaigns.json")


def _read():
    try:
        with open(STORE, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, ValueError):
        return {}


def save_campaign_result(campaign_type, data):
    store = _read()
    store[campaign_type] = {**store.get(campaign_type, {}), **data}
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(STORE, "w", encoding="utf-8") as fh:
        json.dump(store, fh, indent=2, default=list)
    return STORE


def load_campaign_result(campaign_type):
    return _read().get(campaign_type)


def all_campaign_results():
    return _read()
