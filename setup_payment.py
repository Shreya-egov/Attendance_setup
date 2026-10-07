#!/usr/bin/env python3
"""
Payment setup for a campaign - what payments-ui "Setup Payment Attributes"
submits: a billing cycle (health-expense-calculator billing-config) and the
per-role wages (MDMS HCM.WORKER_RATES).

    ./setup_payment.py CMP-2026-10-06-011022 --billing CUSTOM --days 3 --rates 50,50,150
    ./setup_payment.py                      # campaign + payment values from .env
    ./setup_payment.py CMP-2026-10-06-011022 --billing WEEKLY --rates 50,50,150 \\
        --rate DISTRIBUTOR=40,40,120 --yes

Writes are DRY RUN unless --yes.

--billing  a code from MDMS HCM-BILLING-CONFIG-PAYMENT-SETUP.BillingCycle
           (unified-qa: WEEKLY, BI_WEEKLY, MONTHLY, CUSTOM). CUSTOM needs --days,
           within the record's minDuration/maxDuration.
--rates    FOOD,TRAVEL,PER_DAY for every wage role of the campaign type (MDMS
           CampaignTypeSkills); --rate SKILL=F,T,P overrides one role. Capped
           by the type's rateMaxLimitSchema, as the UI caps them.

Re-running is safe: an existing billing config / rate record is updated, or
left alone when it already matches. The expense calculator generates the
billing periods itself when the config is saved.

Needs CAMPAIGN_MANAGER (billing-config and WORKER_RATES writes on unified-qa).
Logic in utils/payment.py.
"""
import argparse

import utils.hcm as H
from utils.cli import add_payment_args
from utils.payment import payment_step

def main():
    ap = argparse.ArgumentParser(description="Billing cycle + role wages for a campaign.")
    H.add_campaign_arg(ap)
    ap.add_argument("--yes", action="store_true", help="actually write; omit for a dry run")
    add_payment_args(ap)
    args = ap.parse_args()
    H.require_campaign(args)
    if not args.billing:
        ap.error("--billing is required (or set HCM_BILLING in .env)")

    hcm, roles = H.connect()
    campaign = hcm.campaign(args.campaign_number)
    H.show_campaign(campaign)
    print()
    payment_step(hcm, roles, campaign, args)
    print()


if __name__ == "__main__":
    try:
        main()
    except H.ApiError as e:
        H.die(str(e))
    except KeyboardInterrupt:
        raise SystemExit(130)
