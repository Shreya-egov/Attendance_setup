#!/usr/bin/env python3
"""
Map campaign users onto the attendance registers created by
create_registers.py.

Each user is bucketed by role into the register(s) for their boundary,
mirroring the three sheets of the xlsx route ("Frontline Workers" /
"Attendance Markers" / "Attendance Approvers"):

    approver roles -> staff, staffType APPROVER   PROXIMITY_SUPERVISOR, CAMPAIGN_SUPERVISOR
    marker roles   -> staff, staffType OWNER      TEAM_SUPERVISOR, DISTRICT_SUPERVISOR
    worker roles   -> attendee                    DISTRIBUTOR, REGISTRAR, FIELD_SUPPORT,
                                                  HEALTH_FACILITY_WORKER
    anything else  -> not enrolled (e.g. PAYMENT_*)

A user with several of these roles lands in one bucket, priority
APPROVER > MARKER > WORKER. Staff go on every in-scope register at their
boundary; a worker may be an attendee of only one register campaign-wide.

    ./map_staff.py CMP-2026-10-06-011022                          # plan (dry run)
    ./map_staff.py CMP-2026-10-06-011022 --yes                    # write staff
    ./map_staff.py CMP-2026-10-06-011022 --include-attendees --yes
    ./map_staff.py CMP-2026-10-06-011022 --users-xlsx ~/Downloads/CMP-2026-10-06-011022-Users.xlsx \\
        --register-id <uuid> --users USR-278731 --include-attendees --yes

Writes are DRY RUN unless --yes.

USERS come from the campaign's project staff by default. --users-xlsx reads
the workbench Users download instead; its "UserService Uuids" are resolved to
Individual ids, which is what attendance stores.

NOT IDEMPOTENT SERVER-SIDE. A second _create for the same (register, user,
staffType) either duplicates or 400s, so already-enrolled pairs are filtered
out client-side from the register search before anything is sent.

ENROLLMENT DATE defaults to the campaign start. The direct API route does not
run AttendanceRegisterAttendeeValidationProcessor - that processor only fires
on the xlsx route - so the out-of-range rejection is absent here and this
script enforces the campaign window itself. Override with --enrollment-date.

Works against demo and unified-qa - logic in utils/attendance.py.
"""
import argparse
import json

import utils.hcm as H
from utils.attendance import names, role_set, unique, warn_no_owner


def main():
    ap = argparse.ArgumentParser(
        description="Map campaign staff (and optionally attendees) onto "
                    "attendance registers.")
    H.add_campaign_arg(ap)
    ap.add_argument("--yes", action="store_true",
                    help="actually write; omit for a dry run")
    ap.add_argument("--include-attendees", action="store_true",
                    help="also enrol the worker bucket as attendees")
    ap.add_argument("--attendees-only", action="store_true",
                    help="skip staff; enrol only the worker bucket")
    ap.add_argument("--register-id", metavar="UUID",
                    help="restrict to a single register (default: all for the campaign)")
    ap.add_argument("--users-xlsx", metavar="PATH",
                    help="read users from the workbench <campaign>-Users.xlsx "
                         "instead of the campaign's project staff")
    ap.add_argument("--users", metavar="USR-1,USR-2",
                    help="only these usernames")
    ap.add_argument("--enrollment-date", metavar="DD-MM-YYYY",
                    help="default: campaign start date")
    ap.add_argument("--marker-roles", default=",".join(sorted(H.DEFAULT_MARKER_ROLES)))
    ap.add_argument("--approver-roles",
                    default=",".join(sorted(H.DEFAULT_APPROVER_ROLES)))
    ap.add_argument("--worker-roles", default=",".join(sorted(H.DEFAULT_WORKER_ROLES)))
    args = ap.parse_args()
    H.require_campaign(args)

    want_staff = not args.attendees_only
    want_attendees = args.include_attendees or args.attendees_only

    hcm, roles = H.connect()
    if want_staff:
        H.require_roles(roles, H.REGISTER_WRITE_ROLES, "staff", args.yes)
    if want_attendees:
        H.require_roles(roles, H.ATTENDEE_WRITE_ROLES, "attendees", args.yes)

    campaign = hcm.campaign(args.campaign_number)
    number = campaign["campaignNumber"]
    H.show_campaign(campaign)

    enrollment_ms = H.resolve_enrollment(campaign, args.enrollment_date)

    all_registers, search_body = hcm.registers(number, detailed=True)
    if not all_registers:
        H.die("no registers exist for this campaign yet.\n"
              f"         Run ./create_registers.py {number} --yes first.")

    registers = all_registers
    if args.register_id:
        registers = [r for r in all_registers if r.get("id") == args.register_id]
        if not registers:
            H.die(f"register {args.register_id} not found on campaign {number}")

    print(f"registers  {len(registers)} in scope"
          f"  (campaign total {search_body.get('totalCount')})")
    for r in registers:
        print(f"           {r.get('serviceCode')}  {r['id'][:8]}…  {r.get('localityCode')}"
              f"  attendees={len(r.get('attendees') or [])}"
              f"  staff={len(r.get('staff') or [])}")

    if args.users_xlsx:
        users = hcm.xlsx_users(args.users_xlsx)
        source = args.users_xlsx
    else:
        users = hcm.campaign_staff_users(number)
        source = "project staff"
    if args.users:
        wanted = {u.strip().upper() for u in args.users.split(",") if u.strip()}
        users = [u for u in users if (u.get("username") or "").upper() in wanted]
        missing = wanted - {(u.get("username") or "").upper() for u in users}
        if missing:
            H.die(f"--users not found in {source}: {sorted(missing)}")
    if not users:
        H.die(f"no users found ({source}).\n"
              "         Users have not been attached to the projects yet, so there\n"
              "         is nobody to map.")

    paths = hcm.boundary_paths(
        campaign["hierarchyType"],
        {u["boundaryCode"] for u in users} | {r.get("localityCode") for r in registers},
    )
    plan = H.plan_attendance(
        registers, users, enrollment_ms,
        role_set(args.marker_roles), role_set(args.approver_roles),
        role_set(args.worker_roles), all_registers=all_registers, paths=paths,
    )
    staff = plan["staff"] if want_staff else []
    attendees = plan["attendees"] if want_attendees else []

    owners = sum(1 for s in staff if s["staffType"] == "OWNER")
    print(f"\nusers      {len(users)} from {source}")
    print(f"           {len(plan['unmapped'])} in no role bucket"
          + (f" ({names(plan['unmapped'])})" if plan["unmapped"] else ""))
    print(f"           {len(plan['unplaced'])} with no register for their boundary"
          + (f" ({names(plan['unplaced'])})" if plan["unplaced"] else ""))
    skipped = unique(plan["skipped"])
    print(f"           {len(skipped)} already enrolled"
          + (f" ({names(skipped)})" if skipped else ""))
    print(f"plan       {len(staff)} staff ({owners} OWNER,"
          f" {len(staff) - owners} APPROVER)"
          f", {len(attendees)} attendee(s)")
    print(f"           enrollmentDate={H.fmt_date(enrollment_ms)}")
    if want_staff:
        warn_no_owner(plan)
    for s in staff:
        print(f"           staff     {s['staffType']:<8} {s['userId'][:8]}…"
              f"  {s['additionalDetails']['staffName']}  -> register {s['registerId'][:8]}…")
    for a in attendees:
        print(f"           attendee  {a['individualId'][:8]}…  -> register {a['registerId'][:8]}…")

    if want_attendees and plan["ambiguous"]:
        print(f"\n  HOLDING  {len(plan['ambiguous'])} worker(s) match several registers at"
              f" their boundary and can join only one:\n           {names(plan['ambiguous'])}"
              f"\n           pick one with --register-id (and --users to split them)")

    if not (staff or attendees):
        print("           nothing to do - everyone in scope is already enrolled")
        print()
        return

    if not args.yes:
        print("\n  DRY RUN  pass --yes to create these")
        if staff:
            print("  staff:    " + json.dumps(staff[0]))
        if attendees:
            print("  attendee: " + json.dumps(attendees[0]))
        print()
        return

    if staff:
        hcm.create_staff(staff)
        print(f"  CREATED  {len(staff)} staff")
    if attendees:
        hcm.create_attendees(attendees)
        print(f"  CREATED  {len(attendees)} attendee(s)")

    H.report_registers(hcm, number)
    print()


if __name__ == "__main__":
    try:
        main()
    except H.ApiError as e:
        H.die(str(e))
    except KeyboardInterrupt:
        raise SystemExit(130)
