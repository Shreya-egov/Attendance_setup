#!/usr/bin/env python3
"""
Generate attendance registers for a campaign - one per campaign boundary that
has a project and does not have a register yet.

    ./create_registers.py CMP-2026-09-28-005397            # inspect + plan (dry run)
    ./create_registers.py CMP-2026-09-28-005397 --yes      # actually create
    ./create_registers.py CMP-2026-09-28-005397 --level 4  # 4th level instead

Writes are DRY RUN unless --yes.

--level picks the hierarchy level registers are created at. Default "auto"
(or HCM_REGISTER_LEVEL): the boundaries the campaign's DISTRIBUTOR users are
mapped to in their credentials, read from project staff or --users-xlsx.
Otherwise a 1-based position from the root (NIGERIA: 1=COUNTRY 2=PROVINCE
3=DISTRICT 4=ADMINISTRATIVEPOST 5=LOCALITY 6=VILLAGE), a type name, or "leaf".

Needs a supervisor/superuser token: the gateway maps
/health-attendance/v1/_create (action 1696) to DISTRICT_SUPERVISOR,
NATIONAL_SUPERVISOR, PROVINCIAL_SUPERVISOR, SUPERUSER, SYSTEM_ADMINISTRATOR.
CAMPAIGN_MANAGER is on none of them. The role is checked up front.

Works against demo and unified-qa (HCM_BASE_URL=https://unified-qa.digit.org
HCM_TENANT_ID=qa). See utils/client.py for why it will not work against UAT.

Staff and attendee mapping is a separate step: ./map_staff.py
"""
import argparse
import json

import utils.hcm as H


def main():
    ap = argparse.ArgumentParser(
        description="Create attendance registers for a campaign (no xlsx).")
    H.add_campaign_arg(ap)
    ap.add_argument("--yes", action="store_true",
                    help="actually write; omit for a dry run")
    ap.add_argument("--register-prefix", default="REG",
                    help="serviceCode prefix (default REG)")
    ap.add_argument("--event-type", default=H.DEFAULT_EVENT_TYPE,
                    help=f"Training|Registration|Distribution "
                         f"(default {H.DEFAULT_EVENT_TYPE})")
    ap.add_argument("--sessions", type=int, default=H.DEFAULT_SESSIONS,
                    choices=(1, 2))
    ap.add_argument("--force", action="store_true",
                    help="create even for boundaries that already have a register")
    ap.add_argument("--name-suffix", default="",
                    help="appended to the register name, e.g. ' v2'")
    ap.add_argument("--level", metavar="auto|N|TYPE|leaf", default=H.DEFAULT_REGISTER_LEVEL,
                    help="hierarchy level to create registers at: 'auto' = where the "
                         "DISTRIBUTOR users are mapped, a 1-based position from the root "
                         "(3 = DISTRICT in NIGERIA), a type name, or 'leaf' "
                         f"(default {H.DEFAULT_REGISTER_LEVEL}, env HCM_REGISTER_LEVEL)")
    ap.add_argument("--users-xlsx", metavar="PATH",
                    help="for --level auto: read users from the workbench "
                         "<campaign>-Users.xlsx instead of the campaign's project staff")
    ap.add_argument("--all-levels", action="store_true",
                    help="create a register at every hierarchy level (ignores --level)")
    args = ap.parse_args()
    H.require_campaign(args)

    hcm, roles = H.connect()
    H.require_roles(roles, H.REGISTER_WRITE_ROLES, "registers", args.yes)

    campaign = hcm.campaign(args.campaign_number)
    number = campaign["campaignNumber"]
    H.show_campaign(campaign)

    existing, search_body = hcm.registers(number)
    bp_map = hcm.projects(number)
    levels = H.boundary_levels(campaign)

    # What level does this campaign already put registers at? Guessing wrong
    # creates registers at a level nobody uses, so show it rather than assume.
    if existing_levels := sorted({levels.get(r.get("localityCode"), "?")
                                  for r in existing}):
        print(f"           existing registers sit at: {existing_levels}")
    print(f"           levels available: "
          f"{sorted(set(levels.values()) & {levels.get(k) for k in bp_map})}")

    users = None
    if str(args.level).lower() == "auto" and not args.all_levels:
        users = (hcm.xlsx_users(args.users_xlsx) if args.users_xlsx
                 else hcm.campaign_staff_users(number))
    bp_map = H.select_boundaries(hcm, campaign, bp_map, args.level, args.all_levels,
                                 users=users)
    print(f"boundaries {len(bp_map)} with a project")
    print(f"registers  {search_body.get('totalCount', len(existing))} existing"
          f"  statusCount={search_body.get('statusCount')}")

    if not bp_map:
        H.die("no boundary has a projectId - the campaign has not finished\n"
              "         creating projects, or /project/v1/_search returned nothing\n"
              "         for referenceID=" + number + ".\n"
              "         Nothing can be created against a boundary with no project.")

    planned = H.plan_registers(
        campaign, bp_map, existing, args.register_prefix,
        args.event_type, args.sessions,
        force=args.force, name_suffix=args.name_suffix,
    )
    print(f"\nplan       {len(planned)} register(s) to create")
    for p in planned[:10]:
        print(f"           {p['serviceCode']}  {p['localityCode']}"
              f"  -> project {p['referenceId'][:8]}…")
    if len(planned) > 10:
        print(f"           … and {len(planned) - 10} more")

    if not planned:
        print("           nothing to do - every boundary with a project already"
              " has a register")
        print()
        return

    if not args.yes:
        print("  DRY RUN  pass --yes to create these")
        print(json.dumps(planned[0], indent=2)[:700])
        print()
        return

    created = hcm.create_registers(planned)
    print(f"  CREATED  {len(created)} register(s)")
    H.report_registers(hcm, number)
    print("\nnext       ./map_staff.py {}  (add --yes to write)".format(number))
    print()


if __name__ == "__main__":
    try:
        main()
    except H.ApiError as e:
        H.die(str(e))
    except KeyboardInterrupt:
        raise SystemExit(130)
