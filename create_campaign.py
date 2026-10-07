#!/usr/bin/env python3
"""
Create a campaign the way the admin console does, over the API - only the
campaign; e2e.py also sets up its attendance and payment.

    ./create_campaign.py --type BEDNET                 # plan only (dry run)
    ./create_campaign.py --type MR-DN --yes            # create, wait for 'created'
    ./create_campaign.py --type BEDNET --boundaries-from CMP-2026-10-06-011022 --yes

Defaults come from .env (HCM_HIERARCHY_TYPE, HCM_BOUNDARIES_FROM,
HCM_USER_ROLES, HCM_USER_PHONE_START). Logic in utils/campaign.py, ported
from api_automation_project's console branch.

Needs CAMPAIGN_MANAGER. Writes are DRY RUN unless --yes.
"""
import argparse

import utils.campaign as C
import utils.hcm as H


def main():
    ap = argparse.ArgumentParser(description="Create a campaign as the admin console does.")
    H.add_create_args(ap)
    ap.add_argument("--yes", action="store_true", help="actually create; omit for a dry run")
    args = ap.parse_args()

    hcm, roles = H.connect()
    H.require_roles(roles, C.CAMPAIGN_WRITE_ROLES, "campaign creation", args.yes)
    plan = C.plan_campaign(hcm, args)
    if not args.yes:
        print("\n  DRY RUN  pass --yes to create the campaign\n")
        return
    campaign = C.create_campaign(hcm, plan)
    print(f"\n  CREATED  {campaign['campaignNumber']}  projectId={campaign.get('projectId')}\n")


if __name__ == "__main__":
    try:
        main()
    except H.ApiError as e:
        H.die(str(e))
    except KeyboardInterrupt:
        raise SystemExit(130)
