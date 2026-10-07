#!/usr/bin/env python3
"""
Create attendance registers and map attendees for a campaign - via direct API
payloads, no spreadsheet.

    ./create_attendance.py CMP-2026-09-15-005352                 # inspect + plan (dry run)
    ./create_attendance.py CMP-2026-09-15-005352 --yes           # actually write
    ./create_attendance.py CMP-2026-09-15-005352 --registers-only --yes
    ./create_attendance.py CMP-2026-09-15-005352 --attendees-only --yes

WHY THIS WORKS WITHOUT AN XLSX
------------------------------
The UI route is: upload a filled template -> project-factory
resource-details/_create -> excel-ingestion parse -> kafka -> health-attendance.
project-factory is just building JSON payloads at the end of that chain, and
those endpoints are reachable directly - the gate is the API-gateway role map,
not the service:

    action 1696  /health-attendance/v1/_create           DISTRICT_SUPERVISOR,
    action 1698  /health-attendance/v1/_update           NATIONAL_SUPERVISOR,
    action 1704  /health-attendance/staff/v1/_create     PROVINCIAL_SUPERVISOR,
                                                         SUPERUSER,
                                                         SYSTEM_ADMINISTRATOR
    action 1702  /health-attendance/attendee/v1/_create  + PROXIMITY_SUPERVISOR

(read from MDMS ACCESSCONTROL-ACTIONS-TEST/actions-test and
ACCESSCONTROL-ROLEACTIONS/roleactions on 2026-09-17.)

CAMPAIGN_MANAGER is NOT in those lists, which is the whole reason the old notes
recorded "401, direct CRUD is role-gated, blocked". It is not blocked - it needs
a supervisor/superuser token. This script checks your roles up front and tells
you exactly which one is missing rather than firing and failing.

PAYLOAD SHAPES
--------------
Taken from project-factory, which is the production client of these APIs
(attendanceRegister-processClass.ts, attendanceRegisterAttendee-processClass.ts):

  POST /health-attendance/v1/_create           {RequestInfo, attendanceRegister:[...]}
  POST /health-attendance/attendee/v1/_create  {RequestInfo, attendees:[...]}
  POST /health-attendance/staff/v1/_create     {RequestInfo, staff:[...]}

register = {tenantId, name, referenceId(projectId), campaignNumber, serviceCode,
            startDate, endDate, localityCode, additionalDetails{campaignNumber,
            campaignName, eventType, sessions}}
attendee = {tenantId, registerId(uuid), individualId, enrollmentDate[, tag]}
staff    = {tenantId, registerId(uuid), userId, enrollmentDate, staffType}

`individualId` and `userId` are both the HRMS user uuid (project-factory resolves
username -> emp.user.uuid), which is exactly what campaign_data type=user carries
in uniqueIdAfterProcess - so no HRMS round trip is needed here.

ENROLLMENT DATES
----------------
Defaults to the campaign start date. An enrollment date before the register start
is REJECTED by validation on the sheet route
(HCM_ATTENDANCE_ATTENDEE_DATE_OUT_OF_RANGE) but the direct API route does not run
that processor, so this script enforces the window itself and refuses to send an
out-of-range date. See docs/register-attendee-api-chain.md section 5a.

Writes are DRY RUN unless --yes.
"""
import argparse
import base64
import datetime as dt
import json
import os
import sys
import time
import uuid

import requests

BASE = os.environ.get("HCM_BASE_URL", "https://health-demo.digit.org").rstrip("/")
TENANT = os.environ.get("HCM_TENANT_ID", "demo")
TIMEOUT = 60

# Roles the API gateway maps to the attendance write actions.
REGISTER_WRITE_ROLES = {
    "DISTRICT_SUPERVISOR", "NATIONAL_SUPERVISOR", "PROVINCIAL_SUPERVISOR",
    "SUPERUSER", "SYSTEM_ADMINISTRATOR",
}
ATTENDEE_WRITE_ROLES = REGISTER_WRITE_ROLES | {"PROXIMITY_SUPERVISOR"}

# Sheet-equivalent role buckets. The three attendee sheets map to:
#   worker sheet   -> attendee
#   marker sheet   -> staff, staffType OWNER
#   approver sheet -> staff, staffType APPROVER
DEFAULT_MARKER_ROLES = {"PROXIMITY_SUPERVISOR"}
DEFAULT_APPROVER_ROLES = {
    "CAMPAIGN_SUPERVISOR", "DISTRICT_SUPERVISOR", "PROVINCIAL_SUPERVISOR",
    "NATIONAL_SUPERVISOR",
}

# project-factory defaults (config/index.ts attendanceRegister)
DEFAULT_EVENT_TYPE = "Training"
DEFAULT_SESSIONS = 1
REGISTER_API_BATCH = 100
ATTENDANCE_BATCH = 50


def die(msg, code=1):
    print(f"\n  ERROR  {msg}\n", file=sys.stderr)
    sys.exit(code)


def epoch_ms(d):
    return int(dt.datetime.combine(d, dt.time.min).timestamp() * 1000)


def fmt_date(ms):
    if not ms:
        return "-"
    return dt.datetime.fromtimestamp(ms / 1000).strftime("%d-%m-%Y")


class Hcm:
    def __init__(self):
        self.s = requests.Session()
        self.token = None
        self.user = None

    # ---- auth ----------------------------------------------------------

    def login(self):
        user = os.environ.get("HCM_USERNAME")
        pwd = os.environ.get("HCM_PASSWORD")
        basic = os.environ.get("HCM_BASIC_AUTH")
        if not (user and pwd):
            die("set HCM_USERNAME and HCM_PASSWORD")
        if not basic:
            die(
                "set HCM_BASIC_AUTH - the token endpoint needs a static client\n"
                "         credential on top of the user credentials:\n"
                "           export HCM_BASIC_AUTH=$(printf '<id>:<secret>' | base64 -w0)"
            )
        r = self.s.post(
            f"{BASE}/user/oauth/token",
            data={
                "username": user, "password": pwd, "tenantId": TENANT,
                "userType": "EMPLOYEE", "scope": "read", "grant_type": "password",
                "check": "true",
            },
            headers={"Authorization": f"Basic {basic}"},
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            die(f"login failed {r.status_code}: {r.text[:300]}")
        body = r.json()
        self.token = body.get("access_token")
        self.user = body.get("UserRequest") or {}
        if not self.token:
            die(f"login returned no access_token: {json.dumps(body)[:300]}")
        return self.user

    def roles(self):
        return {r.get("code") for r in (self.user or {}).get("roles") or []}

    def request_info(self):
        return {
            "apiId": "Rainmaker",
            "ver": ".01",
            "ts": int(time.time() * 1000),
            "action": "_create",
            "did": "1",
            "key": "",
            "msgId": f"{int(time.time() * 1000)}|en_IN",
            "authToken": self.token,
            "userInfo": self.user,
            "correlationId": str(uuid.uuid4()),
            "plainAccessRequest": {},
        }

    # ---- transport -----------------------------------------------------

    def post(self, path, payload, params=None, expect=(200, 202)):
        body = dict(payload)
        body["RequestInfo"] = self.request_info()
        r = self.s.post(
            f"{BASE}{path}", json=body, params=params, timeout=TIMEOUT
        )
        if r.status_code not in expect:
            raise ApiError(path, r.status_code, r.text)
        return r.json() if r.content else {}

    # ---- reads ---------------------------------------------------------

    def campaign(self, number):
        body = self.post(
            "/project-factory/v1/project-type/search",
            {"CampaignDetails": {"tenantId": TENANT, "campaignNumber": number}},
            expect=(200,),
        )
        rows = body.get("CampaignDetails") or []
        if not rows:
            die(f"campaign {number} not found in tenant {TENANT}")
        return rows[0]

    def campaign_data(self, number, type_, limit=1000):
        """campaign_data rows. type=boundary gives boundaryCode -> projectId."""
        out, offset = [], 0
        while True:
            body = self.post(
                "/project-factory/v1/data/campaign/_search",
                {
                    "SearchCriteria": {
                        "tenantId": TENANT, "type": type_, "campaignNumber": number,
                    },
                    "Pagination": {"limit": limit, "offset": offset},
                },
                expect=(200,),
            )
            rows = (
                body.get("CampaignData")
                or body.get("campaignData")
                or body.get("data")
                or []
            )
            out.extend(rows)
            if len(rows) < limit:
                return out
            offset += limit

    def registers(self, number):
        body = self.post(
            "/health-attendance/v1/_search", {},
            params={"tenantId": TENANT, "campaignNumber": number},
            expect=(200,),
        )
        return body.get("attendanceRegister") or [], body

    # ---- writes --------------------------------------------------------

    def create_registers(self, registers):
        created = []
        for i in range(0, len(registers), REGISTER_API_BATCH):
            batch = registers[i:i + REGISTER_API_BATCH]
            body = self.post(
                "/health-attendance/v1/_create", {"attendanceRegister": batch}
            )
            created.extend(body.get("attendanceRegister") or [])
        return created

    def create_attendees(self, attendees):
        for i in range(0, len(attendees), ATTENDANCE_BATCH):
            self.post(
                "/health-attendance/attendee/v1/_create",
                {"attendees": attendees[i:i + ATTENDANCE_BATCH]},
            )

    def create_staff(self, staff):
        for i in range(0, len(staff), ATTENDANCE_BATCH):
            self.post(
                "/health-attendance/staff/v1/_create",
                {"staff": staff[i:i + ATTENDANCE_BATCH]},
            )


class ApiError(Exception):
    def __init__(self, path, status, text):
        self.path, self.status, self.text = path, status, text
        super().__init__(f"POST {path} -> {status}: {text[:400]}")


# ---- planning ----------------------------------------------------------


def boundary_project_map(rows):
    """
    boundaryCode -> projectId, exactly as project-factory builds it
    (attendanceRegister-processClass.ts buildBoundaryProjectMap): boundary rows
    carry the code in data and the created projectId in uniqueIdAfterProcess.
    """
    out = {}
    for row in rows:
        data = row.get("data") or {}
        code = data.get("HCM_ADMIN_CONSOLE_BOUNDARY_CODE") or row.get("uniqueIdentifier")
        project_id = row.get("uniqueIdAfterProcess")
        if code and project_id:
            out[code] = project_id
    return out


def campaign_users(rows):
    """
    user rows -> [{userId, boundaryCode, roles[], username}]. uniqueIdAfterProcess
    is the HRMS user uuid, which is what the attendance APIs want for both
    attendee.individualId and staff.userId.
    """
    users = []
    for row in rows:
        data = row.get("data") or {}
        user_id = row.get("uniqueIdAfterProcess")
        if not user_id:
            continue
        roles_raw = data.get("HCM_ADMIN_CONSOLE_USER_ROLE") or ""
        roles = {
            r.strip().upper().replace(" ", "_")
            for r in str(roles_raw).replace(",", "#").split("#")
            if r.strip()
        }
        users.append({
            "userId": user_id,
            "username": data.get("UserName") or row.get("uniqueIdentifier"),
            "boundaryCode": data.get("HCM_ADMIN_CONSOLE_BOUNDARY_CODE_MANDATORY")
                            or data.get("HCM_ADMIN_CONSOLE_BOUNDARY_CODE"),
            "roles": roles,
        })
    return users


def plan_registers(campaign, bp_map, existing, prefix, event_type, sessions):
    """One register per campaign boundary that has a project and no register yet."""
    by_locality = {r.get("localityCode") for r in existing}
    name = campaign.get("campaignName") or campaign.get("campaignNumber")
    number = campaign["campaignNumber"]
    start, end = campaign.get("startDate"), campaign.get("endDate")
    seq = len(existing)
    planned = []
    for code, project_id in sorted(bp_map.items()):
        if code in by_locality:
            continue
        seq += 1
        planned.append({
            "tenantId": TENANT,
            "name": f"{name} {code}",
            "referenceId": project_id,
            "campaignNumber": number,
            "serviceCode": f"{prefix}-{number.split('-')[-1]}-{seq:03d}",
            "startDate": start,
            "endDate": end,
            "localityCode": code,
            "additionalDetails": {
                "campaignNumber": number,
                "campaignName": name,
                "eventType": event_type,
                "sessions": sessions,
            },
        })
    return planned


def plan_attendees(registers, users, enrollment_ms, marker_roles, approver_roles):
    """
    Bucket each campaign user into the register for their boundary:
      marker roles   -> staff OWNER
      approver roles -> staff APPROVER
      everyone else  -> attendee
    Mirrors the worker/marker/approver sheets of the xlsx route.
    """
    by_locality = {}
    for r in registers:
        by_locality.setdefault(r.get("localityCode"), r)

    # Already-enrolled sets per register. These APIs are not idempotent - a
    # second _create for the same (register, user) either duplicates or 400s,
    # so skip them here rather than finding out per batch.
    enrolled_attendees, enrolled_staff = {}, {}
    for loc, r in by_locality.items():
        rid = r["id"]
        enrolled_attendees[rid] = {
            a.get("individualId") for a in r.get("attendees") or []
        }
        enrolled_staff[rid] = {
            (s.get("userId"), s.get("staffType")) for s in r.get("staff") or []
        }

    attendees, staff, unplaced, skipped = [], [], [], []
    for u in users:
        reg = by_locality.get(u["boundaryCode"])
        if not reg:
            unplaced.append(u)
            continue
        rid = reg["id"]
        common = {
            "tenantId": TENANT,
            "registerId": rid,
            "enrollmentDate": enrollment_ms,
        }

        if u["roles"] & marker_roles:
            staff_type = "OWNER"
        elif u["roles"] & approver_roles:
            staff_type = "APPROVER"
        else:
            staff_type = None

        if staff_type:
            if (u["userId"], staff_type) in enrolled_staff[rid]:
                skipped.append(u)
                continue
            staff.append({**common, "userId": u["userId"], "staffType": staff_type})
        else:
            if u["userId"] in enrolled_attendees[rid]:
                skipped.append(u)
                continue
            attendees.append({**common, "individualId": u["userId"]})

    return attendees, staff, unplaced, skipped


# ---- main --------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser(
        description="Create attendance registers + map attendees for a campaign "
                    "using direct API payloads (no xlsx).",
    )
    ap.add_argument("campaign_number")
    ap.add_argument("--yes", action="store_true",
                    help="actually write; omit for a dry run")
    ap.add_argument("--registers-only", action="store_true")
    ap.add_argument("--attendees-only", action="store_true")
    ap.add_argument("--register-prefix", default="REG",
                    help="serviceCode prefix (default REG)")
    ap.add_argument("--event-type", default=DEFAULT_EVENT_TYPE,
                    help=f"Training|Registration|Distribution (default {DEFAULT_EVENT_TYPE})")
    ap.add_argument("--sessions", type=int, default=DEFAULT_SESSIONS,
                    choices=(1, 2))
    ap.add_argument("--enrollment-date", metavar="DD-MM-YYYY",
                    help="default: campaign start date")
    ap.add_argument("--marker-roles", default=",".join(sorted(DEFAULT_MARKER_ROLES)))
    ap.add_argument("--approver-roles", default=",".join(sorted(DEFAULT_APPROVER_ROLES)))
    args = ap.parse_args()

    hcm = Hcm()
    user = hcm.login()
    roles = hcm.roles()
    print(f"\nenv        {BASE}  (tenant {TENANT})")
    print(f"logged in  {user.get('userName')}  roles={sorted(roles) or '[]'}")

    want_registers = not args.attendees_only
    want_attendees = not args.registers_only

    missing = []
    if want_registers and not (roles & REGISTER_WRITE_ROLES):
        missing.append(f"registers need one of {sorted(REGISTER_WRITE_ROLES)}")
    if want_attendees and not (roles & ATTENDEE_WRITE_ROLES):
        missing.append(f"attendees need one of {sorted(ATTENDEE_WRITE_ROLES)}")
    if missing and args.yes:
        die("this token cannot write:\n         " + "\n         ".join(missing)
            + "\n\n         CAMPAIGN_MANAGER is deliberately not on those lists."
              "\n         Re-run with a supervisor/superuser account, or use the"
              "\n         xlsx route (suite/payloads/registers_create.json).")
    if missing:
        print("\n  WARNING  token lacks the write roles; dry run only:")
        for m in missing:
            print(f"           {m}")

    # ---- resolve ----
    campaign = hcm.campaign(args.campaign_number)
    number = campaign["campaignNumber"]
    print(f"\ncampaign   {number}  {campaign.get('campaignName')}")
    print(f"           id={campaign.get('id')}  type={campaign.get('projectType')}"
          f"  hierarchy={campaign.get('hierarchyType')}  status={campaign.get('status')}")
    print(f"           window {fmt_date(campaign.get('startDate'))}"
          f" -> {fmt_date(campaign.get('endDate'))}")

    if args.enrollment_date:
        try:
            enroll_d = dt.datetime.strptime(args.enrollment_date, "%d-%m-%Y").date()
        except ValueError:
            die(f"--enrollment-date must be DD-MM-YYYY, got {args.enrollment_date!r}")
        enrollment_ms = epoch_ms(enroll_d)
    else:
        enrollment_ms = campaign.get("startDate")

    c_start, c_end = campaign.get("startDate"), campaign.get("endDate")
    if enrollment_ms and c_start and enrollment_ms < c_start:
        die(
            f"enrollment date {fmt_date(enrollment_ms)} is before the campaign "
            f"start {fmt_date(c_start)}.\n"
            "         That must be rejected, not clamped - refusing to send it."
        )
    if enrollment_ms and c_end and enrollment_ms > c_end:
        die(f"enrollment date {fmt_date(enrollment_ms)} is after the campaign "
            f"end {fmt_date(c_end)}")

    bp_map = boundary_project_map(hcm.campaign_data(number, "boundary"))
    existing, search_body = hcm.registers(number)
    print(f"boundaries {len(bp_map)} with a project")
    print(f"registers  {search_body.get('totalCount', len(existing))} existing"
          f"  statusCount={search_body.get('statusCount')}")

    # ---- registers ----
    created = []
    if want_registers:
        planned = plan_registers(
            campaign, bp_map, existing, args.register_prefix,
            args.event_type, args.sessions,
        )
        print(f"\nplan       {len(planned)} register(s) to create")
        for p in planned[:10]:
            print(f"           {p['serviceCode']}  {p['localityCode']}  -> project {p['referenceId'][:8]}…")
        if len(planned) > 10:
            print(f"           … and {len(planned) - 10} more")

        if planned and args.yes:
            created = hcm.create_registers(planned)
            print(f"  CREATED  {len(created)} register(s)")
        elif planned:
            print("  DRY RUN  pass --yes to create these")
            print(json.dumps(planned[0], indent=2)[:700])

        if created:
            existing, search_body = hcm.registers(number)

    # ---- attendees ----
    if want_attendees:
        users = campaign_users(hcm.campaign_data(number, "user"))
        registers_now = existing
        if not registers_now:
            print("\nattendees  no registers exist yet - create them first")
            return
        attendees, staff, unplaced, skipped = plan_attendees(
            registers_now, users, enrollment_ms,
            {r.strip().upper() for r in args.marker_roles.split(",") if r.strip()},
            {r.strip().upper() for r in args.approver_roles.split(",") if r.strip()},
        )
        print(f"\nusers      {len(users)} in campaign"
              f"  ({len(unplaced)} with no register for their boundary,"
              f" {len(skipped)} already enrolled)")
        print(f"plan       {len(attendees)} attendee(s), {len(staff)} staff"
              f"  enrollmentDate={fmt_date(enrollment_ms)}")
        owners = sum(1 for s in staff if s["staffType"] == "OWNER")
        print(f"           staff split: {owners} OWNER, {len(staff) - owners} APPROVER")

        if (attendees or staff) and args.yes:
            if attendees:
                hcm.create_attendees(attendees)
                print(f"  CREATED  {len(attendees)} attendee(s)")
            if staff:
                hcm.create_staff(staff)
                print(f"  CREATED  {len(staff)} staff")
        elif attendees or staff:
            print("  DRY RUN  pass --yes to create these")
            if attendees:
                print("  attendee: " + json.dumps(attendees[0]))
            if staff:
                print("  staff:    " + json.dumps(staff[0]))

    # ---- verify ----
    if args.yes:
        final, body = hcm.registers(number)
        n_att = sum(len(r.get("attendees") or []) for r in final)
        n_staff = sum(len(r.get("staff") or []) for r in final)
        print(f"\nverify     {body.get('totalCount')} register(s),"
              f" {n_att} attendee(s), {n_staff} staff")
        for r in final:
            print(f"           {r['registerNumber']}  {r.get('serviceCode')}"
                  f"  {r.get('localityCode')}"
                  f"  attendees={len(r.get('attendees') or [])}"
                  f"  staff={len(r.get('staff') or [])}")
    print()


if __name__ == "__main__":
    try:
        main()
    except ApiError as e:
        die(str(e))
    except KeyboardInterrupt:
        sys.exit(130)
