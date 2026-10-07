"""
Payment setup for a campaign - what payments-ui "Setup Payment Attributes"
submits: a billing cycle (health-expense-calculator billing-config) and the
per-role wages (MDMS HCM.WORKER_RATES).

plan_payment() validates against MDMS the way the UI does - billing code in
BillingCycle, CUSTOM days within min/maxDuration, rates within the campaign
type's rateMaxLimitSchema - and returns only what differs from what is saved,
so applying it twice is a no-op.

``args`` needs .billing, .days, .rates, .rate (list of SKILL=F,T,P) and .yes,
as the CLI parses them; tests pass a SimpleNamespace.
"""
import json

from utils.cli import require_roles
from utils.client import die, fmt_date
from utils.config import TENANT

PAYMENT_ROLES = {"CAMPAIGN_MANAGER"}
RATE_KEYS = ("FOOD", "TRAVEL", "PER_DAY")


def parse_rate(text, what):
    try:
        vals = [float(x) for x in text.split(",")]
    except ValueError:
        vals = []
    if len(vals) != 3:
        die(f"{what} must be FOOD,TRAVEL,PER_DAY numbers, got {text!r}")
    return {k: int(v) if v.is_integer() else v for k, v in zip(RATE_KEYS, vals)}


def plan_payment(hcm, campaign, args, user_uuid):
    """Returns (config_change, rates_change) - each None when nothing to do -
    after validating against MDMS the way the UI does."""
    number = campaign["campaignNumber"]
    root = campaign.get("projectId")
    if not root:
        die("campaign has no root projectId yet - it has not finished creating")

    # ---- billing cycle -------------------------------------------------
    cycles = hcm.billing_cycles()
    if not cycles:
        die("MDMS HCM-BILLING-CONFIG-PAYMENT-SETUP.BillingCycle has no records in "
              f"tenant {TENANT}.\n         The UI disables billing-cycle selection "
              "too; seed it first (WEEKLY, BI_WEEKLY, MONTHLY, CUSTOM).")
    freq = (args.billing or "").upper()
    if freq not in cycles:
        die(f"--billing {args.billing!r} is not configured. Options: {sorted(cycles)}")
    days = None
    if freq == "CUSTOM":
        c = cycles["CUSTOM"]
        lo, hi = c.get("minDuration", 1), c.get("maxDuration", 10 ** 6)
        if args.days is None:
            die("--billing CUSTOM needs --days")
        if not lo <= args.days <= hi:
            die(f"--days must be between {lo} and {hi} (MDMS CUSTOM cycle)")
        days = args.days
    elif args.days is not None:
        print(f"  NOTE     --days ignored for {freq}")

    existing, periods = hcm.billing_config(number)
    config = {
        "tenantId": TENANT, "campaignNumber": number, "billingFrequency": freq,
        "projectStartDate": campaign.get("startDate"),
        "projectEndDate": campaign.get("endDate"),
        "status": "ACTIVE", "createdBy": user_uuid, "projectId": root,
    }
    if days is not None:
        config["customFrequencyDays"] = str(days)   # the UI sends it as a string
    config_change = None
    if existing:
        same = (existing.get("billingFrequency") == freq
                and (days is None or int(existing.get("customFrequencyDays") or 0) == days))
        print(f"billing    existing {existing.get('billingFrequency')}"
              f"{' / ' + str(existing.get('customFrequencyDays')) + ' days' if existing.get('customFrequencyDays') else ''}"
              f", {len(periods)} period(s)" + ("  - unchanged" if same else f"  -> {freq}"))
        if not same:
            config_change = ("update", {**config, "id": existing["id"]})
    else:
        print(f"billing    none yet -> {freq}{f' every {days} days' if days else ''}")
        config_change = ("create", config)

    # ---- wages ---------------------------------------------------------
    skills_row = hcm.campaign_type_skills(campaign.get("projectType"))
    if not skills_row or not skills_row.get("skills"):
        die(f"no CampaignTypeSkills for project type {campaign.get('projectType')!r}"
              " - the UI shows 'no roles found' too")
    skills = [s["code"] for s in skills_row["skills"]]
    limits = skills_row.get("rateMaxLimitSchema") or {}

    current = hcm.worker_rates(root)
    current_rates = {r["skillCode"]: r.get("rateBreakup") or {}
                     for r in ((current or {}).get("data") or {}).get("rates") or []}
    base = parse_rate(args.rates, "--rates") if args.rates else None
    overrides = {}
    for item in args.rate:
        skill, _, vals = item.partition("=")
        skill = skill.strip().upper()
        if skill not in skills:
            die(f"--rate {skill}: not a wage role for {campaign.get('projectType')}."
                  f" Roles: {skills}")
        overrides[skill] = parse_rate(vals, f"--rate {skill}")

    rates = {}
    for s in skills:
        r = overrides.get(s) or base or current_rates.get(s)
        if r is None:
            die(f"no rate for {s}: pass --rates F,T,P (no existing rate record)")
        for k in RATE_KEYS:
            if k in limits and r.get(k, 0) > limits[k]:
                die(f"{s} {k}={r[k]} exceeds the limit {limits[k]} (rateMaxLimitSchema)")
        rates[s] = {k: r.get(k, 0) for k in RATE_KEYS}

    data = {
        "name": campaign.get("campaignName"), "eventType": "CAMPAIGN",
        "currency": ((current or {}).get("data") or {}).get("currency", "USD"),
        "rates": [{"skillCode": s, "rateBreakup": rates[s]} for s in skills],
        "campaignId": root,   # the root project id, as the UI stores it
    }
    for s in skills:
        r = rates[s]
        print(f"wages      {s:<24} food {r['FOOD']:<5} travel {r['TRAVEL']:<5}"
              f" per-day {r['PER_DAY']:<5} = {sum(r.values())}")
    rates_change = None
    if current and {s: current_rates.get(s) for s in skills} == rates:
        print("           rate record exists and matches - unchanged")
    else:
        rates_change = ("update" if current else "create", data, current)
    return config_change, rates_change


def apply_payment(hcm, number, config_change, rates_change):
    if config_change:
        verb, cfg = config_change
        saved = hcm.save_billing_config(cfg, update=(verb == "update"))
        print(f"  SAVED    billing config {verb}d: {saved.get('id')}")
    if rates_change:
        verb, data, current = rates_change
        hcm.save_worker_rates(data, existing=current)
        print(f"  SAVED    worker rates {verb}d")
    cfg, periods = hcm.billing_config(number)
    if cfg:
        periods = sorted(periods, key=lambda p: p.get("periodNumber") or 0)
        print(f"\nverify     billing {cfg.get('billingFrequency')}"
              f"{' every ' + str(cfg.get('customFrequencyDays')) + ' days' if cfg.get('customFrequencyDays') else ''}"
              f", {len(periods)} period(s)")
        for p in periods[:3] + (periods[-1:] if len(periods) > 3 else []):
            print(f"           #{p.get('periodNumber')}  {fmt_date(p.get('periodStartDate'))}"
                  f" -> {fmt_date(p.get('periodEndDate'))}  {p.get('status')}")


def payment_step(hcm, roles, campaign, args):
    """Plan, and with --yes apply, the payment setup. Shared with
    setup_attendance.py."""
    require_roles(roles, PAYMENT_ROLES, "payment setup", args.yes)
    config_change, rates_change = plan_payment(
        hcm, campaign, args, (hcm.user or {}).get("uuid"))
    if not (config_change or rates_change):
        print("           nothing to do - payment setup already matches")
        return
    if not args.yes:
        print("\n  DRY RUN  pass --yes to save the payment setup")
        if config_change:
            print(f"  billing ({config_change[0]}): " + json.dumps(config_change[1])[:500])
        if rates_change:
            print(f"  rates ({rates_change[0]}):   " + json.dumps(rates_change[1])[:500])
        return
    apply_payment(hcm, campaign["campaignNumber"], config_change, rates_change)
