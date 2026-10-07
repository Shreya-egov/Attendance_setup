"""
Payment setup (HCM_BILLING / HCM_BILLING_DAYS / HCM_RATES): the billing cycle,
whose periods the expense calculator generates, and the per-role wages.
"""
import pytest

from tests.conftest import require_roles
from utils.client import fmt_date
from utils.payment import PAYMENT_ROLES, apply_payment, parse_rate, plan_payment

pytestmark = pytest.mark.payment

DAY_MS = 86400000


@pytest.mark.writes
def test_payment_setup(hcm, roles, campaign, payment_args):
    require_roles(roles, PAYMENT_ROLES, "payment setup")
    number = campaign["campaignNumber"]
    config_change, rates_change = plan_payment(hcm, campaign, payment_args,
                                               (hcm.user or {}).get("uuid"))
    apply_payment(hcm, number, config_change, rates_change)

    cfg, periods = hcm.billing_config(number)
    assert cfg, f"{number} has no billing config"
    assert cfg["billingFrequency"] == payment_args.billing.upper()
    if payment_args.billing.upper() == "CUSTOM":
        assert int(cfg["customFrequencyDays"]) == payment_args.days

    periods = sorted(periods, key=lambda p: p["periodStartDate"])
    assert periods, "the expense calculator generated no billing periods"
    assert fmt_date(periods[0]["periodStartDate"]) == fmt_date(campaign["startDate"])
    assert fmt_date(periods[-1]["periodEndDate"]) == fmt_date(campaign["endDate"])
    for before, after in zip(periods, periods[1:]):
        gap = after["periodStartDate"] - before["periodEndDate"]
        assert 0 < gap <= DAY_MS, (
            f"periods #{before.get('periodNumber')} and #{after.get('periodNumber')} "
            "overlap or leave a gap")

    record = hcm.worker_rates(campaign["projectId"])
    assert record, "no HCM.WORKER_RATES record for the campaign"
    expected = parse_rate(payment_args.rates, "HCM_RATES")
    for rate in record["data"]["rates"]:
        assert rate["rateBreakup"] == expected, (
            f"{rate['skillCode']} wages {rate['rateBreakup']}, expected {expected}")


def test_payment_unchanged_on_rerun(hcm, campaign, payment_args):
    changes = plan_payment(hcm, campaign, payment_args, (hcm.user or {}).get("uuid"))
    assert changes == (None, None), f"a re-run would change {changes}"
