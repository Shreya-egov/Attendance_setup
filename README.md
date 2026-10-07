# DIGIT HCM — campaign → registers → user mapping → payment setup

An API automation framework, structured like `api_automation_project`, for the
whole HCM attendance + payment flow: it **creates a campaign** the way the admin
console does, **creates attendance registers**, **maps users** onto them
(attendees, OWNER, APPROVER) and **sets up payment** (billing cycle + wages) —
once per campaign type (BEDNET, MR-DN).

Two ways to drive it, over the same `utils/` code:

* **pytest** — the suite in `tests/`, with assertions on server state, a
  results JSON, an HTML dashboard and the failing request of every failed test.
* **command-line scripts** in the root — `./e2e.py` and the single-step tools,
  with a dry run by default.

## Project structure

```
.
├── conftest.py              # session hooks: results JSON, dashboard, failed requests
├── pytest.ini               # markers, testpaths
├── .env / .env.example      # environment, credentials, campaign + payment settings
├── utils/
│   ├── config.py            # every setting from .env, service paths, role buckets
│   ├── client.py            # Hcm: login, RequestInfo, all reads and writes
│   ├── campaign.py          # console campaign creation (ported from api_automation console branch)
│   ├── template_filler.py   # fills the generated campaign template from a sample
│   ├── phone_book.py        # users to create + sequential, tracked phone numbers
│   ├── attendance.py        # register level, register + mapping planners
│   ├── payment.py           # billing cycle + wage planning, applying
│   ├── data_loader.py       # load_payload(service, file)
│   ├── store.py             # campaigns handed between test stages (output/campaigns.json)
│   ├── dashboard.py         # output/dashboard.html
│   ├── cli.py               # argument + banner helpers for the scripts
│   └── hcm.py               # one import for the scripts (import utils.hcm as H)
├── payloads/
│   ├── campaign/            # project-factory create / update / search bodies
│   └── excel_ingestion/     # template generate / validate bodies
├── data/
│   ├── templates/           # BEDNET_sample.xlsx, MR-DN_sample.xlsx
│   └── issued_users.json    # every phone number issued, and the next one
├── tests/
│   ├── conftest.py          # fixtures: hcm, campaign_type, campaign, args from .env
│   ├── test_00_preflight.py # read-only: roles, MDMS, samples, billing + wage config
│   ├── test_10_campaign.py  # create the campaign; its users exist with their roles
│   ├── test_20_registers.py # registers where the DISTRIBUTORs are; no duplicates on re-run
│   ├── test_30_user_mapping.py # attendees / OWNER / APPROVER; payment roles not enrolled
│   └── test_40_payment.py   # billing config + periods + wages; unchanged on re-run
├── e2e.py, create_campaign.py, setup_attendance.py,
│   create_registers.py, map_staff.py, setup_payment.py   # CLI wrappers over utils/
├── docs/                    # register-attendee-api-chain.md
├── legacy/                  # create_attendance.py, setup_campaign.py (demo era, self-contained)
└── output/                  # generated: campaigns/, failed_requests/, test_results.json, dashboard.html
```

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env && chmod 600 .env     # fill in HCM_USERNAME / HCM_PASSWORD
```

Everything reads `.env` from this folder (real environment variables win) — no
`source` needed. `.env.example` documents every key.

## Commands

All from this folder (`cd ~/Downloads/attendancepayments`).

**pytest**
```bash
pytest -m preflight                     # read-only checks, run this first
pytest                                  # full flow: create campaign → registers → mapping → payment, BEDNET + MR-DN
pytest -k BEDNET                        # only BEDNET
pytest -k MR-DN                         # only MR-DN
pytest -m create                        # only create the campaigns (and check their users)
pytest -m "not create"                  # re-verify stored campaigns, nothing new created
pytest -m attendance                    # registers + user mapping only
pytest -m payment                       # payment only
pytest -m "not writes"                  # nothing that changes server state
pytest tests/test_30_user_mapping.py    # a single stage
```

**Dashboard** — written to `output/dashboard.html` by every pytest run
```bash
pytest                                   # or any pytest command; writes output/dashboard.html
xdg-open output/dashboard.html           # open it

# rebuild it from the last run's results, without running tests again
python3 -c "import json; from utils.dashboard import generate_dashboard; print(generate_dashboard(json.load(open('output/test_results.json'))))"
```

**HTML report** (pytest-html) — more detail, with the logs of every test
```bash
pytest --html=output/report.html --self-contained-html               # full run + report
pytest -m preflight --html=output/report.html --self-contained-html  # any selection works the same way
xdg-open output/report.html
```

**Raw results**
```bash
cat output/test_results.json            # every test: outcome, duration, failure detail
ls output/failed_requests/              # request/response of each failed test
cat output/campaigns.json               # campaigns the tests are using
cat data/issued_users.json              # every phone number issued, and the next one
```

**Scripts** (dry run unless `--yes`)
```bash
./e2e.py                                # plan BEDNET + MR-DN
./e2e.py --yes                          # create both, fully set up
./e2e.py --type MR-DN --yes             # one type only

./create_campaign.py --type BEDNET --yes                          # campaign only
./setup_attendance.py CMP-2026-10-07-011029 --yes                 # registers + mapping + payment on an existing campaign
./create_registers.py CMP-2026-10-07-011029 --yes                 # registers only
./map_staff.py CMP-2026-10-07-011029 --include-attendees --yes    # mapping only
./setup_payment.py CMP-2026-10-07-011029 --yes                    # payment only
```

> `pytest`, `pytest -m create` and `./e2e.py --yes` **create new campaigns and
> users** on the environment in `.env` (2 campaigns + 16 users for both
> types), and those cannot be deleted. To check existing campaigns without
> creating anything, use `pytest -m "not create"`.

## Running the tests

```bash
pytest -m preflight                  # read-only checks, seconds - run this first
pytest                               # the whole flow, once per campaign type
pytest -k MR-DN                      # one campaign type
pytest -m "not create"               # re-check the stored campaigns without creating new ones
pytest tests/test_40_payment.py      # one stage, against the campaigns test_10 stored
pytest -m "not writes"               # nothing that changes server state
```

| Marker | Selects |
|---|---|
| `preflight` | read-only checks of the account, MDMS masters, samples, billing/wage config |
| `create` | campaign creation (`test_10`) — creates a campaign and 8 users per type |
| `attendance` | registers and user mapping (`test_20`, `test_30`) |
| `payment` | billing cycle and wages (`test_40`) |
| `writes` | anything that changes server state |

**How the stages connect.** `test_10` creates a campaign per type and records it
(number, users, files) in `output/campaigns.json`; `test_20`–`test_40` load it
from there, so a later stage can be re-run on its own, in another session.
Every write stage is followed by a re-run check (`…_not_duplicated_on_rerun`,
`…_unchanged_on_rerun`) asserting a second run would change nothing.

**Output** (`output/`):

| File | Content |
|---|---|
| `test_results.json` | every test with outcome, duration, failure detail |
| `dashboard.html` | totals, the campaigns, every test — open it in a browser |
| `failed_requests/<test>.json` | the last request + response of each failed test |
| `campaigns.json`, `campaigns/<number>*` | stored campaigns; generated / filled templates, validation errors |

## Command-line scripts

Same code, no pytest. **Everything is a dry run unless `--yes`.**

```bash
./e2e.py                 # plan a BEDNET and an MR-DN campaign
./e2e.py --yes           # create both, each with registers, users and payment
./setup_attendance.py CMP-2026-10-07-011029          # existing campaign: plan registers + mapping (+ payment)
./setup_attendance.py CMP-2026-10-07-011029 --yes
```

### End to end — no campaign number

`e2e.py` creates the campaign itself, the way the admin console does, then runs
`setup_attendance.py` on it:

```bash
./e2e.py              # dry run: plans a BEDNET and an MR-DN campaign, all from .env
./e2e.py --yes        # creates both, each with registers, users and payment
./e2e.py --type MR-DN --yes
```

Without `--type` it creates one campaign per `HCM_CAMPAIGN_TYPES`
(BEDNET, MR-DN), like the console suite.

1. **Campaign** (`create_campaign.py`, ported from the `console` branch of
   `api_automation_project` — `utils/console.py`, `tests/console/test_campaign_e2e.py`):
   draft → boundaries → delivery rules → generate the unified template →
   fill it → upload → validate → attach → create → poll until `created`.
   Run it on its own to only create a campaign.
2. **Attendance + payment** — `setup_attendance.py <new number> --yes …`. Every
   option `e2e.py` does not know (`--level`, `--billing`, `--rates`,
   `--staff-only`, …) is passed through to it; `HCM_BILLING`/`HCM_RATES` in
   `.env` turn on the payment step.

The dry run can only plan step 1, since there is no campaign yet. Files
(generated, filled, validation errors) and a summary land in
`output/campaigns/<number>*`.

| Option / `.env` | Meaning |
|---|---|
| `--type` / `HCM_CAMPAIGN_TYPES` | BEDNET or MR-DN (matched to MDMS `Bednet`, `MR-DN`); without `--type`, every type listed |
| `--boundaries-from` / `HCM_BOUNDARIES_FROM` | copy an existing campaign's boundary selection, e.g. `CMP-2026-10-06-011022` |
| `--boundary` / `HCM_BOUNDARY` | instead: root → CODE with all its children; default the first root-to-leaf path |
| `--hierarchy` / `HCM_HIERARCHY_TYPE` | NIGERIA on qa; default the `--boundaries-from` campaign's |
| `--users` / `HCM_USER_ROLES` | one user per role (see below) |
| `HCM_USER_PHONE_START` | first phone number (default 9100000001) |
| `--sample` / `HCM_SAMPLE_TEMPLATE` | filled template to learn from; default `data/templates/<TYPE>_sample.xlsx` |
| `--start` / `--end` | `DD-MM-YYYY`; default tomorrow → +31 days |
| `--locale` / `HCM_LOCALE` | default the tenant's StateInfo language (`en_MZ` on qa) |
| `--name`, `--timeout` | campaign name (default `<TYPE>_e2e_<random>`), seconds per async step (900) |

**Users.** One active user per role in `HCM_USER_ROLES` — by default
DISTRIBUTOR, FIELD_SUPPORT, TEAM_SUPERVISOR, PAYMENT_EDITOR, PAYMENT_REVIEWER,
PAYMENT_APPROVER, PROXIMITY_SUPERVISOR, CAMPAIGN_SUPERVISOR (`PAYMENT_APPROVAL`
is accepted for PAYMENT_APPROVER) — at the campaign's target boundary, named
`<Role> <last 4 digits>`. For attendance that gives 2 attendees (DISTRIBUTOR,
FIELD_SUPPORT), an OWNER (TEAM_SUPERVISOR) and 2 APPROVERs; the dry run warns
if the list leaves a bucket empty.

Phone numbers are **sequential**: from `HCM_USER_PHONE_START`, continuing
across runs. `data/issued_users.json` keeps the next number and every number issued
(role, name, campaign, time). A number the user service already holds is
skipped, since validation rejects it. Raising `HCM_USER_PHONE_START` above the
stored next number jumps ahead; lowering it does not go back.

**The sample.** The generated template has boundary rows but blank data
columns. `utils/template_filler.py` learns facility usage and boundary targets from a
sample a human filled once, by `HCM_*` header code, so the console branch's
samples (MICROPLAN hierarchy) work on NIGERIA: Bednet and MR-DN use the same
target columns on qa. The sample's users are not copied. Active facilities whose
boundary the campaign does not cover are re-pointed to the target boundary;
otherwise excel-ingestion rejects them with `HCM_BOUNDARY_CODE_NOT_IN_CAMPAIGN`.

| Sample | Learnt from it |
|---|---|
| `data/templates/BEDNET_sample.xlsx` | facility usage Active; targets `HCM_ADMIN_CONSOLE_TARGET`, `…_BEDNET_COLUMN_2/3` |
| `data/templates/MR-DN_sample.xlsx` | facility usage Active; targets `…_SMC_AGE_3_TO_11`, `…_SMC_AGE_12_TO_59`, `…_SMC_COLUUM_HOUSEHOLD/PRODUCT` |

**Locale.** excel-ingestion localizes the template in the locale at the end
of `RequestInfo.msgId`. The console sends the campaign locale there; sending
`en_IN` on qa produced sheets named by code (`HCM_ADMIN_CONSOLE_USERS_LIST`),
so creation sends the campaign locale. The filler also matches sheets by header
codes, so an unlocalized template still fills.

**Role values** must match the generated template's dropdown, which on qa
(`en_MZ`) holds bare codes (`DISTRIBUTOR`), elsewhere display names
(`Distributor`); users are written in whichever form it offers. Names may only
hold letters, digits, spaces and hyphens.

**Generation races project-factory.** For unified campaigns project-factory
starts its own template generation ~2s after a boundary update, and each
`_init` expires the earlier ones. A generation that comes back `expired` is
restarted (up to 3 times).

**The draft is
persisted asynchronously**: updating it straight after `create` returns
`CAMPAIGN_NOT_FOUND`, so the script waits until search returns it.

**excel-ingestion differences from the console branch**, from the service
source: generation ends `generated` (older builds `completed`, both accepted),
and a validation with row errors still ends `completed`. The verdict is
`processedStatus` (`valid` / `invalid`), which is what `create_campaign.py`
checks.

### What `setup_attendance.py` does, in order

1. **Registers** — one per boundary where the campaign's **DISTRIBUTOR** users
   are mapped (`--level auto`, the default). Each boundary needs a project and
   gets a register only if it has none yet. Override with `--level 3`,
   `--level locality`, or `--level leaf`.
2. **User mapping** — users from the campaign's project staff (or
   `--users-xlsx <campaign>-Users.xlsx`), bucketed by role and matched through
   the boundary tree, the way the UI's `boundaryFilter` does:

   | Bucket | Roles | Becomes | Goes on |
   |---|---|---|---|
   | approver | PROXIMITY_SUPERVISOR, CAMPAIGN_SUPERVISOR | staff `APPROVER` | every register at or below the user's boundary |
   | marker | TEAM_SUPERVISOR, DISTRICT_SUPERVISOR | staff `OWNER` | the register at the user's boundary or nearest ancestor |
   | worker | DISTRIBUTOR, REGISTRAR, FIELD_SUPPORT, HEALTH_FACILITY_WORKER | attendee | same as marker |
   | — | anything else (PAYMENT_*) | not enrolled | — |

   A user with several roles lands in one bucket, priority APPROVER > MARKER >
   WORKER. A **worker can be on only one active register** campaign-wide
   (supervisor roles are exempt), so a worker matching several registers is
   held and reported — pick one with `map_staff.py --register-id … --users …`.
   Registers left without an `OWNER` are flagged: nobody could mark attendance.
3. **Payment setup** (with `--billing`) — billing cycle via
   `health-expense-calculator/billing-config` (the service generates the billing
   periods) and per-role wages in MDMS `HCM.WORKER_RATES`. `--rates F,T,P` sets
   food, travel, per-day for every wage role; `--rate SKILL=F,T,P` overrides one.
   Validated like the UI: the code must exist in MDMS `BillingCycle`, CUSTOM
   `--days` within its min/max (3–90 on qa), rates within the campaign type's
   `rateMaxLimitSchema` (food 50, travel 50, per-day 150 for POLIO).

### The individual scripts

| Script | Step |
|---|---|
| `e2e.py` | create a campaign, then everything below on it |
| `create_campaign.py` | campaign creation only (`--type`, `--boundaries-from`, `--users`) |
| `setup_attendance.py` | all three steps above, planned together |
| `create_registers.py` | registers only (`--level auto\|N\|TYPE\|leaf`, `--users-xlsx`) |
| `map_staff.py` | mapping only; staff by default, `--include-attendees` / `--attendees-only`, `--register-id`, `--users USR-1,USR-2`, `--users-xlsx` |
| `setup_payment.py` | payment setup only (`--billing`, `--days`, `--rates`, `--rate`) |

### Roles the account needs

| Step | Roles (unified-qa `ACCESSCONTROL-ROLEACTIONS`) |
|---|---|
| registers, staff, attendees (`health-attendance/*/v1/_create`) | DISTRICT_SUPERVISOR, PROVINCIAL_SUPERVISOR, NATIONAL_SUPERVISOR, SUPERUSER, SYSTEM_ADMINISTRATOR (attendees also PROXIMITY_SUPERVISOR) |
| campaign creation (`project-factory/v1/project-type/create` / `update`, excel-ingestion) | CAMPAIGN_MANAGER |
| payment setup (`billing-config/v1/_create` / `_update`, `v2/_create` / `_update/HCM.WORKER_RATES`) | CAMPAIGN_MANAGER |

CAMPAIGN_MANAGER alone gets **401** on the attendance APIs — the workbench UI
works for it only because project-factory makes those calls server-side. The
scripts check roles up front and name the missing one; dry runs still work.

### unified-qa specifics (verified 2026-10-06)

- **API paths.** Project, project-staff and individual search are tried at
  `/health-*` first. On qa the plain paths reach other services: `/project/v1`
  400s ("Any one project search field is required"), `/individual/v1` 401s, and
  `/project/staff/v1` answers **200 with zero rows** — which is why `/health-*`
  must go first rather than be a fallback. On demo `/health-*` 404s and the
  plain paths are used.
- **IDs.** Attendance stores the **Individual id** (= HRMS employee `uuid`) in
  `attendee.individualId` and `staff.userId`. The Users xlsx column
  `UserService Uuids` is the user-service uuid (`Individual.userUuid`) — it is
  resolved through individual search, never sent as-is.
- **Register search.** Searching by `campaignNumber` ignores
  `includeStaff`/`includeAttendee`; registers are re-read by `serviceCode` with
  `isServiceCodeExact=true` to see their staff and attendees.
- **Staff `additionalDetails`.** `{ownerName, staffName}` as the UI writes them,
  `ownerName` being the register's OWNER.
- **MDMS the flow depends on** — each was missing on qa and had to be seeded:
  `HCM-ADMIN-CONSOLE.excelIngestionGenerate` (`attendanceRegister`,
  `attendanceRegisterAttendee`, UI template downloads) and
  `HCM-BILLING-CONFIG-PAYMENT-SETUP.BillingCycle` (WEEKLY, BI_WEEKLY, MONTHLY,
  CUSTOM — without records the UI disables the billing-cycle dropdown and
  `setup_payment.py` stops with that message). qa's gateway has **no
  access-control action** for `v2/_create/…BillingCycle`, so that master can
  only be written directly to mdms-v2 (`kubectl port-forward svc/mdms-v2`).
- **Registers created by these scripts show in the workbench UI** like the
  ones uploaded through it.

---

---

## Reference

### `HCM_BASIC_AUTH`

`POST /user/oauth/token` needs a static Basic client credential on top of the
user credentials: base64 of the client id with an **empty secret**,
`egov-user-client:` → `ZWdvdi11c2VyLWNsaWVudDo=` (the default
`egov-user-client:egov-user-secret` is rejected with 401 `Bad credentials`).
Instead of a password, `HCM_AUTH_TOKEN` takes an access token from a browser
session; userInfo is then resolved via `/user/_details` (or `HCM_USER_INFO`).

### billing-config contract (verified via the server's own NotNull errors)

Required: `tenantId`, `campaignNumber`, `projectId`, `projectStartDate`,
`projectEndDate`, `billingFrequency`; `customFrequencyDays` when
`billingFrequency == CUSTOM` (the UI sends it as a string). `projectId` is the
campaign's root project, not the campaign `id`. `_search` returns
`billingConfig` as a single object, `null` when nothing matches.

### Not idempotent server-side

A second `_create` for the same (register, user, staffType) duplicates or
400s, so already-enrolled pairs are filtered out client-side from the register
search before anything is sent. The enrollment window is enforced here too: the
direct route never runs `AttendanceRegisterAttendeeValidationProcessor` (it only
fires on the xlsx route). See `docs/register-attendee-api-chain.md`.

### `legacy/`

`create_attendance.py` (registers + attendees in one pass) and
`setup_campaign.py` (inspect + billing config) predate the framework, were
built for demo, and keep their own client. Everything they do is covered by
`setup_attendance.py` / `setup_payment.py`.
