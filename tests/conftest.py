"""
Fixtures for the attendance + payment suite.

Every test that takes ``campaign_type`` runs once per HCM_CAMPAIGN_TYPES
(BEDNET, MR-DN). The stages hand the campaign on through output/campaigns.json:
test_10 creates it, test_20/30/40 load it - so a later stage can be re-run on
its own against the campaign an earlier run created.
"""
from types import SimpleNamespace

import pytest

from utils import config
from utils.client import Hcm
from utils.store import load_campaign_result


@pytest.fixture(scope="session")
def hcm():
    """One logged-in session for the whole run (token or password grant)."""
    if not (config.env("HCM_AUTH_TOKEN")
            or (config.env("HCM_USERNAME") and config.env("HCM_PASSWORD"))):
        pytest.skip("no credentials: set HCM_USERNAME/HCM_PASSWORD (or HCM_AUTH_TOKEN) in .env")
    client = Hcm()
    client.login()
    return client


@pytest.fixture(scope="session")
def roles(hcm):
    """Role codes of the account, or None when they cannot be read."""
    return hcm.roles()


def require_roles(roles, needed, what):
    if roles is not None and not roles & needed:
        pytest.skip(f"{what} needs one of {sorted(needed)}; the account has {sorted(roles)}")


@pytest.fixture(params=config.CAMPAIGN_TYPES)
def campaign_type(request):
    """One campaign type, e.g. BEDNET or MR-DN."""
    return request.param


@pytest.fixture
def stored(campaign_type):
    """What test_10 recorded for this campaign type."""
    record = load_campaign_result(campaign_type)
    if not record:
        pytest.skip(f"no {campaign_type} campaign in output/campaigns.json - "
                    "run tests/test_10_campaign.py first")
    return record


@pytest.fixture
def campaign(hcm, stored):
    """The stored campaign as the server holds it now."""
    return hcm.campaign(stored["campaignNumber"])


@pytest.fixture
def create_args(campaign_type):
    """The options create_campaign.py would take, from .env."""
    return SimpleNamespace(
        project_type=campaign_type, name=None, boundary=config.BOUNDARY,
        boundaries_from=config.BOUNDARIES_FROM, hierarchy=config.HIERARCHY_TYPE,
        sample=None, start=None, end=None, locale=config.LOCALE,
        timeout=config.CREATE_TIMEOUT, users=config.USER_ROLES,
        user_level=config.USER_LEVEL,
    )


@pytest.fixture
def payment_args():
    """The options setup_payment.py would take, from .env."""
    if not config.BILLING:
        pytest.skip("HCM_BILLING is not set - payment setup not configured")
    return SimpleNamespace(billing=config.BILLING, days=config.BILLING_DAYS,
                           rates=config.RATES, rate=[], yes=True)
