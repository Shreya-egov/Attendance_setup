"""Request body templates from payloads/<service>/<file>.json, as in
api_automation_project. Callers fill in the per-call fields; Hcm.post adds
RequestInfo."""
import json
import os

from utils.config import PAYLOADS_DIR


def load_payload(service, filename):
    with open(os.path.join(PAYLOADS_DIR, service, filename), encoding="utf-8") as fh:
        return json.load(fh)
