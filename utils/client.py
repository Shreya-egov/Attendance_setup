"""
The HCM API client: auth (token or password grant), RequestInfo, transport,
and every read/write the framework makes. One Hcm instance is one logged-in
session; tests share it through the session-scoped ``hcm`` fixture.

Every request/response is kept as ``last_exchange`` so a failing test can
write it to output/failed_requests/ (see the root conftest.py).
"""
import datetime as dt
import json
import os
import time
import uuid

import requests

from utils.config import (
    ATTENDANCE_BATCH, BASE, INDIVIDUAL_SEARCH_PATHS, LOCALE, PROJECT_SEARCH_PATHS,
    PROJECT_STAFF_PATHS, REGISTER_API_BATCH, TENANT, TIMEOUT,
)


def die(msg):
    """Stop with a message. SystemExit carrying text prints it and exits 1
    from a script; under pytest it fails the test with that text."""
    raise SystemExit(f"\n  ERROR  {msg}\n")


def epoch_ms(d):
    return int(dt.datetime.combine(d, dt.time.min).timestamp() * 1000)


def fmt_date(ms):
    if not ms:
        return "-"
    return dt.datetime.fromtimestamp(ms / 1000).strftime("%d-%m-%Y")


class ApiError(Exception):
    def __init__(self, path, status, text):
        self.path, self.status, self.text = path, status, text
        super().__init__(f"POST {path} -> {status}: {text[:400]}")


class Hcm:
    def __init__(self):
        self.s = requests.Session()
        self.token = None
        self.user = None
        self.roles_known = True
        # boundary code -> type, filled by projects(); covers boundaries a
        # campaign selected via includeAllChildren but does not list itself.
        self.boundary_types = {}
        # the last request/response, for output/failed_requests/ on failure
        self.last_exchange = None
        # Sent as the msgId suffix, which is where excel-ingestion reads the
        # locale it localizes generated templates in (create_campaign sets it).
        self.locale = LOCALE or "en_IN"

    # ---- auth ----------------------------------------------------------

    def login(self):
        """
        Two ways in:

          HCM_AUTH_TOKEN   an access token you already have (from a browser
                           session, or your own curl). Skips the password flow
                           entirely, so neither the password nor the static
                           client credential is needed. Preferred.

          HCM_USERNAME + HCM_PASSWORD + HCM_BASIC_AUTH
                           the full password grant.
        """
        token = os.environ.get("HCM_AUTH_TOKEN")
        if token:
            return self._adopt_token(token.strip())
        return self._password_login()

    def _adopt_token(self, token):
        """
        Resolve userInfo for a token we were handed. The attendance services
        read RequestInfo.userInfo (roles, uuid, tenantId), so an authToken on
        its own is not enough - /user/_details fills in the rest.

        HCM_USER_INFO (inline JSON or a path to a .json file) overrides the
        lookup, for the case where /user/_details is not reachable.
        """
        self.token = token

        override = os.environ.get("HCM_USER_INFO")
        if override:
            try:
                if os.path.exists(override):
                    with open(override) as fh:
                        self.user = json.load(fh)
                else:
                    self.user = json.loads(override)
            except (OSError, ValueError) as e:
                die(f"HCM_USER_INFO is not readable JSON: {e}")
            return self.user

        try:
            r = self.s.post(
                f"{BASE}/user/_details/",
                params={"access_token": token},
                json={"RequestInfo": {
                    "apiId": "Rainmaker", "ver": ".01",
                    "ts": int(time.time() * 1000), "action": "_get",
                    "did": "1", "key": "", "msgId": f"{int(time.time()*1000)}|{LOCALE or 'en_IN'}",
                    "authToken": token,
                }},
                timeout=TIMEOUT,
            )
        except requests.RequestException as e:
            die(f"could not reach {BASE}/user/_details: {e}")

        if r.status_code != 200:
            # Non-fatal. The gateway populates RequestInfo.userInfo from the
            # authToken for the services these scripts call, so an unusable
            # /user/_details costs us the up-front role check, not the run.
            # A genuinely dead token will surface as a 401 on the first real
            # call, with the endpoint named.
            print(f"\n  WARNING  /user/_details returned {r.status_code}; "
                  "cannot read roles up front.")
            print("           Proceeding - the gateway resolves userInfo from the "
                  "token.")
            print("           Set HCM_USER_INFO to restore the role check.")
            self.user = {}
            self.roles_known = False
            return self.user

        self.user = (r.json() or {}).get("UserRequest") or {}
        if not self.user:
            print("\n  WARNING  /user/_details returned no UserRequest; "
                  "role check skipped")
            self.roles_known = False
        return self.user

    def _password_login(self):
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
        """Role codes, or None when we could not determine them."""
        if not self.roles_known:
            return None
        return {r.get("code") for r in (self.user or {}).get("roles") or []}

    def request_info(self):
        return {
            "apiId": "Rainmaker",
            "ver": ".01",
            "ts": int(time.time() * 1000),
            "action": "_create",
            "did": "1",
            "key": "",
            "msgId": f"{int(time.time() * 1000)}|{self.locale}",
            "authToken": self.token,
            "userInfo": self.user,
            "correlationId": str(uuid.uuid4()),
            "plainAccessRequest": {},
        }

    # ---- transport -----------------------------------------------------

    def post(self, path, payload, params=None, expect=(200, 202)):
        body = dict(payload)
        body["RequestInfo"] = self.request_info()
        # Searches are retried on a gateway hiccup (qa answers 500
        # PrematureCloseException / 502-504 now and then); writes never are,
        # since a retried _create can duplicate.
        attempts = 3 if path.endswith(("_search", "/search")) else 1
        for attempt in range(1, attempts + 1):
            r = self.s.post(f"{BASE}{path}", json=body, params=params, timeout=TIMEOUT)
            transient = r.status_code in (502, 503, 504) or (
                r.status_code == 500 and "PrematureClose" in r.text)
            if not transient or attempt == attempts:
                break
            time.sleep(2 * attempt)
        self.last_exchange = {
            "method": "POST", "url": r.url,
            "payload": {k: v for k, v in body.items() if k != "RequestInfo"},
            "status": r.status_code, "response": r.text[:20000],
        }
        if r.status_code not in expect:
            raise ApiError(path, r.status_code, r.text)
        return r.json() if r.content else {}

    def post_first(self, paths, payload, params=None, expect=(200,)):
        """post() to the first path that answers. The same API sits under
        different prefixes per environment (/project vs /health-project), and
        the wrong one 401s/404s rather than routing - or, on unified-qa with a
        SUPERUSER token, reaches a different project service that 400s the
        health-style search ("Any one project search field is required")."""
        for path in paths:
            try:
                return self.post(path, payload, params=params, expect=expect)
            except ApiError as e:
                if e.status not in (400, 401, 403, 404) or path == paths[-1]:
                    raise

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

    def projects(self, number, limit=1000):
        """
        boundaryCode -> projectId via the project service.

        Not /project-factory/v1/data/campaign/_search: that is an INTERNAL
        endpoint that 401s for any client token (verified on demo and UAT
        2026-09-28, both from a CLI token and a logged-in browser session
        with SUPERUSER).

        The join key is referenceID = campaign NUMBER, not the campaign uuid.
        Searching by the uuid silently returns zero rows.
        """
        body = self.post_first(
            PROJECT_SEARCH_PATHS,
            {"Projects": [{"tenantId": TENANT, "referenceID": number}],
             "apiOperation": "SEARCH"},
            params={"tenantId": TENANT, "limit": limit, "offset": 0,
                    "includeDescendants": "true"},
        )
        out = {}
        for pr in body.get("Project") or body.get("Projects") or []:
            addr = pr.get("address") or {}
            code = addr.get("boundary")
            if code and pr.get("id"):
                out[code] = pr["id"]
                if addr.get("boundaryType"):
                    self.boundary_types[code] = addr["boundaryType"]
        return out

    def hierarchy_levels(self, hierarchy_type):
        """
        Boundary types root-first, e.g. [COUNTRY, PROVINCE, DISTRICT, ...].
        Walked from the parentBoundaryType links rather than trusting the
        response order.
        """
        body = self.post(
            "/boundary-service/boundary-hierarchy-definition/_search",
            {"BoundaryTypeHierarchySearchCriteria": {
                "tenantId": TENANT, "hierarchyType": hierarchy_type,
                "limit": 1, "offset": 0}},
            expect=(200,),
        )
        rows = body.get("BoundaryHierarchy") or []
        if not rows:
            die(f"no boundary hierarchy definition for {hierarchy_type} in tenant {TENANT}")
        child_of = {h.get("parentBoundaryType"): h.get("boundaryType")
                    for h in rows[0].get("boundaryHierarchy") or []}
        ordered, cur = [], child_of.get(None)
        while cur and cur not in ordered:
            ordered.append(cur)
            cur = child_of.get(cur)
        return ordered

    def boundary_paths(self, hierarchy_type, codes, batch=50):
        """
        code -> [root, ..., code] for each requested boundary, from
        boundary-relationships/_search?includeParents=true. Codes the
        service does not know map to [code], i.e. exact match only.
        """
        codes = sorted({c for c in codes if c})
        paths = {}

        def walk(node, trail):
            trail = trail + [node.get("code")]
            paths[node.get("code")] = trail
            for child in node.get("children") or []:
                walk(child, trail)

        for i in range(0, len(codes), batch):
            body = self.post(
                "/boundary-service/boundary-relationships/_search", {},
                params={"tenantId": TENANT, "hierarchyType": hierarchy_type,
                        "includeParents": "true",
                        "codes": ",".join(codes[i:i + batch])},
                expect=(200,),
            )
            for tb in body.get("TenantBoundary") or []:
                for root in tb.get("boundary") or []:
                    walk(root, [])
        return {c: paths.get(c, [c]) for c in codes}

    def registers(self, number, detailed=False):
        """
        Registers of a campaign. detailed=True also loads staff and attendees:
        the campaignNumber search ignores includeStaff/includeAttendee
        (unified-qa 2026-10-06), so each register is re-read by serviceCode,
        which is how the workbench register-details page loads them.
        """
        body = self.post(
            "/health-attendance/v1/_search", {},
            params={"tenantId": TENANT, "campaignNumber": number},
            expect=(200,),
        )
        regs = body.get("attendanceRegister") or []
        if detailed:
            full = []
            for r in regs:
                one = self.post(
                    "/health-attendance/v1/_search", {},
                    params={"tenantId": TENANT, "serviceCode": r["serviceCode"],
                            "isServiceCodeExact": "true", "includeStaff": "true",
                            "includeAttendee": "true", "limit": 10, "offset": 0},
                    expect=(200,),
                ).get("attendanceRegister") or []
                full.extend(x for x in one if x.get("id") == r["id"])
            regs = full
        return regs, body

    def project_staff(self, project_ids, limit=200):
        """userIds attached to the given projects, via project/staff/v1/_search."""
        out = set()
        for pid in project_ids:
            body = self.post_first(
                PROJECT_STAFF_PATHS,
                {"ProjectStaff": {"projectId": [pid], "tenantId": TENANT}},
                params={"tenantId": TENANT, "limit": limit, "offset": 0},
            )
            for st in body.get("ProjectStaff") or []:
                if st.get("userId") and not st.get("isDeleted"):
                    out.add((pid, st["userId"]))
        return out

    def users_by_uuid(self, uuids, batch=100):
        """uuid -> {userName, roles}. /user/_search is the only reachable
        source of role codes here; HRMS employees/_search ignores a uuids
        filter and returns the whole tenant."""
        info = {}
        uuids = list(uuids)
        for i in range(0, len(uuids), batch):
            body = self.post(
                "/user/_search",
                {"uuid": uuids[i:i + batch], "tenantId": TENANT},
                expect=(200,),
            )
            for u in body.get("user") or []:
                if u.get("uuid"):
                    info[u["uuid"]] = {
                        "username": u.get("userName"),
                        "name": u.get("name"),
                        "roles": {r.get("code") for r in u.get("roles") or []
                                  if r.get("code")},
                    }
        return info

    def individuals_by_user(self, uuids, batch=100):
        """
        userUuid -> individual id.

        health-attendance stores INDIVIDUAL ids, not user-service uuids, in
        both attendee.individualId and staff.userId. Verified against a live
        register on demo 2026-09-30: its enrolled ids (f007c869, c5121ba8,
        dae75c64) are the individuals of users 3e35e7a9, da5373b4, 05ec037d.
        Sending user uuids here silently defeats the duplicate check and
        enrols the same person twice.
        """
        out = {}
        uuids = list(uuids)
        for i in range(0, len(uuids), batch):
            chunk = uuids[i:i + batch]
            body = self.post_first(
                INDIVIDUAL_SEARCH_PATHS, {"Individual": {"userUuid": chunk}},
                params={"tenantId": TENANT, "limit": batch, "offset": 0},
            )
            for ind in body.get("Individual") or []:
                if ind.get("userUuid") and ind.get("id"):
                    out[ind["userUuid"]] = ind["id"]
        return out

    def campaign_staff_users(self, number):
        """
        Campaign users as [{userId, userUuid, username, name, boundaryCode,
        roles}], from the project service (the internal-only
        /data/campaign/_search 401s for any client token).

        project -> boundary comes from projects(); project -> userId from
        project/staff/v1/_search; userId -> roles from /user/_search.
        """
        bp = self.projects(number)
        proj_to_boundary = {v: k for k, v in bp.items()}
        pairs = self.project_staff(list(proj_to_boundary))
        info = self.users_by_uuid({uid for _, uid in pairs})
        ind = self.individuals_by_user({uid for _, uid in pairs})
        users, no_individual = [], []
        for pid, uid in sorted(pairs):
            meta = info.get(uid, {})
            individual_id = ind.get(uid)
            if not individual_id:
                # Enrolling a user with no Individual record is what produces
                # INDIVIDUAL_SEARCH_RESPONSE_IS_EMPTY; drop them here with a
                # name rather than failing the whole batch at the API.
                no_individual.append(meta.get("username") or uid[:8])
                continue
            users.append({
                "userId": individual_id,
                "userUuid": uid,
                "username": meta.get("username") or uid[:8],
                "name": meta.get("name") or meta.get("username") or uid[:8],
                "boundaryCode": proj_to_boundary.get(pid),
                "roles": meta.get("roles", set()),
            })
        if no_individual:
            print(f"  WARNING  {len(no_individual)} project staff have no Individual "
                  f"record and were skipped: {no_individual[:5]}")
        return users

    def xlsx_users(self, path):
        """
        Campaign users from the workbench "Users" download
        (<campaignNumber>-Users.xlsx, sheet "Create List of Users"), in the
        same shape as campaign_staff_users().

        Its "UserService Uuids" column is the user-service uuid
        (Individual.userUuid), NOT the id attendance stores - that is the
        Individual id (= HRMS employee uuid). Verified on unified-qa
        2026-10-06: USR-278724 is userUuid e85043cd…, individual 580e9d9b….
        So every row goes through the individual search before use.
        """
        from utils.attendance import read_users_xlsx  # attendance imports client
        rows = read_users_xlsx(path)
        ind = self.individuals_by_user({r["userUuid"] for r in rows})
        users, no_individual = [], []
        for r in rows:
            individual_id = ind.get(r["userUuid"])
            if not individual_id:
                no_individual.append(r["username"])
                continue
            users.append({**r, "userId": individual_id})
        if no_individual:
            print(f"  WARNING  {len(no_individual)} xlsx user(s) have no Individual "
                  f"record and were skipped: {no_individual[:5]}")
        return users

    # ---- writes --------------------------------------------------------

    # ---- payment setup (payments-ui "Setup Payment Attributes") --------

    def billing_cycles(self):
        """code -> record from MDMS HCM-BILLING-CONFIG-PAYMENT-SETUP.BillingCycle.
        Empty on a tenant that never had them seeded - the UI then disables
        its billing-cycle dropdown."""
        body = self.post(
            "/egov-mdms-service/v2/_search",
            {"MdmsCriteria": {"tenantId": TENANT,
                              "schemaCode": "HCM-BILLING-CONFIG-PAYMENT-SETUP.BillingCycle",
                              "limit": 100}},
            expect=(200,),
        )
        return {m["data"]["code"]: m["data"] for m in body.get("mdms") or []
                if m.get("isActive") and (m.get("data") or {}).get("active", True)}

    def campaign_type_skills(self, project_type):
        """The wage rows for a campaign type: {skills:[{code,name}],
        rateBreakupSchema, rateMaxLimitSchema}, from MDMS v1 exactly as the
        payment setup page asks for them."""
        body = self.post(
            "/egov-mdms-service/v1/_search",
            {"MdmsCriteria": {"tenantId": TENANT, "moduleDetails": [{
                "moduleName": "HCM-BILLING-CONFIG-PAYMENT-SETUP",
                "masterDetails": [{"name": "CampaignTypeSkills",
                                   "filter": f"[?(@.campaignType=='{project_type}')]"}],
            }]}},
            expect=(200,),
        )
        rows = ((body.get("MdmsRes") or {}).get("HCM-BILLING-CONFIG-PAYMENT-SETUP") or {}
                ).get("CampaignTypeSkills") or []
        return rows[0] if rows else None

    def billing_config(self, number):
        """(billingConfig or None, periods) for a campaign."""
        body = self.post(
            "/health-expense-calculator/billing-config/v1/_search",
            {"searchCriteria": {"tenantId": TENANT, "campaignNumber": number,
                                "includePeriods": True}},
            expect=(200,),
        )
        return body.get("billingConfig"), body.get("periods") or []

    def save_billing_config(self, config, update=False):
        verb = "_update" if update else "_create"
        body = self.post(f"/health-expense-calculator/billing-config/v1/{verb}",
                         {"billingConfig": config}, expect=(200,))
        return body.get("billingConfig") or {}

    def worker_rates(self, root_project_id):
        """The HCM.WORKER_RATES MDMS record of a campaign. Keyed by the ROOT
        PROJECT id - stored in data.campaignId despite the name."""
        body = self.post(
            "/egov-mdms-service/v2/_search",
            {"MdmsCriteria": {"tenantId": TENANT, "schemaCode": "HCM.WORKER_RATES",
                              "uniqueIdentifiers": [root_project_id], "limit": 5}},
            expect=(200,),
        )
        rows = body.get("mdms") or []
        return rows[0] if rows else None

    def save_worker_rates(self, data, existing=None):
        if existing:
            record = {**existing, "data": data}
            path = "/egov-mdms-service/v2/_update/HCM.WORKER_RATES"
        else:
            record = {"tenantId": TENANT, "schemaCode": "HCM.WORKER_RATES",
                      "data": data, "isActive": True}
            path = "/egov-mdms-service/v2/_create/HCM.WORKER_RATES"
        return self.post(path, {"Mdms": record})

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
