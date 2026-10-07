"""
Campaign creation as the admin console does it (utils/campaign.py): draft ->
boundaries -> delivery rules -> template generate/fill/validate -> create.
Records the campaign in output/campaigns.json for the later stages.

    pytest tests/test_10_campaign.py -k BEDNET
"""
import pytest

from tests.conftest import require_roles
from utils import campaign as C
from utils.store import save_campaign_result


@pytest.mark.create
@pytest.mark.writes
def test_create_campaign(hcm, roles, campaign_type, create_args):
    require_roles(roles, C.CAMPAIGN_WRITE_ROLES, "campaign creation")
    plan = C.plan_campaign(hcm, create_args)
    campaign = C.create_campaign(hcm, plan)
    number = campaign["campaignNumber"]
    save_campaign_result(campaign_type, {
        "campaignType": campaign_type, "campaignNumber": number,
        "campaignId": campaign.get("id"), "campaignName": campaign.get("campaignName"),
        "projectType": campaign.get("projectType"), "projectId": campaign.get("projectId"),
        "boundaries": plan["boundaries"], "users": plan["users"], "files": plan["files"],
    })

    assert campaign["status"] == "created"
    assert campaign.get("projectId"), "created campaign has no root project"
    resources = campaign.get("resources") or []
    assert any(r.get("type") == C.RESOURCE_TYPE
               and r.get("filestoreId") == plan["files"]["uploaded"] for r in resources), (
        f"uploaded template {plan['files']['uploaded']} not among resources {resources}")

    projects = C.poll(f"projects of {number}",
                      lambda attempt: hcm.projects(number) or None, timeout=120)
    missing = {b["code"] for b in plan["boundaries"]} - set(projects)
    assert not missing, f"no project for selected boundaries {sorted(missing)}"


def test_campaign_users(hcm, stored):
    """Every user written into the template exists as project staff, with
    its role, at the boundary it was given."""
    staff = {u["name"]: u for u in hcm.campaign_staff_users(stored["campaignNumber"])}
    for issued in stored["users"]:
        user = staff.get(issued["name"])
        assert user, f"{issued['name']} ({issued['phone']}) is not project staff"
        assert issued["role"] in user["roles"], (
            f"{issued['name']} has roles {sorted(user['roles'])}, expected {issued['role']}")
        if issued.get("boundary"):
            assert user["boundaryCode"] == issued["boundary"]
    phones = [u["phone"] for u in stored["users"]]
    assert len(phones) == len(set(phones)), "two users share a phone number"
