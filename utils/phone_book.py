"""
Users created with a campaign: their roles, and sequential phone numbers that
are unique and tracked across runs.
"""
import datetime as dt
import json

from utils.client import die
from utils.config import PHONE_BOOK, TENANT, USER_PHONE_START

ROLE_ALIASES = {"PAYMENT_APPROVAL": "PAYMENT_APPROVER"}


def parse_roles(text):
    roles = [ROLE_ALIASES.get(r.strip().upper(), r.strip().upper())
             for r in (text or "").split(",") if r.strip()]
    if len(set(roles)) != len(roles):
        die(f"duplicate role in the user list: {roles}")
    return roles


class PhoneBook:
    """
    Sequential 10-digit phone numbers for created users, so every user is
    unique and the numbers can be traced. State lives in data/issued_users.json:
    {"next": <number>, "issued": [{phone, role, name, campaign, ...}]}. The
    next run continues where the last stopped; HCM_USER_PHONE_START only sets
    the first number (or jumps ahead if set higher).
    """

    planned = 0  # numbers previewed by plans earlier in this process

    def __init__(self, path=PHONE_BOOK):
        self.path = path
        try:
            with open(path) as fh:
                self.data = json.load(fh)
        except FileNotFoundError:
            self.data = {"next": None, "issued": []}
        start = USER_PHONE_START
        self.data["next"] = max(self.data.get("next") or start, start)

    def peek(self, count, skip=0):
        return [self.data["next"] + skip + i for i in range(count)]

    def _taken(self, hcm, phone):
        """True if the user service already has this mobile number. A number
        it holds fails validation (HCM_USER_PHONE_NUMBER_EXISTS)."""
        body = hcm.post("/user/_search", {"tenantId": TENANT, "mobileNumber": str(phone),
                                          "pageSize": 1}, expect=(200,))
        return bool(body.get("user"))

    def issue(self, hcm, role, campaign_number):
        while True:
            phone = self.data["next"]
            if phone > 9999999999:
                die("phone numbers exhausted - set HCM_USER_PHONE_START lower")
            self.data["next"] = phone + 1
            if self._taken(hcm, phone):
                print(f"  6 users          {phone} is already registered, skipped")
                continue
            entry = {"phone": phone, "role": role,
                     "name": f"{role.replace('_', ' ').title()} {str(phone)[-4:]}",
                     "campaign": campaign_number,
                     "issuedAt": dt.datetime.now().isoformat(timespec="seconds")}
            self.data["issued"].append(entry)
            self.save()
            return entry

    def save(self):
        with open(self.path, "w") as fh:
            json.dump(self.data, fh, indent=2)
