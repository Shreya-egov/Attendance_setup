"""

Used by create_campaign.py / e2e.py and tests/test_10_campaign.py.
"""
import datetime as dt
import json
import os
import re
import time
import uuid

from utils.client import ApiError, die, fmt_date
from utils.config import (
    BASE, CAMPAIGN_OUTPUT_DIR, CAMPAIGN_TYPES, DEFAULT_APPROVER_ROLES,
    DEFAULT_MARKER_ROLES, DEFAULT_WORKER_ROLES, TENANT, TIMEOUT,
)
from utils.data_loader import load_payload
from utils.phone_book import PhoneBook, parse_roles
from utils.template_filler import (fill_template, rebase_boundaries, sample_template_path,
                                   write_users)

PROJECT_TYPE_SCHEMA = "HCM-PROJECT-TYPES.projectTypes"
CAMPAIGN_WRITE_ROLES = {"CAMPAIGN_MANAGER"}

# additionalDetails.key the console sets as the wizard advances.
STEP_KEY_DRAFT = 2
STEP_KEY_BOUNDARY = 6
STEP_KEY_DELIVERY = 10
STEP_KEY_FILES = 10

GENERATE_TYPE = "unified-console"
VALIDATION_TYPE = "unified-console-validation"
RESOURCE_TYPE = "unified-console-resources"
FILESTORE_MODULE = "HCM-ADMIN-CONSOLE"
NAME_MAX_LENGTH = 30

# Attendance mapping needs an active user in each bucket (utils.config
# DEFAULT_*_ROLES): a worker becomes an attendee, a marker the register OWNER
# (without one nobody can mark attendance), an approver the APPROVER.
ROLE_BUCKETS = [
    ("worker (attendee)", DEFAULT_WORKER_ROLES),
    ("marker (register OWNER)", DEFAULT_MARKER_ROLES),
    ("approver (APPROVER)", DEFAULT_APPROVER_ROLES),
]

PF = "/project-factory/v1/project-type"
EI = "/excel-ingestion/v1/data"
FILESTORE = "/filestore/v1/files"


# ---- small helpers -----------------------------------------------------

def _iso(ms):
    return dt.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def default_dates():
    """Tomorrow 00:00 -> 31 days later 23:59:59, as the console test uses."""
    now = dt.datetime.now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0) + dt.timedelta(days=1)
    end = now.replace(hour=23, minute=59, second=59, microsecond=0) + dt.timedelta(days=31)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def parse_date(text, end_of_day=False):
    d = dt.datetime.strptime(text, "%d-%m-%Y")
    if end_of_day:
        d = d.replace(hour=23, minute=59, second=59)
    return int(d.timestamp() * 1000)


def unique_name(project_type):
    """Valid under the console's rules: <= 30 chars, starts alphanumeric,
    no consecutive underscores."""
    stem = re.sub(r"[^A-Za-z0-9]+", "", project_type) or "Campaign"
    return f"{stem}_e2e_{uuid.uuid4().hex[:8]}"[:NAME_MAX_LENGTH]


def poll(what, fetch, timeout, delay=5):
    """Call fetch() until it returns a non-None value or timeout seconds pass."""
    deadline = time.time() + timeout
    attempt = 0
    while True:
        attempt += 1
        result = fetch(attempt)
        if result is not None:
            return result
        if time.time() >= deadline:
            die(f"{what} did not settle within {timeout}s")
        time.sleep(delay)


# ---- reads -------------------------------------------------------------

def project_type(hcm, code):
    """The MDMS project type, matched case-insensitively (Bednet / BEDNET)."""
    body = hcm.post("/egov-mdms-service/v2/_search",
                    {"MdmsCriteria": {"tenantId": TENANT, "schemaCode": PROJECT_TYPE_SCHEMA,
                                      "limit": 200}}, expect=(200,))
    types = {m["data"]["code"]: m["data"] for m in body.get("mdms") or []
             if m.get("isActive") is not False and (m.get("data") or {}).get("code")}
    for existing, data in types.items():
        if existing.lower() == code.lower():
            return data
    die(f"project type {code!r} is not in MDMS {PROJECT_TYPE_SCHEMA} on tenant "
          f"{TENANT}. Options: {sorted(types)}")


def default_locale(hcm):
    """HCM_LOCALE, else the tenant's StateInfo defaultLanguage (en_MZ on qa)."""
    if os.environ.get("HCM_LOCALE"):
        return os.environ["HCM_LOCALE"]
    body = hcm.post("/egov-mdms-service/v1/_search",
                    {"MdmsCriteria": {"tenantId": TENANT, "moduleDetails": [{
                        "moduleName": "common-masters",
                        "masterDetails": [{"name": "StateInfo"}]}]}}, expect=(200,))
    info = ((body.get("MdmsRes") or {}).get("common-masters") or {}).get("StateInfo") or []
    return (info[0].get("defaultLanguage") if info else None) or "en_IN"


def _relationships(hcm, hierarchy, **params):
    body = hcm.post("/boundary-service/boundary-relationships/_search", {},
                    params={"tenantId": TENANT, "hierarchyType": hierarchy, **params},
                    expect=(200,))
    return [root for tb in body.get("TenantBoundary") or [] for root in tb.get("boundary") or []]


def select_boundaries(hcm, hierarchy, target=None):
    """
    (boundaries, valid codes, target code). boundaries is the console's
    project-factory format: the root-to-target path, includeAllChildren on the
    target. valid codes are those excel-ingestion will accept on a row - the
    path plus everything under the target.
    """
    if target:
        roots = _relationships(hcm, hierarchy, codes=target, includeParents="true")
        path, node = [], (roots[0] if roots else None)
        while node:
            path.append(node)
            if node.get("code") == target:
                break
            children = node.get("children") or []
            node = children[0] if children else None
        if not path or path[-1].get("code") != target:
            die(f"boundary {target} not found in hierarchy {hierarchy}")
    else:
        root_type = hcm.hierarchy_levels(hierarchy)[0]
        roots = _relationships(hcm, hierarchy, boundaryType=root_type, includeChildren="true")
        if not roots:
            die(f"no boundaries in hierarchy {hierarchy}")
        path, node = [], roots[0]
        while node:
            path.append(node)
            children = node.get("children") or []
            node = children[0] if children else None

    boundaries = []
    for i, node in enumerate(path):
        entry = {"code": node["code"], "name": node["code"], "type": node.get("boundaryType"),
                 "isRoot": i == 0, "includeAllChildren": i == len(path) - 1}
        if i:
            entry["parent"] = path[i - 1]["code"]
        boundaries.append(entry)

    valid = {b["code"] for b in boundaries}

    def walk(n):
        valid.add(n.get("code"))
        for child in n.get("children") or []:
            walk(child)
    for root in _relationships(hcm, hierarchy, codes=path[-1]["code"], includeChildren="true"):
        walk(root)
    return boundaries, valid, path[-1]["code"]


def boundaries_from(hcm, hierarchy, number):
    """
    select_boundaries(), but copying another campaign's selection verbatim,
    e.g. one created in the UI. The target (where re-pointed rows go) is the
    last boundary it marks includeAllChildren.
    """
    source = hcm.campaign(number)
    if source.get("hierarchyType") != hierarchy:
        die(f"{number} uses hierarchy {source.get('hierarchyType')}, not {hierarchy}")
    boundaries = [{k: b[k] for k in ("code", "name", "type", "isRoot", "includeAllChildren",
                                     "parent") if k in b}
                  for b in source.get("boundaries") or []]
    if not boundaries:
        die(f"{number} has no boundaries to copy")
    valid = {b["code"] for b in boundaries}

    def walk(n):
        valid.add(n.get("code"))
        for child in n.get("children") or []:
            walk(child)
    expand = [b["code"] for b in boundaries if b.get("includeAllChildren")]
    for i in range(0, len(expand), 50):
        for root in _relationships(hcm, hierarchy, codes=",".join(expand[i:i + 50]),
                                   includeChildren="true"):
            walk(root)
    return boundaries, valid, (expand or [boundaries[-1]["code"]])[-1]


# ---- project-factory ---------------------------------------------------

def delivery_rules(ptype, start, end):
    """The MDMS project type as the console's deliveryRules array."""
    rule = json.loads(json.dumps(ptype))
    rule["id"] = rule["code"]
    rule.setdefault("name", rule["code"])
    rule.setdefault("IsCycleDisable", True)
    rule.setdefault("attrAddDisable", False)
    rule.setdefault("productCountHide", True)
    rule.setdefault("deliveryAddDisable", True)
    for cycle in rule.get("cycles") or []:
        cycle["startDate"], cycle["endDate"] = start, end
    return [rule]


def cycle_data(ptype, start, end):
    cycles = ptype.get("cycles") or []
    count = len(cycles) or 1
    deliveries = len(cycles[0].get("deliveries") or []) if cycles else 1
    return {
        "cycleConfgureDate": {"cycle": count, "isDisable": True, "deliveries": deliveries or 1},
        "cycleData": [{"key": i + 1, "fromDate": _iso(start), "toDate": _iso(end)}
                      for i in range(count)],
    }


def _details(plan, campaign, key, action="draft", boundaries=None, rules=None,
             cycles=None, resources=None):
    payload = load_payload("campaign", "update_campaign.json")
    d = payload["CampaignDetails"]
    d.update({
        "id": campaign["id"], "tenantId": TENANT, "action": action,
        "campaignNumber": campaign["campaignNumber"], "campaignName": plan["name"],
        "projectType": plan["ptype"]["code"], "hierarchyType": plan["hierarchy"],
        "startDate": plan["start"], "endDate": plan["end"], "locale": plan["locale"],
        "boundaries": boundaries or [], "deliveryRules": rules or [],
        "resources": resources or [],
    })
    d["additionalDetails"].update({
        "key": key, "beneficiaryType": plan["ptype"].get("beneficiaryType", ""),
        "locale": plan["locale"]})
    if cycles:
        d["additionalDetails"]["cycleData"] = cycles
    if boundaries:
        d["boundaryCode"] = next((b for b in boundaries if b["isRoot"]), boundaries[0])["code"]
    return payload


def search_campaign(hcm, number):
    payload = load_payload("campaign", "search_campaign.json")
    payload["CampaignDetails"].update({"tenantId": TENANT, "campaignNumber": number})
    body = hcm.post(f"{PF}/search", payload, expect=(200,))
    return next((c for c in body.get("CampaignDetails") or []
                 if c.get("campaignNumber") == number), None)


# ---- excel-ingestion + filestore ---------------------------------------

def _auth_headers(hcm):
    return {"auth-token": hcm.token, "Authorization": f"Bearer {hcm.token}"}


def download(hcm, file_store_id, dest):
    r = hcm.s.get(f"{BASE}{FILESTORE}/url",
                  params={"tenantId": TENANT, "fileStoreIds": file_store_id},
                  headers=_auth_headers(hcm), timeout=TIMEOUT)
    if r.status_code != 200:
        raise ApiError(f"{FILESTORE}/url", r.status_code, r.text)
    urls = r.json().get("fileStoreIds") or []
    if not urls:
        die(f"filestore has no URL for {file_store_id}")
    blob = hcm.s.get(urls[0]["url"].split(",")[0], timeout=TIMEOUT)
    blob.raise_for_status()
    with open(dest, "wb") as fh:
        fh.write(blob.content)
    return dest


def upload(hcm, path):
    with open(path, "rb") as fh:
        r = hcm.s.post(
            f"{BASE}{FILESTORE}", headers=_auth_headers(hcm), timeout=TIMEOUT,
            data={"tenantId": TENANT, "module": FILESTORE_MODULE},
            files={"file": (os.path.basename(path), fh,
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
    if r.status_code not in (200, 201):
        raise ApiError(FILESTORE, r.status_code, r.text)
    return r.json()["files"][0]["fileStoreId"]


# ---- the flow ----------------------------------------------------------

def plan_campaign(hcm, args):
    """Resolve and print everything creation needs. Read-only."""
    if not args.project_type:
        die(f"no campaign type: pass --type ({', '.join(CAMPAIGN_TYPES)})")
    ptype = project_type(hcm, args.project_type)

    sample = args.sample or sample_template_path(args.project_type)
    if not sample or not os.path.exists(sample):
        die(f"no sample template for {args.project_type}: put a filled unified template "
              f"at templates/{args.project_type}_sample.xlsx or pass --sample")
    hierarchy = args.hierarchy
    if not hierarchy and args.boundaries_from:
        hierarchy = hcm.campaign(args.boundaries_from).get("hierarchyType")
    if not hierarchy:
        die("no hierarchy: set HCM_HIERARCHY_TYPE in .env or pass --hierarchy")

    roles = parse_roles(args.users)
    if not roles:
        die("no users to create: set HCM_USER_ROLES in .env or pass --users")
    missing = [label for label, codes in ROLE_BUCKETS if not set(roles) & codes]

    start, end = default_dates()
    if args.start:
        start = parse_date(args.start)
    if args.end:
        end = parse_date(args.end, end_of_day=True)
    elif args.start:
        end = start + 31 * 86400000 - 1000
    if end <= start:
        die("--end must be after --start")

    if args.boundaries_from and args.boundary:
        die("pass --boundary or --boundaries-from, not both")
    if args.boundaries_from:
        boundaries, valid, target = boundaries_from(hcm, hierarchy, args.boundaries_from)
    else:
        boundaries, valid, target = select_boundaries(hcm, hierarchy, args.boundary)
    plan = {
        "ptype": ptype, "name": args.name or unique_name(ptype["code"]),
        "hierarchy": hierarchy, "locale": args.locale or default_locale(hcm),
        "start": start, "end": end, "sample": sample,
        "boundaries": boundaries, "valid": valid, "target": target,
        "timeout": args.timeout, "roles": roles,
    }
    print("\n== campaign")
    print(f"           {plan['name']}  type={ptype['code']}  hierarchy={hierarchy}"
          f"  locale={plan['locale']}")
    print(f"           {fmt_date(start)} -> {fmt_date(end)}")
    if args.boundaries_from:
        print(f"           boundaries as {args.boundaries_from}:")
    print(f"           boundaries {' > '.join(b['code'] for b in boundaries)}"
          f"  (+ children: {len(valid) - len(boundaries)})")
    print(f"           sample     {sample}")
    # earlier campaigns planned in this run take their numbers first
    phones = PhoneBook().peek(len(roles), skip=PhoneBook.planned)
    PhoneBook.planned += len(roles)
    print(f"           users      {len(roles)} at {target}, phones from {phones[0]}"
          " (skipping registered ones):")
    for role, phone in zip(roles, phones):
        print(f"             {role:<22} {phone}")
    if missing:
        print(f"\n  WARNING  no user for: {', '.join(missing)} - registers will lack it")
    return plan


def create_campaign(hcm, plan):
    """Run the console wizard for plan. Returns the campaign once 'created'."""
    ptype, start, end = plan["ptype"], plan["start"], plan["end"]
    hcm.locale = plan["locale"]  # the template is localized in the msgId locale
    boundaries = plan["boundaries"]
    rules, cycles = delivery_rules(ptype, start, end), cycle_data(ptype, start, end)
    timeout = plan["timeout"]

    # 1. draft
    payload = load_payload("campaign", "create_setup.json")
    payload["CampaignDetails"].update({
        "hierarchyType": plan["hierarchy"], "tenantId": TENANT, "campaignName": plan["name"],
        "projectType": ptype["code"], "startDate": start, "endDate": end,
        "locale": plan["locale"]})
    payload["CampaignDetails"]["additionalDetails"].update(
        {"key": STEP_KEY_DRAFT, "locale": plan["locale"]})
    body = hcm.post(f"{PF}/create", payload)
    campaign = body["CampaignDetails"]
    number = campaign["campaignNumber"]
    print(f"\n  1 draft          {number}  id={campaign['id']}")
    # The draft is persisted asynchronously; an update sent before it is
    # searchable fails with CAMPAIGN_NOT_FOUND.
    poll(f"draft {number}", lambda attempt: search_campaign(hcm, number), timeout, delay=2)

    # 2. boundaries, 3. delivery rules
    hcm.post(f"{PF}/update", _details(plan, campaign, STEP_KEY_BOUNDARY, boundaries=boundaries))
    print(f"  2 boundaries     {len(boundaries)} selected")
    hcm.post(f"{PF}/update", _details(plan, campaign, STEP_KEY_DELIVERY, boundaries=boundaries,
                                      rules=rules, cycles=cycles))
    print(f"  3 delivery rules {len(rules)} rule(s), {len(cycles['cycleData'])} cycle(s)")

    # 4. generate the unified template. For unified campaigns project-factory
    # also starts a generation itself ~2s after an update that changes the
    # boundaries (generateUtils.callGenerateIfBoundariesOrCampaignTypeDiffer),
    # and each _init expires the earlier ones for the campaign. If ours loses
    # that race it comes back "expired"; start another, which is then newest.
    def init_generation():
        payload = load_payload("excel_ingestion", "generate_init.json")
        payload["GenerateResource"].update({
            "tenantId": TENANT, "type": GENERATE_TYPE, "hierarchyType": plan["hierarchy"],
            "referenceId": campaign["id"], "referenceType": ptype["code"]})
        body = hcm.post(f"{EI}/generate/_init", payload)
        return body["GenerateResource"]["id"]

    following = {"id": init_generation(), "restarts": 0}

    def generated(attempt):
        payload = load_payload("excel_ingestion", "generation_search.json")
        payload["GenerationSearchCriteria"].update(
            {"tenantId": TENANT, "ids": [following["id"]]})
        rows = hcm.post(f"{EI}/generate/_search", payload,
                        expect=(200,)).get("GenerationDetails") or []
        status = rows[0].get("status") if rows else None
        print(f"  4 generate       attempt {attempt}: {status}")
        if status == "failed":
            die(f"template generation failed: {json.dumps(rows[0])[:600]}")
        if status == "expired":
            if following["restarts"] >= 3:
                die("template generation keeps being superseded (expired 3 times)")
            following["restarts"] += 1
            following["id"] = init_generation()
            print(f"  4 generate       superseded; restarted as {following['id']}")
            return None
        # "generated" in current excel-ingestion, "completed" in older builds
        done = status in ("generated", "completed") and rows[0].get("fileStoreId")
        return rows[0]["fileStoreId"] if done else None
    gen_fs = poll("template generation", generated, timeout)

    # 5. download, 6. fill from the sample and upload
    os.makedirs(CAMPAIGN_OUTPUT_DIR, exist_ok=True)
    blank = download(hcm, gen_fs, os.path.join(CAMPAIGN_OUTPUT_DIR, f"{number}_generated.xlsx"))
    filled, rows = fill_template(blank, plan["sample"], plan["hierarchy"],
                                   output_path=os.path.join(CAMPAIGN_OUTPUT_DIR, f"{number}_filled.xlsx"),
                                   fill_users=False)
    if not rows:
        die("the sample produced no fill pattern - check its headers match the "
              "generated template")
    moved = rebase_boundaries(filled, plan["valid"], plan["target"])
    for sheet, n in moved.items():
        print(f"  6 fill           '{sheet}': {n} active row(s) re-pointed to {plan['target']}")
    book = PhoneBook()
    users = [dict(book.issue(hcm, role, number), boundary=plan["target"])
             for role in plan["roles"]]
    try:
        write_users(filled, users)
    except ValueError as e:
        die(str(e))
    for u in users:
        print(f"  6 users          {u['role']:<22} {u['phone']}  {u['name']}")
    plan["users"] = users
    upload_fs = upload(hcm, filled)
    print(f"  6 fill           {rows} row(s) filled, uploaded {upload_fs}")

    # 7. validate
    payload = load_payload("excel_ingestion", "process_validation.json")
    payload["ResourceDetails"].update({
        "type": VALIDATION_TYPE, "hierarchyType": plan["hierarchy"], "tenantId": TENANT,
        "fileStoreId": upload_fs, "referenceId": campaign["id"], "locale": plan["locale"]})
    body = hcm.post(f"{EI}/process/_validation", payload)
    proc_id = body["ProcessResource"]["id"]
    plan["files"] = {"generated": blank, "filled": filled, "uploaded": upload_fs,
                     "processId": proc_id}

    def validated(attempt):
        payload = load_payload("excel_ingestion", "process_search.json")
        payload["ProcessingSearchCriteria"].update({"tenantId": TENANT, "ids": [proc_id]})
        rows_ = hcm.post(f"{EI}/process/_search", payload,
                         expect=(200,)).get("ProcessingDetails") or []
        status = rows_[0].get("status") if rows_ else None
        # status only says the job ran; the verdict is processedStatus
        # (additionalDetails.validationStatus): valid / invalid / "error : ..."
        verdict = (rows_[0].get("processedStatus")
                   or (rows_[0].get("additionalDetails") or {}).get("validationStatus")
                   if rows_ else None)
        print(f"  7 validate       attempt {attempt}: {status}"
              + (f" / {verdict}" if verdict else ""))
        if status == "failed" or (status == "completed" and verdict
                                  and str(verdict).lower() != "valid"):
            report_failed_validation(hcm, number, rows_[0])
        return rows_[0] if status == "completed" else None
    poll("template validation", validated, timeout)

    # 8. attach the resource. The update response omits resources even when
    # persisted, so read the campaign back (as the console test does).
    resources = [{"type": RESOURCE_TYPE, "filename": os.path.basename(filled),
                  "filestoreId": upload_fs}]
    hcm.post(f"{PF}/update", _details(plan, campaign, STEP_KEY_FILES, boundaries=boundaries,
                                      rules=rules, cycles=cycles, resources=resources))
    stored = search_campaign(hcm, number) or {}
    resources = stored.get("resources") or []
    if not any(r.get("filestoreId") == upload_fs for r in resources):
        die(f"resource {upload_fs} is not attached to {number} after update")
    print(f"  8 attach         {upload_fs}")

    # 9. create, 10. wait for 'created'
    hcm.post(f"{PF}/update", _details(plan, campaign, STEP_KEY_FILES, action="create",
                                      boundaries=boundaries, rules=rules, cycles=cycles,
                                      resources=resources))
    print("  9 create         submitted")

    def created(attempt):
        c = search_campaign(hcm, number) or {}
        print(f" 10 status         attempt {attempt}: {c.get('status')}")
        if c.get("status") == "failed":
            die(f"campaign {number} failed: "
                  f"{json.dumps(c.get('additionalDetails', {}).get('error'))[:600]}")
        return c if c.get("status") == "created" else None
    return poll(f"campaign {number}", created, timeout)


def report_failed_validation(hcm, number, details):
    """Save the annotated workbook excel-ingestion returns and stop."""
    out = details.get("processedFileStoreId") or details.get("processedFilestoreId")
    msg = f"template validation failed for {number}"
    if out:
        path = download(hcm, out, os.path.join(CAMPAIGN_OUTPUT_DIR, f"{number}_validation_errors.xlsx"))
        msg += f"\n         row-level errors are in {path}"
    extra = details.get("additionalDetails") or {}
    if extra:
        msg += f"\n         {json.dumps(extra)[:600]}"
    die(msg)
