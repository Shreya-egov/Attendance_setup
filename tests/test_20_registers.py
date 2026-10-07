"""
Attendance registers: one per boundary where the campaign's DISTRIBUTOR users
are mapped (HCM_REGISTER_LEVEL, default auto), created directly through
health-attendance as setup_attendance.py does.
"""
import pytest

from tests.conftest import require_roles
from utils import config
from utils.attendance import plan_registers, select_boundaries

pytestmark = pytest.mark.attendance


def _plan(hcm, campaign):
    number = campaign["campaignNumber"]
    users = hcm.campaign_staff_users(number)
    assert users, f"{number} has no project staff"
    projects = hcm.projects(number)
    assert projects, f"{number} has no projects"
    wanted = select_boundaries(hcm, campaign, projects, config.DEFAULT_REGISTER_LEVEL,
                               users=users)
    existing, _ = hcm.registers(number, detailed=True)
    planned = plan_registers(campaign, wanted, existing, "REG",
                             config.DEFAULT_EVENT_TYPE, config.DEFAULT_SESSIONS)
    return wanted, planned


@pytest.mark.writes
def test_create_registers(hcm, roles, campaign):
    require_roles(roles, config.REGISTER_WRITE_ROLES, "registers")
    wanted, planned = _plan(hcm, campaign)
    if planned:
        hcm.create_registers(planned)

    registers, _ = hcm.registers(campaign["campaignNumber"])
    localities = [r.get("localityCode") for r in registers]
    missing = set(wanted) - set(localities)
    assert not missing, f"no register at {sorted(missing)}"
    doubled = sorted({c for c in localities if localities.count(c) > 1})
    assert not doubled, f"more than one register at {doubled}"


def test_registers_not_duplicated_on_rerun(hcm, campaign):
    _, planned = _plan(hcm, campaign)
    assert planned == [], f"a re-run would create {len(planned)} more register(s)"
