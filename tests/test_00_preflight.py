"""
Read-only checks that the environment can run the flow at all - the account,
MDMS masters, samples and payment config - so a misconfiguration fails here
in seconds rather than halfway through creating a campaign.

    pytest -m preflight
"""
import os

import pytest

from tests.conftest import require_roles
from utils import campaign as C
from utils import config
from utils.payment import parse_rate
from utils.phone_book import parse_roles
from utils.template_filler import sample_template_path

pytestmark = pytest.mark.preflight


def test_account_can_create_and_map(roles):
    require_roles(roles, C.CAMPAIGN_WRITE_ROLES, "campaign creation and payment setup")
    require_roles(roles, config.REGISTER_WRITE_ROLES, "registers and staff")


def test_project_type_in_mdms(hcm, campaign_type):
    ptype = C.project_type(hcm, campaign_type)
    assert ptype["code"].upper() == campaign_type
    assert ptype.get("cycles"), f"{ptype['code']} has no delivery cycles in MDMS"


def test_sample_template_present(campaign_type):
    path = sample_template_path(campaign_type)
    assert path and os.path.exists(path), (
        f"no sample for {campaign_type}: add data/templates/{campaign_type}_sample.xlsx")


def test_boundary_source(hcm):
    if not config.BOUNDARIES_FROM:
        pytest.skip("HCM_BOUNDARIES_FROM not set - the first root-to-leaf path is used")
    source = hcm.campaign(config.BOUNDARIES_FROM)
    assert source.get("boundaries"), f"{config.BOUNDARIES_FROM} has no boundaries"
    if config.HIERARCHY_TYPE:
        assert source.get("hierarchyType") == config.HIERARCHY_TYPE


def test_user_level_selected(hcm):
    """HCM_USER_LEVEL is a type the campaign's boundaries include."""
    if not config.BOUNDARIES_FROM:
        pytest.skip("HCM_BOUNDARIES_FROM not set - checked when the campaign is planned")
    types = [(b.get("type") or "").upper()
             for b in hcm.campaign(config.BOUNDARIES_FROM).get("boundaries") or []]
    assert config.USER_LEVEL in types, (
        f"HCM_USER_LEVEL={config.USER_LEVEL} is not among the selected types {types}")


def test_users_cover_attendance_roles():
    roles = set(parse_roles(config.USER_ROLES))
    missing = [label for label, codes in C.ROLE_BUCKETS if not roles & codes]
    assert not missing, f"HCM_USER_ROLES has no user for: {missing}"


def test_billing_cycle_configured(hcm):
    if not config.BILLING:
        pytest.skip("HCM_BILLING not set")
    cycles = hcm.billing_cycles()
    code = config.BILLING.upper()
    assert code in cycles, f"{code} not in MDMS BillingCycle: {sorted(cycles)}"
    if code == "CUSTOM":
        lo = cycles[code].get("minDuration", 1)
        hi = cycles[code].get("maxDuration", 10 ** 6)
        assert config.BILLING_DAYS is not None, "CUSTOM billing needs HCM_BILLING_DAYS"
        assert lo <= config.BILLING_DAYS <= hi


def test_wage_roles_and_limits(hcm, campaign_type):
    if not config.RATES:
        pytest.skip("HCM_RATES not set")
    ptype = C.project_type(hcm, campaign_type)
    skills = hcm.campaign_type_skills(ptype["code"])
    assert skills and skills.get("skills"), f"no CampaignTypeSkills for {ptype['code']}"
    rates = parse_rate(config.RATES, "HCM_RATES")
    for key, limit in (skills.get("rateMaxLimitSchema") or {}).items():
        assert rates.get(key, 0) <= limit, f"{key}={rates.get(key)} exceeds the limit {limit}"
