#!/usr/bin/env python3
"""
Campaign setup helper.

  ./setup_campaign.py CMP-2026-09-08-005089            # inspect only (default)
  ./setup_campaign.py CMP-2026-09-08-005089 --payment --yes

Given a campaign number it can:

  [WORKS]    resolve the campaign  (project-factory/v1/project-type/search)
  [WORKS]    report register + attendee status  (health-attendance/v1/_search)
  [WORKS]    set up payment attributes  (billing-config/v1/_create)
  [BLOCKED]  create registers      - see BLOCKED note below
  [BLOCKED]  map attendees         - see BLOCKED note below

BLOCKED, and why - not an oversight:

  * health-attendance/v1/_create and _update EXIST but return
      401 CustomException "You are not authorized to access this resource"
    for a CAMPAIGN_MANAGER token. Direct register/attendee CRUD is role-gated;
    the UI never uses it.
  * The UI creates registers and maps attendees by uploading an xlsx through
    excel-ingestion (/excel-ingestion/v1/data/process/_validation). Those request
    bodies were never captured, so they cannot be reproduced here.

  To unblock, capture one real UI run of "Create registers" and "Map attendees"
  with hcm-automation-notes/tracker-inject.js loaded, then read window.__netlog2.

Every contract used below was verified by live probe against hcm-demo.
"""
import argparse
import base64
import json
import os
import sys
import time

import requests

BASE = os.environ.get("HCM_BASE_URL", "https://hcm-demo.digit.org").rstrip("/")
TENANT = os.environ.get("HCM_TENANT_ID", "demo")


def die(msg, code=1):
    print(f"\n  ERROR  {msg}\n", file=sys.stderr)
    sys.exit(code)


class Hcm:
    def __init__(self):
        self.s = requests.Session()
        self.token = None
        self.user = None

    # ---- auth ----------------------------------------------------------

    def login(self):
        user = os.environ.get("HCM_USERNAME")
        pwd = os.environ.get("HCM_PASSWORD")
        if not (user and pwd):
            die("set HCM_USERNAME and HCM_PASSWORD")

        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        basic = os.environ.get("HCM_BASIC_AUTH")
        if basic:
            headers["Authorization"] = f"Basic {basic}"

        r = self.s.post(
            f"{BASE}/user/oauth/token",
            data={
                "username": user, "password": pwd, "check": "true",
                "userType": "EMPLOYEE", "tenantId": TENANT,
                "scope": "read", "grant_type": "password",
            },
            headers=headers, params={"_": int(time.time() * 1000)}, timeout=60,
        )
        if r.status_code != 200:
            msg = ""
            try:
                msg = r.json().get("message", "")
            except Exception:
                msg = r.text[:200]
            if "Full authentication is required" in msg:
                die(
                    "/user/oauth/token needs a static Basic client credential.\n"
                    "         export HCM_BASIC_AUTH=$(printf '<id>:<secret>' | base64 -w0)\n"
                    "         Find it in the Authorization header of a real browser login."
                )
            die(f"login failed ({r.status_code}): {msg}")

        b = r.json()
        self.token = b["access_token"]
        self.user = b.get("UserRequest")
        return b

    def roles(self):
        return [x["code"] for x in (self.user or {}).get("roles", [])]

    def _ri(self):
        return {
            "apiId": "Rainmaker", "ver": ".01", "ts": "", "action": "", "did": "1",
            "key": "", "msgId": f"{int(time.time()*1000)}|en_IN",
            "authToken": self.token, "userInfo": self.user,
        }

    def post(self, path, payload=None, params=None, expect=(200,)):
        body = dict(payload or {})
        body["RequestInfo"] = self._ri()
        r = self.s.post(f"{BASE}{path}", json=body, params=params, timeout=60)
        try:
            j = r.json()
        except Exception:
            j = {"__raw": r.text[:400]}
        if r.status_code not in expect:
            errs = "; ".join(
                f"{e.get('code')}: {e.get('message')}" for e in j.get("Errors", [])
            ) or str(j)[:400]
            die(f"{path} -> {r.status_code}\n         {errs}")
        return j

    # ---- reads ---------------------------------------------------------

    def campaign(self, number):
        """project-factory search by campaignNumber. Gives projectId + dates."""
        j = self.post(
            "/project-factory/v1/project-type/search",
            {"CampaignDetails": {"tenantId": TENANT, "campaignNumber": number}},
        )
        rows = j.get("CampaignDetails") or []
        if not rows:
            die(f"campaign {number} not found in tenant {TENANT}")
        return rows[0]

    def registers(self, campaign_number):
        """
        health-attendance search.
        VERIFIED: tenantId MUST be a query param - in the body it fails with
        400 TENANT_ID "Tenant is mandatory" regardless of wrapper key.
        VERIFIED: filter on campaignNumber; referenceId alone is rejected
        (needs localityCode alongside it).
        """
        return self.post(
            "/health-attendance/v1/_search", {},
            params={"tenantId": TENANT, "campaignNumber": campaign_number},
        )

    def billing_config(self, campaign_number=None):
        crit = {"tenantId": TENANT}
        if campaign_number:
            crit["campaignNumber"] = campaign_number
        return self.post(
            "/health-expense-calculator/billing-config/v1/_search",
            {"searchCriteria": crit}, params={"tenantId": TENANT},
        )

    # ---- write: payment attributes -------------------------------------

    def create_billing_config(self, campaign, frequency, custom_days, dry_run=True):
        """
        POST /health-expense-calculator/billing-config/v1/_create

        Required fields (from the server's own NotNull errors):
          tenantId, campaignNumber, projectId, projectStartDate,
          projectEndDate, billingFrequency
        customFrequencyDays is required when billingFrequency == CUSTOM.
        """
        cfg = {
            "tenantId": TENANT,
            "campaignNumber": campaign["campaignNumber"],
            "projectId": campaign["projectId"],
            "projectStartDate": campaign["startDate"],
            "projectEndDate": campaign["endDate"],
            "billingFrequency": frequency,
            "active": True,
        }
        if frequency == "CUSTOM":
            cfg["customFrequencyDays"] = custom_days

        if dry_run:
            print("  DRY RUN - would POST billing-config/v1/_create with:")
            print(indent(json.dumps({"billingConfig": cfg}, indent=2)))
            return None

        return self.post(
            "/health-expense-calculator/billing-config/v1/_create",
            {"billingConfig": cfg}, params={"tenantId": TENANT},
        )


def indent(s, pad="      "):
    return "\n".join(pad + ln for ln in s.splitlines())


def human_date(ms):
    if not ms:
        return "-"
    return time.strftime("%d %b %Y", time.localtime(ms / 1000))


BLOCKED_NOTE = """
  BLOCKED - cannot be scripted yet:

    create registers   health-attendance/v1/_create  -> 401 for CAMPAIGN_MANAGER
    map attendees      health-attendance/v1/_update  -> 401 for CAMPAIGN_MANAGER

  The UI does both by uploading an xlsx through
  /excel-ingestion/v1/data/process/_validation, and that request body was never
  captured, so it cannot be reproduced.

  To unblock: run "Create registers" and "Map attendees" once in the UI with
  hcm-automation-notes/tracker-inject.js loaded, then read window.__netlog2 and
  fill suite/payloads/registers_create.json and map_attendees.json.
"""


def main():
    ap = argparse.ArgumentParser(
        description="Inspect a campaign and set up its payment attributes.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=BLOCKED_NOTE,
    )
    ap.add_argument("campaign_number", help="e.g. CMP-2026-09-08-005089")
    ap.add_argument("--payment", action="store_true",
                    help="create the billing config (payment attributes)")
    ap.add_argument("--frequency", default="CUSTOM",
                    choices=["CUSTOM", "WEEKLY", "MONTHLY", "DAILY"],
                    help="billingFrequency (observed value: CUSTOM)")
    ap.add_argument("--custom-days", type=int, default=3,
                    help="customFrequencyDays when frequency is CUSTOM (observed: 3)")
    ap.add_argument("--yes", action="store_true",
                    help="actually write. Without it, writes are dry-run.")
    args = ap.parse_args()

    hcm = Hcm()
    hcm.login()
    print(f"\n  logged in as {hcm.user.get('userName')}  roles={hcm.roles()}  tenant={TENANT}")

    # ---- 1. campaign
    c = hcm.campaign(args.campaign_number)
    print(f"\n  CAMPAIGN  {c['campaignNumber']}")
    print(f"    name          {c.get('campaignName')}")
    print(f"    status        {c.get('status')}")
    print(f"    projectType   {c.get('projectType')}")
    print(f"    hierarchy     {c.get('hierarchyType')}")
    print(f"    period        {human_date(c.get('startDate'))} -> {human_date(c.get('endDate'))}")
    print(f"    id            {c.get('id')}")
    print(f"    projectId     {c.get('projectId')}")
    if not c.get("projectId"):
        die("campaign has no projectId - payment attributes cannot be created")

    # ---- 2. registers + attendees
    reg = hcm.registers(args.campaign_number)
    regs = reg.get("attendanceRegister") or []
    print(f"\n  REGISTERS  totalCount={reg.get('totalCount')}  statusCount={reg.get('statusCount')}")
    if not regs:
        print("    none yet - registers have not been generated for this campaign")
    for r in regs:
        att = r.get("attendees") or []
        stf = r.get("staff") or []
        print(f"    {r['registerNumber']}  {r.get('status')}/{r.get('reviewStatus')}")
        print(f"      locality    {r.get('localityCode')}")
        print(f"      serviceCode {r.get('serviceCode')}")
        print(f"      attendees   {len(att)}   staff {len(stf)}")
        bad = [a for a in att if not a.get("individualId")]
        if bad:
            print(f"      WARNING: {len(bad)} attendee(s) with no individualId")

    # ---- 3. payment attributes
    existing = hcm.billing_config(args.campaign_number)
    cur = existing.get("billingConfig")
    has_cfg = bool(cur) and cur.get("campaignNumber") == args.campaign_number
    print("\n  PAYMENT ATTRIBUTES")
    if has_cfg:
        print(f"    already configured: {cur.get('billingFrequency')}"
              f" every {cur.get('customFrequencyDays')}d,"
              f" status={cur.get('status')}, periods={existing.get('totalPeriods')}")
    else:
        print("    not configured for this campaign")

    if args.payment:
        if has_cfg:
            print("    skipping create - a config already exists (would duplicate)")
        else:
            res = hcm.create_billing_config(
                c, args.frequency, args.custom_days, dry_run=not args.yes
            )
            if res:
                new = res.get("billingConfig") or {}
                print(f"    CREATED  id={new.get('id')}"
                      f"  {new.get('billingFrequency')}"
                      f"  every {new.get('customFrequencyDays')}d")
            elif not args.yes:
                print("    (re-run with --yes to actually create)")
    else:
        print("    pass --payment to configure it")

    print(BLOCKED_NOTE)


if __name__ == "__main__":
    main()
