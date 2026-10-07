# Register + attendee creation: the real API chain

Derived on **2026-09-15** from three sources, so each claim below says which:

| Source | What it gave |
|---|---|
| live pod logs on `unified-uat` (ns `health`) — `project-factory`, `excel-ingestion`, `health-attendance` | actual call order, types, the SQL, one live bug |
| MDMS `HCM-ADMIN-CONSOLE.excelIngestionProcess` (readable unauthenticated) | authoritative ingestion type names + sheet/processor map |
| `~/health-campaign-services` source | exact request models and required fields |
| browser capture on `health-demo` (2026-09-15, `CMP-2026-09-15-005352`) | the create-registers request body |

## 0. The environment moved

`hcm-demo.digit.org` **no longer resolves** (NXDOMAIN). The live host is
`health-demo.digit.org`, and the EKS cluster is named `hcm-demo-new`. The suite's
default `HCM_BASE_URL` is therefore stale — set it explicitly.

Log-reading contexts:

```bash
# unified-uat (reachable, read-only shell) - three services live in ns "health"
kubectl -n health logs -l app=excel-ingestion   --tail=2000
kubectl -n health logs -l app=project-factory   --tail=2000   # 4 replicas: grep all
kubectl -n health logs -l app=health-attendance --since=2h
```

## 1. The client never calls health-attendance or excel-ingestion directly

This is the single thing that was wrong in the old notes. The UI posts **one**
request to project-factory; project-factory does everything else server-to-server
as a system user.

```
browser
  └─ POST /project-factory/v1/resource-details/_create          ← the only client call
       {"ResourceDetails": {tenantId, campaignId, type, fileStoreId, filename}}
       │
       │  project-factory: persist row to health.eg_cm_resource_details (kafka
       │  create-resource-details), then triggerIfCampaignCreated(...)
       ▼
     POST /excel-ingestion/v1/data/process/_create              ← pf-correlation-* in logs
       {"RequestInfo":…, "ResourceDetails": {tenantId, type: "<base>-parse",
                                             hierarchyType, referenceId,
                                             referenceType, fileStoreId}}
       │  202, then async on excel-ingestion's el-async-N thread
       ▼
     kafka  ba-hcm-processing-result
       │
       ▼
     project-factory  attendanceRegister-processClass / attendanceRegisterAttendee-processClass
       │
       ├─ POST /health-attendance/v1/_search            (idempotency: existing serviceCodes)
       ├─ POST /health-attendance/v1/_create            (batched, registerApiBatchSize)
       ├─ POST /health-attendance/v1/_update            (existing registers)
       ├─ POST /health-attendance/attendee/v1/_create   (attendee phase)
       ├─ POST /health-attendance/attendee/v1/_delete
       └─ POST /health-attendance/staff/v1/_create|_delete
```

**So `health-attendance/v1/_create` returning `401 CustomException` for a
CAMPAIGN_MANAGER token is not a misconfiguration.** That endpoint is only ever
called machine-to-machine by project-factory. The README's "create registers /
map attendees are blocked" conclusion was a wrong diagnosis of a correct 401.

## 2. Ingestion type names (MDMS, authoritative)

`HCM-ADMIN-CONSOLE.excelIngestionProcess` has exactly 6 configs. The type sent to
excel-ingestion is suffixed per phase; the type sent to **project-factory** is the
bare name.

| project-factory `type` | template download (`generate/_init`) | validate (`process/_validation`) | parse (`process/_create`) |
|---|---|---|---|
| `attendanceRegister` | `attendanceRegister` | `attendanceRegister-validation` | `attendanceRegister-parse` |
| `attendanceRegisterAttendee` | `attendanceRegisterAttendee` | `attendanceRegisterAttendee-validation` | `attendanceRegisterAttendee-parse` |
| `user` / `facility` / `boundary` (microplan) | `unified-console` | `unified-console-validation` | `unified-console-parse` |

Only the `-parse` configs have a `processingResultTopic`
(`ba-hcm-processing-result` for attendance, `hcm-processing-result` for the
microplan flow); `-validation` has none — it writes errors back into the sheet
instead.

Sheets and processors:

| type | sheet | schema | processor |
|---|---|---|---|
| `attendanceRegister-*` | `HCM_ATTENDANCE_REGISTER_README` | `attendance-register-readme` | — |
| | `HCM_ATTENDANCE_REGISTER_LIST` | `attendance-register` | `AttendanceRegisterValidationProcessor` |
| | `HCM_ADMIN_CONSOLE_BOUNDARY_DATA` | `boundary-data` | — |
| `attendanceRegisterAttendee-*` | `HCM_REGISTER_WORKER_SHEET` | `attendance-register-attendee-worker` | `AttendanceRegisterAttendeeValidationProcessor` |
| | `HCM_REGISTER_MARKER_SHEET` | `attendance-register-attendee-marker` | same |
| | `HCM_REGISTER_APPROVER_SHEET` | `attendance-register-attendee-approver` | same |

Required sheet column, all three attendee sheets and the register sheet:
`HCM_ATTENDANCE_REGISTER_ID` (`isRequired: true`, 3–64 chars). Everything else on
those sheets is optional at schema level. The attendee sheets carry
`HCM_ADMIN_CONSOLE_USER_ROLE` as a multi-select of 1–5 roles from a 14-value enum
prefixed `ACCESSCONTROL_ROLES_ROLES_`.

## 3. Request bodies

### project-factory — `POST /v1/resource-details/_create` (client-facing, VERIFIED)

Server-side zod schema `resourceDetailsCreateSchema`:

| field | required | note |
|---|---|---|
| `tenantId` | yes | |
| `campaignId` | yes | campaign **UUID**, not `campaignNumber` |
| `type` | yes | ≤128 chars; `attendanceRegister` \| `attendanceRegisterAttendee` |
| `fileStoreId` | yes | filled template, uploaded first |
| `filename` | no | ≤256 chars |
| `parentResourceId` | no | ≤64 chars; **required in practice for attendees** — the register UUID |
| `additionalDetails` | no | free-form object |

Response is `{"ResourceDetails": {…, "id": …}}`. **200 means "upload accepted"**,
not "registers exist" — the work happens off Kafka afterwards. Poll
`/v1/resource-details/_search` with `{"ResourceDetailsCriteria": {tenantId,
campaignId, types[]}}` until the row's `status` is terminal. (`Pagination` is a
separate optional wrapper — this is the key the older notes were missing when six
probe variants all returned `invalid_type expected object received undefined`.)

Both uploads are the **same call**; only two fields differ:

| | registers | attendees |
|---|---|---|
| `type` | `attendanceRegister` | `attendanceRegisterAttendee` |
| `parentResourceId` | absent | the **attendance register UUID** (`register.id`) |

`parentResourceId` is the register's own UUID — the same value the UI carries in
`?registerId=` — **not** the resource-details row id of the register upload. Both
captured bodies are in `suite/payloads/{registers_create,map_attendees}.json`.

### excel-ingestion — internal, for reference

`ProcessResourceRequest` = `{RequestInfo, ResourceDetails}` where
`ResourceDetails` requires `tenantId`, `type`, `hierarchyType`, `referenceId`,
`referenceType`, `fileStoreId`. `referenceType` is a closed set of two:
`campaign` or `attendanceRegister` (`ProcessingConstants`). When it is
`attendanceRegister`, the campaign UUID moves to `additionalDetails.campaignId`.

`GenerateResourceRequest` = `{RequestInfo, GenerateResource}` with the same
required set minus `fileStoreId`; boundaries ride in
`additionalDetails.boundaries[]`.

## 4. Ordering is enforced — attendees depend on registers

`resourceTypeRegistry.ts` gives each type a phase and a dependency:

| type | phase | dependsOn | allowMultiplePerParent |
|---|---|---|---|
| `boundary` (projectCreation) | 1 | — | |
| `attendanceRegister` | 2 | projectCreation | false |
| `attendanceRegisterAttendee` | 3 | attendanceRegisterCreation | true |

Both are `sharedAcrossCampaignFamily: true` and `isRequired: false`. Uploading
attendees before registers exist cannot work — check phase 2 finished first.

## 5. Two gotchas that will cost time

**`project-type/search` hides attendee resources.** Observed SQL on
project-factory:

```sql
SELECT * FROM health.eg_cm_resource_details
 WHERE tenantid = $1 AND campaignid = $2 AND type != ALL($3) AND isactive = $4
-- $3 bound to attendanceRegisterAttendee
```

So the campaign object is not a place to check attendee upload status. Use
`resource-details/_search` directly.

**The attendee sheet's Register ID is a serviceCode, not a UUID.** From
`attendanceRegisterAttendeeValidation-processClass.ts`: the sheet stores
`serviceCode` in `HCM_ATTENDANCE_REGISTER_ID`, while the attendance APIs need the
internal register UUID — project-factory resolves one to the other. Any client
comparing the sheet value to `registerNumber` or `id` will mismatch.

## 5a. Enrollment date before campaign start — the expected rule is REJECT

Required behaviour: an enrollment date before the campaign start date must be
**rejected**, not adjusted.

### That rule is already enforced — but against the *register* window

`AttendanceRegisterAttendeeValidationProcessor.java` validates every row
(check 3, "enrollment date format and range"):

```java
} else if (registerContext != null && !isDateInRange(enrollmentDate, registerContext)) {
    rowErrors.add(getLocalizedMessage(localizationMap, LOC_DATE_OUT_OF_RANGE, …));
}

private boolean isDateInRange(LocalDate date, RegisterContext context) {
    return !date.isBefore(context.startDate) && !date.isAfter(context.endDate);
}
```

Out-of-range → row marked `INVALID` with
`HCM_ATTENDANCE_ATTENDEE_DATE_OUT_OF_RANGE` / *"Date must be between register
start and end dates"*. project-factory then **skips INVALID rows before any
attendance API call** (`attendanceRegisterAttendee-processClass.ts:112,138`), so
nothing is written for them.

This processor runs in **both** phases — the MDMS `attendanceRegisterAttendee-parse`
config lists it for all three sheets, with `parseEnabled: true`, while
`-validation` lists it with `parseEnabled: false`. So the rejection is not
skippable by going straight to `process/_create`.

**And the register window equals the campaign window**, because register creation
copies it (`attendanceRegister-processClass.ts:310`):

```ts
startDate: campaign?.startDate,
endDate:   campaign?.endDate
```

So today, "before register start" and "before campaign start" are the same date,
and the required behaviour holds.

### Where it actually breaks: the two windows can drift

The equality above is a *consequence of creation*, not an invariant — nothing
re-checks it. And §6 below is exactly the mechanism that breaks it: the
`AttendanceRegisterConsumer` that would push changed project/campaign dates onto
existing registers **throws on every message**. So after a campaign start-date
edit, registers keep their stale window and enrollment dates are validated against
the *old* campaign start. An enrollment date before the new campaign start is then
accepted.

That is the defect to file for this rule — not a missing validation, but a
validation keyed to a value that silently goes stale. `test_60` asserts the
equality directly (`test_register_window_matches_campaign_window`) so the drift
is caught rather than assumed.

### Second, weaker guard: the parse path *clamps* instead of rejecting

`collectStaffOperation` / `collectAttendeeOperation` also do this:

```ts
const clampedEnrollment = (enrollmentDateEpoch !== null && regStart != null)
    ? Math.max(enrollmentDateEpoch, regStart) : enrollmentDateEpoch;
```

Redundant while the validation above runs, and it disagrees with it: clamp vs
reject. `processorClass` is per-sheet MDMS config, so if the validation processor
is ever dropped from a sheet, this clamp silently accepts bad dates instead of
failing the row. Worth aligning to reject.

Also on enrollment dates: required to create staff
(`HCM_ATTENDANCE_ENROLLMENT_DATE_REQUIRED`) and immutable once set
(`HCM_ATTENDANCE_CANNOT_CHANGE_ENROLLMENT_DATE`, compared with `sameDateInTz`, so
timezone-shifted epochs for the same calendar day are tolerated).

### Project staff is a separate thing and never sees the enrollment date

`health-project/staff/v1/_create` is a different service and a different flow —
created only from `campaignMappingUtils.ts:145` → `startUserMapping()` during
campaign **user** mapping, with the campaign's own dates, unconditionally
(`utils/userMappingUtils.ts`):

```ts
const startDate = campaignDetails.startDate;
const endDate = campaignDetails.endDate;
…
const ProjectStaff = { tenantId, projectId, userId, startDate, endDate };
```

The attendee upload creates **attendance** staff only; it never touches project
staff. So project staff `startDate` is always campaign start, and the reject rule
above does not apply to it — there is no enrollment date on that path to reject.

Untested here: whether `health-project` itself rejects a ProjectStaff startDate
outside the project window. Worth one probe before filing anything.

## 6. Live bug found in the logs (unified-uat, 2026-09-15 12:28)

`health-attendance` `AttendanceRegisterConsumer` fails on every
`update-project-health` message:

```
Received message from topic: update-project-health
AttendanceRegisterConsumer: Attendance Register Consumer Started for project update.
AttendanceRegisterService: Fetching register from db for project : 2baa9976-982f-4e44-96c9-152af59637a3
ERROR AttendanceRegisterConsumer: Error in Attendance Register consumer update
org.egov.tracer.model.CustomException: Both referenceId AND localityCode are
  required together for project-based search.
  Received: referenceId=2baa9976-982f-4e44-96c9-152af59637a3, localityCode=null
    at AttendanceRegisterService.fetchAndFilterRegistersV1(AttendanceRegisterService.java:289)
    at AttendanceRegisterService.searchAttendanceRegister(AttendanceRegisterService.java:136)
    at AttendanceRegisterService.updateAttendanceRegister(AttendanceRegisterService.java:979)
    at AttendanceRegisterConsumer.projectUpdate(AttendanceRegisterConsumer.java:52)
```

The service calls its **own** search with only a projectId and trips its own
`referenceId` + `localityCode` validation — the exact rule the suite already
covers as a negative test (`test_reference_id_requires_locality_code`). Effect:
registers are never updated when a project changes. Internal caller, so it is
invisible from the API surface; only the log shows it.

## 7. Runbook — given a campaign number

1. `POST /project-factory/v1/project-type/search` → take `id` (UUID) **and**
   `projectId`; `campaignNumber` is not accepted by the write APIs.
2. `POST /health-attendance/v1/_search?tenantId=…&campaignNumber=…` → do
   registers already exist? (`totalCount`, `statusCount`)
3. `POST /project-factory/v1/resource-details/_search` with
   `types: ["attendanceRegister", "attendanceRegisterAttendee"]` → any upload
   in flight or failed?
4. Missing registers → download template (`generate/_init` → poll
   `generate/_search` → `filestore/v1/files/<id>`), fill
   `HCM_ATTENDANCE_REGISTER_LIST`, upload to filestore, then
   `resource-details/_create` with `type: attendanceRegister`.
5. Wait for phase 2 terminal, then repeat step 4 with
   `type: attendanceRegisterAttendee` and the three worker/marker/approver
   sheets.
6. Verify with step 2 + `attendee` counts, and tail the three pod logs above if
   a status sticks.
