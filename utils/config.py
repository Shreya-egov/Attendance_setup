"""
Configuration for the HCM attendance + payment framework.

Everything environment-specific comes from .env in the project root (real
environment variables win), so the same tests and scripts run against
unified-qa or demo by changing .env only. See .env.example for every key.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAYLOADS_DIR = os.path.join(ROOT, "payloads")
DATA_DIR = os.path.join(ROOT, "data")
TEMPLATES_DIR = os.path.join(DATA_DIR, "templates")
OUTPUT_DIR = os.path.join(ROOT, "output")
CAMPAIGN_OUTPUT_DIR = os.path.join(OUTPUT_DIR, "campaigns")


def _load_dotenv(path=os.path.join(ROOT, ".env")):
    """KEY=VALUE lines from .env, so credentials never need to be on a
    command line. Real environment variables win."""
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip().strip("'\""))
    except FileNotFoundError:
        pass


_load_dotenv()
env = os.environ.get

# ---- environment -------------------------------------------------------

BASE = os.environ.get("HCM_BASE_URL", "https://health-demo.digit.org").rstrip("/")
TENANT = os.environ.get("HCM_TENANT_ID", "demo")
TIMEOUT = 60

# Roles the API gateway maps to the attendance write actions.
REGISTER_WRITE_ROLES = {
    "DISTRICT_SUPERVISOR", "NATIONAL_SUPERVISOR", "PROVINCIAL_SUPERVISOR",
    "SUPERUSER", "SYSTEM_ADMINISTRATOR",
}
ATTENDEE_WRITE_ROLES = REGISTER_WRITE_ROLES | {"PROXIMITY_SUPERVISOR"}

# Sheet-equivalent role buckets. The three attendee sheets observed live on
# demo are "Frontline Workers" / "Attendance Markers" / "Attendance Approvers":
#   worker sheet   -> attendee
#   marker sheet   -> staff, staffType OWNER
#   approver sheet -> staff, staffType APPROVER
# Role lists match unified-qa MDMS HCM-ADMIN-CONSOLE.excelIngestionGenerate
# "attendanceRegisterAttendee" (2026-10-06). A user matching several buckets
# lands in one, priority APPROVER > MARKER > WORKER, as excel-ingestion does.
# Roles in no bucket (PAYMENT_*) are not enrolled at all.
DEFAULT_MARKER_ROLES = {"TEAM_SUPERVISOR", "DISTRICT_SUPERVISOR"}
DEFAULT_APPROVER_ROLES = {"PROXIMITY_SUPERVISOR", "CAMPAIGN_SUPERVISOR"}
DEFAULT_WORKER_ROLES = {
    "DISTRIBUTOR", "REGISTRAR", "FIELD_SUPPORT", "HEALTH_FACILITY_WORKER",
}
# Roles allowed on more than one active register (MDMS multiRegisterAllowedRoles).
# Anyone else - every worker - may be an attendee of only one.
MULTI_REGISTER_ROLES = {
    "PROXIMITY_SUPERVISOR", "DISTRICT_SUPERVISOR", "TEAM_SUPERVISOR",
    "CAMPAIGN_SUPERVISOR",
}

# Hierarchy level registers are created at: "auto" = wherever the campaign's
# DISTRIBUTOR users are mapped (their boundary in the user credentials), or a
# 1-based position from the root (COUNTRY=1, PROVINCE=2, DISTRICT=3 in
# NIGERIA), a boundary type name, or "leaf".
DEFAULT_REGISTER_LEVEL = os.environ.get("HCM_REGISTER_LEVEL", "auto")
AUTO_LEVEL_ROLE = os.environ.get("HCM_AUTO_LEVEL_ROLE", "DISTRIBUTOR")

# Project / staff / individual search live under different gateway paths
# per environment. /health-* goes first: on unified-qa the plain paths reach
# other services that 401, 400, or - for project staff - answer 200 with
# ZERO rows, which no fallback can detect (verified 2026-10-06). Where
# /health-* does not exist (demo) it 404s and the plain path is used.
PROJECT_SEARCH_PATHS = ("/health-project/v1/_search", "/project/v1/_search")
PROJECT_STAFF_PATHS = ("/health-project/staff/v1/_search", "/project/staff/v1/_search")
INDIVIDUAL_SEARCH_PATHS = ("/health-individual/v1/_search", "/individual/v1/_search")

# project-factory defaults (config/index.ts attendanceRegister)
DEFAULT_EVENT_TYPE = "Training"
DEFAULT_SESSIONS = 1
REGISTER_API_BATCH = 100
ATTENDANCE_BATCH = 50


# ---- campaign creation (create_campaign.py / e2e.py / tests) -----------

# Campaign types created, one campaign each; matched case-insensitively to
# MDMS HCM-PROJECT-TYPES (BEDNET -> Bednet).
CAMPAIGN_TYPES = [t.strip().upper() for t in
                  (env("HCM_CAMPAIGN_TYPES") or "BEDNET,MR-DN").split(",") if t.strip()]
HIERARCHY_TYPE = env("HCM_HIERARCHY_TYPE") or None
BOUNDARIES_FROM = env("HCM_BOUNDARIES_FROM") or None
BOUNDARY = env("HCM_BOUNDARY") or None
SAMPLE_TEMPLATE = env("HCM_SAMPLE_TEMPLATE") or None
LOCALE = env("HCM_LOCALE") or None
CREATE_TIMEOUT = int(env("HCM_CREATE_TIMEOUT") or 900)

# Users created with each campaign: one per role.
DEFAULT_USER_ROLES = ("DISTRIBUTOR,FIELD_SUPPORT,TEAM_SUPERVISOR,PAYMENT_EDITOR,"
                      "PAYMENT_REVIEWER,PAYMENT_APPROVER,PROXIMITY_SUPERVISOR,"
                      "CAMPAIGN_SUPERVISOR")
USER_ROLES = env("HCM_USER_ROLES") or DEFAULT_USER_ROLES
USER_PHONE_START = int(env("HCM_USER_PHONE_START") or 9100000001)
PHONE_BOOK = os.path.join(DATA_DIR, "issued_users.json")

# ---- payment setup -----------------------------------------------------

BILLING = env("HCM_BILLING") or None
BILLING_DAYS = int(env("HCM_BILLING_DAYS")) if env("HCM_BILLING_DAYS") else None
RATES = env("HCM_RATES") or None
