#!/usr/bin/env python3
"""
End to end, no campaign number needed: create a campaign the way the admin
console does, then set up its attendance and payment.

    ./e2e.py                                   # plan BEDNET and MR-DN (dry run), all from .env
    ./e2e.py --yes                             # one campaign per type, each set up fully
    ./e2e.py --type MR-DN --yes                # just one type

Without --type it runs once per HCM_CAMPAIGN_TYPES (BEDNET, MR-DN), like the
console suite.

  1. campaign   create_campaign.py: draft -> boundaries -> delivery rules ->
                template generate/fill/upload/validate -> create -> 'created'
  2. attendance setup_attendance.py on the new campaign: registers, then users
  3. payment    with --billing (or HCM_BILLING in .env), as setup_payment.py

Options not listed under "campaign creation" go to setup_attendance.py
unchanged (--level, --users-xlsx, --staff-only, --billing, --rates, ...).
Writes are DRY RUN unless --yes. A dry run cannot plan steps 2-3 - there is no
campaign yet - so it stops after planning step 1.

Needs CAMPAIGN_MANAGER (campaign, payment) plus DISTRICT_SUPERVISOR or another
attendance write role (registers, users) on the one account in .env.
"""
import argparse
import json
import os
import re
import sys

import setup_attendance
import utils.campaign as C
import utils.hcm as H


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(
        description="Create a campaign, then its registers, user mapping and payment setup.",
        epilog="Any other option is passed to setup_attendance.py; see its --help.",
        allow_abbrev=False)
    H.add_create_args(ap)
    ap.add_argument("--yes", action="store_true", help="actually write; omit for a dry run")
    args, rest = ap.parse_known_args(argv)
    stray = [a for a in rest if re.match(r"^CMP-\d{4}-", a)]
    if stray:
        H.die(f"e2e.py creates its own campaign; drop {stray[0]} "
              "(or run setup_attendance.py on an existing one)")

    hcm, roles = H.connect()
    H.require_roles(roles, C.CAMPAIGN_WRITE_ROLES, "campaign creation", args.yes)
    H.require_roles(roles, H.REGISTER_WRITE_ROLES, "registers and staff", args.yes)

    types = [args.project_type] if args.project_type else C.CAMPAIGN_TYPES
    done = []
    for ctype in types:
        print(f"\n{'#' * 68}\n  {ctype}\n{'#' * 68}")
        args.project_type = ctype
        number = run_one(hcm, args, rest)
        if number:
            done.append(f"{ctype}: {number}")
    if done:
        print("\n  ALL DONE  " + "   ".join(done) + "\n")


def run_one(hcm, args, rest):
    """Create one campaign of args.project_type and set it up.
    Returns its number, or None on a dry run."""
    plan = C.plan_campaign(hcm, args)

    if not args.yes:
        print("\n== attendance + payment")
        print("           run by setup_attendance.py on the new campaign"
              + (f", with: {' '.join(rest)}" if rest else ""))
        print("\n  DRY RUN  pass --yes to create the campaign and continue\n")
        return None

    campaign = C.create_campaign(hcm, plan)
    number = campaign["campaignNumber"]
    record = os.path.join(C.CAMPAIGN_OUTPUT_DIR, f"{number}.json")
    with open(record, "w") as fh:
        json.dump({"campaignNumber": number, "campaignId": campaign.get("id"),
                   "campaignName": campaign.get("campaignName"),
                   "projectType": campaign.get("projectType"),
                   "projectId": campaign.get("projectId"),
                   "boundaries": plan["boundaries"]}, fh, indent=2)
    print(f"\n  CREATED  {number}  projectId={campaign.get('projectId')}  ({record})")

    # 'created' is set once project-factory has written everything, but the
    # project search can lag behind it by a few seconds.
    C.poll(f"projects of {number}",
           lambda attempt: hcm.projects(number) or None, timeout=120)

    print(f"\n{'=' * 68}\n  attendance + payment for {number}\n{'=' * 68}")
    setup_attendance.main([number, "--yes", *rest])
    print(f"\n  DONE  {number}  - open it in the workbench to check\n")
    return number


if __name__ == "__main__":
    try:
        main()
    except H.ApiError as e:
        H.die(str(e))
    except KeyboardInterrupt:
        raise SystemExit(130)
