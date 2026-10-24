# TRACE — F1 telemetry, decoded.

TRACE is a historical Formula 1 telemetry comparison workspace. The project is being built in milestones, with real data and verified calculations required before a comparison is shown as successful.

## Current milestone

Milestone 7 verifies the dynamic workflow across multiple real sessions and adds optional explicit candidate-lap selection. Meeting/session and driver choices come from OpenF1 metadata; lap choices are loaded for the selected session and driver pair. See [`docs/data-validation.md`](docs/data-validation.md) for exact live and offline validation evidence and limitations.

## Stack

- Frontend: Next.js App Router, React, TypeScript.
- Backend: FastAPI, served by Uvicorn.
- Historical data: OpenF1 for the current API workflow; FastF1 remains as a separate diagnostic and historical-data adapter.

## Local setup

Prerequisites: Node.js 20.9 or newer and Python 3.10 or newer.

Install the frontend dependencies and start Next.js:

```powershell
cd frontend
npm install
npm run dev
```

In another PowerShell terminal, create a Python environment, install backend dependencies, and start the API:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

On this Windows environment, Application Control blocks Next.js's native SWC binary. To run a frontend build or dev server here, set Next's WASM fallback before the `npm` command:

```powershell
$env:NEXT_TEST_WASM = "1"
$env:NEXT_TEST_WASM_DIR = (Resolve-Path .\node_modules\@next\swc-wasm-nodejs).Path
npm run dev # or npm run build
```

This uses Next.js's internal WASM test hook and is only needed where the native compiler is blocked. Standard `npm run dev` and `npm run build` are preferred on normal developer machines.

The API health endpoint is `http://127.0.0.1:8000/api/health`. The frontend checks it on page load. Set `NEXT_PUBLIC_API_URL` if the API runs at a different local address.

The dashboard discovers available meetings and sessions from OpenF1 metadata. Choose a year, then an event/meeting and its session; driver choices are fetched for that session. Discovery endpoints are `GET /api/sessions?year=2024` and `GET /api/drivers?session_key=9554`. The current comparison API accepts stable session identifiers. Example:

```text
http://127.0.0.1:8000/api/compare?session_key=9554&driver_a=VER&driver_b=NOR
```

`/api/sessions` joins OpenF1 `/meetings` and `/sessions` by `meeting_key`, preserving the event-versus-session distinction and using provider session keys internally. `/api/drivers` supplies session participants. `/api/compare` validates both identifiers against the chosen session before retrieving candidates and telemetry. The previous `year`, `event`, and `session` query form remains available when both drivers are explicitly supplied; ambiguous matches now ask callers to use a stable session key. There are no fixed driver defaults. `GET /api/laps?session_key=9554&driver_a=VER&driver_b=NOR` returns timing-only lap options for the selected pair without fetching telemetry. Each driver may optionally select a lap independently with `lap_a` and `lap_b` on `/api/compare`; omitted parameters retain automatic selection. Manual options require a finite lap time, all three finite sector times, an explicit non-pit-out flag, a unique lap number, and a timezone-aware lap start timestamp for telemetry retrieval. Automatic selection applies the same finite timing checks. Other provider rows remain visible but disabled with the reason. This TRACE eligibility rule does not establish official validity or personal-best status. Options are fetched only for the active session and driver pair. The JSON comparison response contains session and driver metadata, candidate lap numbers/times/sectors, timestamped samples (`speed_kph`, throttle/brake percentages, gear, DRS status, RPM), sample-based speed summaries, an elapsed-time nearest-sample speed insight, lap-time delta, fetch time, and explicit warnings. `delta_seconds` uses `driver_b - driver_a`: a negative number means driver B's candidate time is faster. `/docs` provides the generated OpenAPI schema.

Candidate selection requires a finite lap duration, three finite sector times, and `is_pit_out_lap=false`; among qualifying records it selects the shortest time. OpenF1 does not establish official lap validity or personal-best status, so responses mark validity as `unknown`. The chart's x coordinate is elapsed time from the first returned timestamp in each trace; it is not circuit distance or position. Lines connect observed samples and are not resampled. The peak speed-gap insight pairs each speed-valid sample with the nearest speed-valid sample from the other trace within the common elapsed-time span, in both directions; pairs more than 0.15 seconds apart are excluded. It never interpolates or extrapolates and reports elapsed-time separation, not same-position comparison. A 429 response is returned as HTTP 429 with `Retry-After` when supplied; wait before trying again. The frontend does not substitute telemetry when the API fails.

To validate the real 2024 British GP qualifying feed, run this separately from ordinary tests:

```powershell
cd backend
.\.venv\Scripts\python.exe -m scripts.validate_fastf1
```

FastF1's cache is configured to `data/cache/` at the repository root and is ignored by Git. The validation script selects each driver's fastest accurate, personal-best lap, reports the available telemetry and data-quality checks, then computes `NOR time - VER time` (positive means VER's selected lap is faster). It retains the original car-data samples and does not align telemetry by row number or interpolate traces.

## Checks

```powershell
cd backend
.\.venv\Scripts\python -m pytest
cd ..\frontend
npm run lint
npm run typecheck
npm run build
```

## Data, cache, and provenance

OpenF1 is the current API source for historical sessions (the provider documents unauthenticated historical data from 2023 onward). The response reports OpenF1 and the local fetch time; OpenF1 source freshness metadata is not available. FastF1 remains separate and configures its cache under the repository's ignored `data/cache/` directory. TRACE discloses its data source and distinguishes unavailable data from measurements. No upstream data terms or redistribution rights have been assessed yet; review the provider terms before any public deployment.

## Known limitations

- Session and driver selectors are populated from provider metadata. Data availability varies by year/session; empty results and provider errors are shown without substituting the validated Silverstone session.
- Candidate validity and personal-best status remain unknown. The values should not be described as official fastest laps.
- The dashboard compares speed over timestamp-derived elapsed time only. Calibrated distance, circuit-position alignment, and distance-based telemetry are not supported.
- FastF1's 2024 session load previously resolved metadata but failed on upstream HTTP 401/404 timing requests; that access issue remains unresolved and FastF1 is not the API's current source.
- The API's real live end-to-end result and current frontend validation/build state are recorded in [`docs/data-validation.md`](docs/data-validation.md).
- Windows Application Control blocks Next.js's native compiler here; use the documented WASM fallback in this environment.
- `npm install` reported seven dependency advisories. `npm audit` could not retrieve advisory details because the registry security endpoint could not resolve, so severity and affected packages have not been independently confirmed.

Milestone 7 controlled live verification also retrieved VER's explicitly selected alternative lap 2 through the OpenF1 adapter and confirmed that its response retained manual selection mode and matching telemetry. Exact figures, request count, offline test coverage and the absence of browser interaction testing are recorded in [docs/data-validation.md](docs/data-validation.md).

## Milestone 8: plain-English comparison insights

The results now include a short “What this means” explanation built from the active comparison response. It names the recorded candidate lap with the shorter time using the existing `driver_b - driver_a` signed delta, lists both selected lap numbers/times and whether each was manually or automatically selected, and handles a tie or inconsistent/unavailable timing without guessing. It reiterates that official lap validity and personal-best status are unverified.

When the user inspects telemetry, TRACE describes the actual nearest sample in each driver's independently elapsed trace: recorded speed, throttle and brake readings, gear and engine RPM. Missing fields are stated as unavailable. These readings are observations only; throttle is not described as proof of acceleration.

The speed-gap wording reuses the existing largest nearby sample-pair calculation and reports both speeds, elapsed offsets, and separation. If no valid pair exists, it explains the unavailable state in plain language. It continues to label the comparison as elapsed-time based and not circuit-position aligned. No live provider request was needed for these presentation changes.
