"""
User mapping onto the registers: workers -> attendees, markers -> OWNER,
approvers -> APPROVER, everyone else (payment roles) not enrolled - the
buckets of the xlsx route, through the direct attendance APIs.
"""
import pytest

from tests.conftest import require_roles
from utils import config
from utils.attendance import classify_user, plan_attendance, resolve_enrollment

pytestmark = pytest.mark.attendance

BUCKETS = (config.DEFAULT_MARKER_ROLES, config.DEFAULT_APPROVER_ROLES,
           config.DEFAULT_WORKER_ROLES)


def _plan(hcm, campaign):
    number = campaign["campaignNumber"]
    registers, _ = hcm.registers(number, detailed=True)
    assert registers, f"{number} has no registers - run test_20 first"
    users = hcm.campaign_staff_users(number)
    paths = hcm.boundary_paths(
        campaign["hierarchyType"],
        {u["boundaryCode"] for u in users} | {r.get("localityCode") for r in registers})
    plan = plan_attendance(registers, users, resolve_enrollment(campaign, None), *BUCKETS,
                           all_registers=registers, paths=paths)
    return users, plan


@pytest.mark.writes
def test_map_users(hcm, roles, campaign):
    require_roles(roles, config.REGISTER_WRITE_ROLES, "staff")
    require_roles(roles, config.ATTENDEE_WRITE_ROLES, "attendees")
    users, plan = _plan(hcm, campaign)
    assert not plan["unplaced"], f"no register for {[u['name'] for u in plan['unplaced']]}"
    assert not plan["ambiguous"], f"workers on several registers: {plan['ambiguous']}"
    if plan["staff"]:
        hcm.create_staff(plan["staff"])
    if plan["attendees"]:
        hcm.create_attendees(plan["attendees"])

    registers, _ = hcm.registers(campaign["campaignNumber"], detailed=True)
    attendees = [a["individualId"] for r in registers for a in r.get("attendees") or []
                 if not a.get("denrollmentDate")]
    staff = {(s["userId"], s["staffType"]) for r in registers for s in r.get("staff") or []}
    for r in registers:
        assert any(s.get("staffType") == "OWNER" for s in r.get("staff") or []), (
            f"register {r.get('registerNumber')} has no OWNER - nobody can mark attendance")
    for u in users:
        kind = classify_user(u["roles"], *BUCKETS)
        if kind == "ATTENDEE":
            assert attendees.count(u["userId"]) == 1, (
                f"worker {u['name']} is an attendee of {attendees.count(u['userId'])} registers")
        elif kind in ("OWNER", "APPROVER"):
            assert (u["userId"], kind) in staff, f"{u['name']} is not {kind} staff"
        else:
            assert u["userId"] not in attendees and not any(
                uid == u["userId"] for uid, _ in staff), (
                f"{u['name']} ({sorted(u['roles'])}) has no attendance role but is enrolled")


def test_mapping_not_duplicated_on_rerun(hcm, campaign):
    _, plan = _plan(hcm, campaign)
    assert not plan["staff"] and not plan["attendees"], (
        f"a re-run would add {len(plan['staff'])} staff, {len(plan['attendees'])} attendee(s)")
