#!/usr/bin/env python3
"""
Attendance setup for a campaign in one go: create registers at a hierarchy
level, then map users onto them. The two steps of create_registers.py and
map_staff.py, planned together.

    ./setup_attendance.py                                   # campaign (+ payment) from .env
    ./setup_attendance.py CMP-2026-10-06-011022             # plan both steps (dry run)
    ./setup_attendance.py CMP-2026-10-06-011022 --yes       # create + map
    ./setup_attendance.py CMP-2026-10-06-011022 --level 5   # LOCALITY, not auto
    ./setup_attendance.py CMP-2026-10-06-011022 --billing CUSTOM --days 3 \\
        --rates 50,50,150 --yes                              # + payment setup

Writes are DRY RUN unless --yes. The dry run previews the mapping against the
registers it would create, using placeholder ids.

  registers  by default one per boundary the DISTRIBUTOR users are mapped
             to (--level auto); or every boundary at --level N/TYPE. Only
             boundaries with a project and no register yet
  users      the campaign's project staff, or --users-xlsx; matched through
             the boundary tree - workers/markers to the register at their
             boundary or nearest ancestor, approvers to every register at or
             below theirs (see map_staff.py for the role buckets)

  payment    with --billing: billing cycle + role wages, as setup_payment.py

Needs DISTRICT_SUPERVISOR (or NATIONAL/PROVINCIAL_SUPERVISOR, SUPERUSER,
SYSTEM_ADMINISTRATOR) - CAMPAIGN_MANAGER alone gets 401 on the attendance APIs -
and, for --billing, CAMPAIGN_MANAGER as well.
Credentials: .env next to this script (HCM_BASE_URL, HCM_TENANT_ID and either
HCM_AUTH_TOKEN or HCM_USERNAME/HCM_PASSWORD/HCM_BASIC_AUTH).
"""
import argparse
import json

import utils.hcm as H
from utils.attendance import names, role_set, unique, warn_no_owner
from utils.cli import add_payment_args
from utils.payment import payment_step


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Create attendance registers and map users, for one campaign.")
    H.add_campaign_arg(ap)
    ap.add_argument("--yes", action="store_true", help="actually write; omit for a dry run")
    ap.add_argument("--level", metavar="auto|N|TYPE|leaf", default=H.DEFAULT_REGISTER_LEVEL,
                    help="register level: 'auto' = where the DISTRIBUTOR users are mapped, "
                         f"or a 1-based position / type / 'leaf' (default {H.DEFAULT_REGISTER_LEVEL})")
    ap.add_argument("--users-xlsx", metavar="PATH",
                    help="users from the workbench <campaign>-Users.xlsx instead of project staff")
    ap.add_argument("--staff-only", action="store_true", help="do not enrol attendees")
    ap.add_argument("--register-prefix", default="REG")
    ap.add_argument("--event-type", default=H.DEFAULT_EVENT_TYPE)
    ap.add_argument("--sessions", type=int, default=H.DEFAULT_SESSIONS, choices=(1, 2))
    ap.add_argument("--enrollment-date", metavar="DD-MM-YYYY")
    ap.add_argument("--marker-roles", default=",".join(sorted(H.DEFAULT_MARKER_ROLES)))
    ap.add_argument("--approver-roles", default=",".join(sorted(H.DEFAULT_APPROVER_ROLES)))
    ap.add_argument("--worker-roles", default=",".join(sorted(H.DEFAULT_WORKER_ROLES)))
    add_payment_args(ap)
    ap.add_argument("--skip-payment", action="store_true",
                    help="skip payment setup even if HCM_BILLING is set in .env")
    args = ap.parse_args(argv)
    H.require_campaign(args, argv)

    hcm, roles = H.connect()
    H.require_roles(roles, H.REGISTER_WRITE_ROLES, "registers and staff", args.yes)

    campaign = hcm.campaign(args.campaign_number)
    number = campaign["campaignNumber"]
    H.show_campaign(campaign)
    enrollment_ms = H.resolve_enrollment(campaign, args.enrollment_date)

    # users first: --level auto takes the register level from them
    if args.users_xlsx:
        users, source = hcm.xlsx_users(args.users_xlsx), args.users_xlsx
    else:
        users, source = hcm.campaign_staff_users(number), "project staff"
    if not users:
        H.die(f"no users found ({source}) - nobody to map.")

    # ---- step 1: registers ---------------------------------------------
    print("\n== registers")
    bp_map = hcm.projects(number)
    if not bp_map:
        H.die("no projects for this campaign - it has not finished creating.")
    bp_map = H.select_boundaries(hcm, campaign, bp_map, args.level, users=users)
    existing, _ = hcm.registers(number, detailed=True)
    planned = H.plan_registers(campaign, bp_map, existing, args.register_prefix,
                               args.event_type, args.sessions)
    print(f"           {len(bp_map)} boundary/ies at that level, {len(existing)} register(s)"
          f" already on the campaign, {len(planned)} to create")
    for p in planned:
        print(f"           + {p['serviceCode']}  {p['localityCode']}")

    # ---- step 2: users (planned against the registers of step 1) --------

    def plan_mapping(registers):
        paths = hcm.boundary_paths(
            campaign["hierarchyType"],
            {u["boundaryCode"] for u in users} | {r.get("localityCode") for r in registers})
        return H.plan_attendance(
            registers, users, enrollment_ms,
            role_set(args.marker_roles), role_set(args.approver_roles),
            role_set(args.worker_roles), all_registers=registers, paths=paths)

    def show(plan):
        staff = plan["staff"]
        attendees = [] if args.staff_only else plan["attendees"]
        owners = sum(1 for s in staff if s["staffType"] == "OWNER")
        print(f"           {len(users)} from {source}")
        for key, label in (("unmapped", "in no role bucket"),
                           ("unplaced", "with no register at/above their boundary"),
                           ("skipped", "already enrolled"),
                           ("ambiguous", "workers matching several registers (held)")):
            group = unique(plan[key])
            if group:
                print(f"           {len(group)} {label}: {names(group)}")
        print(f"           plan {len(staff)} staff ({owners} OWNER,"
              f" {len(staff) - owners} APPROVER), {len(attendees)} attendee(s)")
        warn_no_owner(plan)
        return staff, attendees

    if not args.yes:
        print("\n== users")
        preview = existing + [dict(p, id=f"planned:{p['serviceCode']}", attendees=[], staff=[])
                              for p in planned]
        staff, attendees = show(plan_mapping(preview))
        print("\n  DRY RUN  pass --yes to create the registers and map the users")
        if planned:
            print("  register: " + json.dumps(planned[0])[:400])
        if staff:
            print("  staff:    " + json.dumps(staff[0])[:400])
        if attendees:
            print("  attendee: " + json.dumps(attendees[0])[:400])
        payment(hcm, roles, campaign, args)
        print()
        return

    if planned:
        created = hcm.create_registers(planned)
        print(f"  CREATED  {len(created)} register(s)")
    else:
        print("           nothing to create")
    # step 2 only after step 1: users map onto the registers just created
    print("\n== users")
    registers, _ = hcm.registers(number, detailed=True)
    staff, attendees = show(plan_mapping(registers))
    if staff:
        hcm.create_staff(staff)
        print(f"  CREATED  {len(staff)} staff")
    if attendees:
        hcm.create_attendees(attendees)
        print(f"  CREATED  {len(attendees)} attendee(s)")
    H.report_registers(hcm, number)
    payment(hcm, roles, campaign, args)
    print()


def payment(hcm, roles, campaign, args):
    """Step 3, only when --billing is given (or HCM_BILLING is in .env)."""
    if not args.billing or args.skip_payment:
        return
    print("\n== payment setup")
    payment_step(hcm, roles, campaign, args)


if __name__ == "__main__":
    try:
        main()
    except H.ApiError as e:
        H.die(str(e))
    except KeyboardInterrupt:
        raise SystemExit(130)
