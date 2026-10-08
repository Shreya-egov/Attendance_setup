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
from utils.attendance import resolve_level
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


def _ok(fn, *args):
    """Run a resolver; turn its stop message into a clean test failure."""
    try:
        return fn(*args)
    except SystemExit as e:
        pytest.fail(str(e).strip(), pytrace=False)


def test_hierarchy_exists(hcm):
    hierarchy = _ok(C.resolve_hierarchy, hcm, config.HIERARCHY_TYPE, config.BOUNDARIES_FROM)
    known = C.list_hierarchies(hcm)
    assert hierarchy in known, f"hierarchy {hierarchy} not in tenant {config.TENANT}: {known}"


def test_boundary_selection(hcm):
    """The campaign's boundaries resolve from whichever source .env sets -
    HCM_BOUNDARIES_FROM, HCM_BOUNDARY, or the default first path - and
    HCM_USER_LEVEL is one of the selected levels."""
    hierarchy = _ok(C.resolve_hierarchy, hcm, config.HIERARCHY_TYPE, config.BOUNDARIES_FROM)
    boundaries, _, _ = _ok(C.resolve_selection, hcm, hierarchy, config.BOUNDARY,
                           config.BOUNDARIES_FROM)
    assert boundaries, "the boundary selection is empty"
    _ok(C.user_level_boundary, boundaries, config.USER_LEVEL)


def test_register_level_valid(hcm):
    """HCM_REGISTER_LEVEL is auto, leaf, a 1-based position or a level name."""
    level = str(config.DEFAULT_REGISTER_LEVEL).lower()
    if level in ("auto", "leaf"):
        return
    hierarchy = _ok(C.resolve_hierarchy, hcm, config.HIERARCHY_TYPE, config.BOUNDARIES_FROM)
    _ok(resolve_level, config.DEFAULT_REGISTER_LEVEL, hcm.hierarchy_levels(hierarchy))


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
