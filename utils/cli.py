"""
Helpers for the command-line scripts in the project root: the campaign
argument, login banner and the up-front role check.
"""
import os
import sys

from utils.client import Hcm, die, fmt_date
from utils.config import (BASE, BILLING, BILLING_DAYS, BOUNDARIES_FROM, BOUNDARY, CAMPAIGN_TYPES,
                          CREATE_TIMEOUT, HIERARCHY_TYPE, RATES, TENANT, USER_LEVEL,
                          USER_ROLES)



def add_campaign_arg(ap):
    """Campaign number as an optional positional, defaulting to
    HCM_CAMPAIGN_NUMBER from .env / the environment."""
    ap.add_argument("campaign_number", nargs="?",
                    default=os.environ.get("HCM_CAMPAIGN_NUMBER") or None,
                    help="campaign number, e.g. CMP-2026-10-06-011023 "
                         "(default: HCM_CAMPAIGN_NUMBER in .env)")


def require_campaign(args, argv=None):
    """Die without a campaign number; say where it came from, so a stale
    .env value is visible before anything runs."""
    if not args.campaign_number:
        die("no campaign number: pass it as the first argument or set "
            "HCM_CAMPAIGN_NUMBER in .env")
    argv = sys.argv[1:] if argv is None else argv
    source = "command line" if args.campaign_number in argv else "HCM_CAMPAIGN_NUMBER in .env"
    print(f"\nusing      {args.campaign_number}  (from {source})")


def connect():
    """Log in and print the standard env/user banner. Returns (hcm, roles)."""
    hcm = Hcm()
    user = hcm.login()
    roles = hcm.roles()
    print(f"\nenv        {BASE}  (tenant {TENANT})")
    shown = "unknown (no /user/_details)" if roles is None else (sorted(roles) or "[]")
    print(f"logged in  {user.get('userName') or '(token)'}  roles={shown}")
    return hcm, roles


def require_roles(roles, needed, what, writing):
    """
    Refuse a write the gateway would reject anyway, and name the missing role
    instead of failing mid-run. Dry runs are allowed through with a warning.

    roles=None means we could not read them; let the call through rather than
    blocking on our own missing information - the gateway is the real gate.
    """
    if roles is None:
        return
    if roles & needed:
        return
    msg = f"{what} need one of {sorted(needed)}"
    if writing:
        die("this token cannot write:\n         " + msg
            + "\n\n         CAMPAIGN_MANAGER is deliberately not on those lists."
              "\n         Re-run with a supervisor/superuser account.")
    print(f"\n  WARNING  token lacks the write roles; dry run only:\n           {msg}")


def show_campaign(campaign):
    print(f"\ncampaign   {campaign['campaignNumber']}  {campaign.get('campaignName')}")
    print(f"           id={campaign.get('id')}  type={campaign.get('projectType')}"
          f"  hierarchy={campaign.get('hierarchyType')}  status={campaign.get('status')}")
    print(f"           window {fmt_date(campaign.get('startDate'))}"
          f" -> {fmt_date(campaign.get('endDate'))}")


def add_payment_args(ap):
    g = ap.add_argument_group("payment setup (defaults from .env: HCM_BILLING, "
                              "HCM_BILLING_DAYS, HCM_RATES)")
    g.add_argument("--billing", metavar="FREQ", default=BILLING,
                   help="billing cycle code, e.g. CUSTOM, WEEKLY")
    g.add_argument("--days", type=int,
                   default=BILLING_DAYS,
                   help="days per bill, for --billing CUSTOM")
    g.add_argument("--rates", metavar="F,T,P", default=RATES,
                   help="food,travel,per-day wage for every role")
    g.add_argument("--rate", action="append", default=[], metavar="SKILL=F,T,P",
                   help="override one role; repeatable")


def add_create_args(ap):
    g = ap.add_argument_group("campaign creation (defaults from .env: HCM_CAMPAIGN_TYPES, "
                              "HCM_HIERARCHY_TYPE, HCM_BOUNDARIES_FROM, HCM_USER_ROLES, "
                              "HCM_USER_PHONE_START, HCM_USER_LEVEL)")
    g.add_argument("--type", dest="project_type", type=str.upper, choices=CAMPAIGN_TYPES,
                   help=f"campaign type, one of {', '.join(CAMPAIGN_TYPES)}")
    g.add_argument("--users", default=USER_ROLES,
                   metavar="ROLE,ROLE,...",
                   help="one user is created per role (default HCM_USER_ROLES)")
    g.add_argument("--user-level", default=USER_LEVEL, metavar="TYPE",
                   help="boundary type the users are created at "
                        f"(default HCM_USER_LEVEL, {USER_LEVEL})")
    g.add_argument("--name", help="campaign name (default <TYPE>_e2e_<random>)")
    g.add_argument("--boundary", default=BOUNDARY, metavar="CODE",
                   help="select root -> CODE with all its children "
                        "(default: the first root-to-leaf path)")
    g.add_argument("--boundaries-from", default=BOUNDARIES_FROM,
                   metavar="CMP-...",
                   help="copy the boundary selection of an existing campaign "
                        "(instead of --boundary)")
    g.add_argument("--hierarchy", default=HIERARCHY_TYPE,
                   help="boundary hierarchy (default: the --boundaries-from campaign's)")
    g.add_argument("--sample", metavar="XLSX",
                   help="filled template to copy from (default templates/<TYPE>_sample.xlsx)")
    g.add_argument("--start", metavar="DD-MM-YYYY", help="start date (default tomorrow)")
    g.add_argument("--end", metavar="DD-MM-YYYY", help="end date (default start + 31 days)")
    g.add_argument("--locale", help="default HCM_LOCALE, else the tenant's default language")
    g.add_argument("--timeout", type=int, default=CREATE_TIMEOUT,
                   help="seconds to wait on each async step (default 900)")
