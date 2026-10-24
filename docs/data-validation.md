# FastF1 data validation

## Target and method

Target: 2024 British Grand Prix, qualifying (`fastf1.get_session(2024, "British Grand Prix", "Q")`), drivers VER and NOR.

FastF1 3.8.3 was installed in `backend/.venv`. The cache helper resolves the checkout root from this module's location and configures the repository path `D:\TRACE\data\cache` (the ignored `data/cache/` directory), independent of the shell's working directory.

The standalone probe is `backend/scripts/validate_fastf1.py`; run it from `backend` with:

```powershell
.\.venv\Scripts\python.exe -m scripts.validate_fastf1
```

It is intentionally separate from pytest. On a successful load it chooses `Laps.pick_accurate()` (`IsAccurate=True`) followed by `Laps.pick_fastest()`. In FastF1 3.8.3, `pick_fastest()` defaults to a personal-best lap, so a lap not counted by timing is not silently substituted.

For telemetry it calls `Lap.get_car_data().add_distance()`. FastF1 describes `get_car_data()` as returning car data without computed telemetry channels; `add_distance()` computes distance in metres by integrating that lap's sample data. FastF1 documents speed in km/h. This avoids `get_telemetry()`'s car/position merge, which FastF1 says can contain interpolated values. The probe retains the returned car-data sampling, records the columns and missing values, and measures duplicate/non-increasing distances and sample spacing. It compares the two distance ranges and their sample-spacing statistics; it does not line up rows by index or interpolate.

Relevant FastF1 references: [session loading](https://docs.fastf1.dev/core.html#fastf1.core.Session.load), [accurate laps](https://docs.fastf1.dev/core.html#fastf1.core.Laps.pick_accurate), [fastest laps](https://docs.fastf1.dev/core.html#fastf1.core.Laps.pick_fastest), [car data](https://docs.fastf1.dev/core.html#fastf1.core.Lap.get_car_data), [distance calculation](https://docs.fastf1.dev/core.html#fastf1.core.Telemetry.add_distance), and [cache configuration](https://docs.fastf1.dev/fastf1.html#fastf1.Cache.enable_cache).

## Diagnosed upstream failure

**Status: real-data validation remains blocked.** The session resolved, but no timing or telemetry was retrieved, so no laps or driver comparison are verified.

The diagnostic mode makes one session-load attempt, temporarily disables FastF1's soft-exception swallowing, and records only GET URL paths, response status/reason, cache status, and sanitized exception summaries. It never records request/response headers, cookies, query strings, or bodies. Run from `backend`:

```powershell
.\.venv\Scripts\python.exe -m scripts.validate_fastf1 --diagnose-upstream
```

In the captured diagnostic attempt, FastF1 3.8.3 resolved the session. The schedule request returned 200 from cache. The first actual session feed was:

```text
https://livetiming.formula1.com/static/2024/2024-07-07_British_Grand_Prix/2024-07-06_Qualifying/SessionInfo.jsonStream
HTTP 401 Unauthorized (not from cache)
```

FastF1 then requested the same endpoint from its mirror:

```text
https://livetiming-mirror.fastf1.dev/static/2024/2024-07-07_British_Grand_Prix/2024-07-06_Qualifying/SessionInfo.jsonStream
HTTP 404 Not Found (not from cache)
```

After those responses, FastF1 raised `SessionNotAvailableError: No data for this session! If this session only finished recently, please try again in a few minutes.` This wrapper is FastF1's outcome after the primary and mirror yielded no usable session payload. The observed failure is an HTTP authorization response, not a DNS, TLS, timeout, or parsing failure. The generic successful HTTPS probes to GitHub and Jolpi do not test authorization to Formula 1's live-timing archive.

The installed FastF1 3.8.3 `fetch_page()` behavior was inspected: it requests the primary archive, tries the FastF1 mirror after a non-success response, and returns no page if neither response succeeds. Its normal soft-error logging omits the response status/detail; in debug mode the exception is re-raised. The trace instrumented the requests layer so the status is visible without enabling request-header logging. The cache was resolved and reported as `D:\TRACE\data\cache`; the failing primary and mirror requests were explicitly marked `from_cache: false`, so a stale cached response did not cause these 401/404 results.

**Upstream corroboration (not a test of this local run):** FastF1 maintainers describe the same 401 behavior in [issue #978](https://github.com/theOehrly/Fast-F1/issues/978), reporting that Formula 1 began requiring authentication for archive feeds around October 15, 2026. A maintainer states that an F1 TV Access subscription or higher is required, and notes authentication support in a FastF1 3.9.0 alpha. The FastF1 mirror does not have this 2024 archive path, consistent with the observed 404. This strongly supports an upstream archive access-policy change as the root cause, with the installed 3.8.3 lacking that newer authentication support. The locally observed status remains the primary evidence; the upstream issue is corroboration.

### Findings and remaining uncertainty

| Requested finding | Verified result |
|---|---|
| Session resolution | British Grand Prix — Qualifying resolved in FastF1 3.8.3. |
| Failed endpoint | Formula 1 `SessionInfo.jsonStream` archive URL above: HTTP 401 Unauthorized. |
| Fallback endpoint | FastF1 mirror for that session: HTTP 404 Not Found. |
| Exception | FastF1 `SessionNotAvailableError` after the HTTP responses; no timeout/DNS/TLS/parse exception was observed. |
| VER/NOR participation | Unverified: no lap table loaded. |
| Suitable laps, sector times, lap metadata, lap-time delta, faster driver | Unavailable; no calculation is reported. Planned delta convention remains `NOR seconds - VER seconds` (positive means VER faster). |
| Telemetry, columns, counts, values, units, missing/duplicate samples, distance coverage/sampling | Unavailable; telemetry was not retrieved. No claim of telemetry validation is made. |
| Cache | The resolved path is `D:\TRACE\data\cache`; the failing archive/mirror responses were not cache hits. |

The underlying HTTP 401 is clear. Remaining uncertainty is whether TRACE should use an officially authenticated F1 TV account and compatible FastF1 authentication flow, and whether that access is acceptable for this project. No credentials were supplied or tested, no paid access was obtained, and no authentication code or FastF1 upgrade was made. The exact current eligibility/terms and availability of an authorized credential are not independently verified here. No further live retries were made.

## Offline checks

`backend/tests/test_historical_data.py` covers telemetry missing/non-finite values, duplicate and non-monotonic distance samples, coverage comparison by physical distance ranges, and the lap-delta sign convention using small synthetic fixtures. These fixtures are unit-test inputs only and are not product telemetry. The live session probe is not run by pytest.

## Next validation action

The next action is a project decision about authorized archive access. If using an eligible F1 TV account is acceptable, determine the supported FastF1 authentication setup and compatible release, then rerun the diagnostic with credentials handled outside source control and logs. Otherwise, decide on an alternative data access approach in a later scoped milestone. Do not treat the comparison API milestone as validated until real laps and telemetry have been retrieved and inspected.

## OpenF1 historical-data feasibility investigation

Investigation date: 2026-10-23. This section records actual responses from OpenF1's unauthenticated historical API. No account, credentials, paid subscription, or live-only endpoint was used. OpenF1's current docs say historical data from 2023 onward is free and requires no authentication, while real-time data requires a paid subscription ([Introduction](https://openf1.org/docs/)). This supports the access expectation for the requested 2024 historical session; it is documentation evidence, not a guarantee of future availability.

### Endpoints and identification

The relevant documented endpoints are `/sessions` (metadata), `/drivers` (session driver identity), `/laps` (lap and sector timing), `/car_data` (about 3.7 Hz car channels), and `/location` (about 3.7 Hz approximate track coordinates). Documentation describes `speed` in km/h, throttle as a percentage, brake as pressed/not pressed (100/0), gear, RPM and DRS for car data. Lap records include approximate `date_start`, lap and sector durations in seconds, pit-out flag, speed traps, and mini-sector arrays. The lap schema does not document a FastF1-equivalent `IsAccurate` or personal-best flag. Location records contain UTC `date` and approximate `x`, `y`, `z` Cartesian position; the docs do not state coordinate units, and describe the origin as arbitrary and location as lacking lateral placement. See [car data](https://openf1.org/docs/#car-data), [drivers](https://openf1.org/docs/#drivers), [laps](https://openf1.org/docs/#laps), [location](https://openf1.org/docs/#location), and [sessions](https://openf1.org/docs/#sessions).

Requests were made with Python `requests`, a `(5, 20)` or `(5, 30)` second connect/read timeout, and no auth headers. Returned metadata—not a guessed key—identified exactly one match for year 2024, country United Kingdom, session name Qualifying:

| Endpoint | Status | Result |
|---|---:|---|
| `/v1/sessions?year=2024&country_name=United+Kingdom&session_name=Qualifying` | 200 | One record: session key `9554`, meeting key `1240`, Silverstone, date `2024-07-06`, qualifying, session metadata window `14:00–15:00 UTC`. |
| `/v1/drivers?session_key=9554` | 200 | 20 driver records. VER is Max Verstappen, number 1; NOR is Lando Norris, number 4. Both were present in this session's returned list. |
| `/v1/laps?session_key=9554` | 200 | 368 lap records: VER has 24 rows (22 with numeric lap durations); NOR has 22 rows (19 with numeric lap durations). |

The fastest timed record for each requested driver was selected only as a feasibility candidate after confirming numeric total duration, all three sector durations, and `is_pit_out_lap=false`. This is a transparent selection heuristic, not a claim that OpenF1 certifies the lap as accurate, legal, or officially counted. OpenF1's documented lap fields expose no deletion/accuracy/personal-best flag. Their sector sums match the total durations exactly for these two returned records.

| Driver | Number | Lap | Lap time | Sector 1 | Sector 2 | Sector 3 | `is_pit_out_lap` | Approximate `date_start` |
|---|---:|---:|---:|---:|---:|---:|---|---|
| VER | 1 | 23 | 86.203 s | 28.136 s | 34.587 s | 23.480 s | false | 2024-07-06 15:08:25.755 UTC |
| NOR | 4 | 19 | 86.030 s | 28.025 s | 34.567 s | 23.438 s | false | 2024-07-06 15:00:21.699 UTC |

**Independent lap-time calculation:** convention is `NOR seconds - VER seconds`; `86.030 - 86.203 = -0.173 s`. The negative result means NOR's candidate lap was 0.173 seconds faster. These are actual returned times, but the absence of a documented timing-validity/deletion flag remains a qualification on the comparison.

### Car and location sample findings

For each selected lap, the script retrieved each driver's `/car_data` and `/location` using a date window from the returned approximate lap start through `date_start + lap_duration`. The valid filtered requests returned HTTP 200. A timestamp discrepancy remains: the session metadata says `date_end=15:00:00 UTC`, while NOR's lap start is `15:00:21.699 UTC` and VER's is `15:08:25.755 UTC`. OpenF1 labels lap start as approximate but does not explain this offset. The lap rows and telemetry records share the returned session key and driver number, but exact attribution of those time-window samples to each lap should be independently checked before they are used as aligned lap traces.

| Driver | Car-data samples | Location samples | Car speed values | Location x/y present |
|---|---:|---:|---:|---:|
| VER | 325 | 330 | 325/325; 86–325 km/h | 330/330 |
| NOR | 320 | 329 | 320/320; 89–319 km/h | 329/329 |

Observed car-data columns for both: `date`, `session_key`, `n_gear`, `driver_number`, `meeting_key`, `drs`, `rpm`, `brake`, `speed`, `throttle`. This confirms real timestamped speed, throttle, brake, gear, RPM, and DRS data for the approximate lap windows. The API docs establish speed units as km/h; timestamps are UTC. The counts over roughly 86-second windows are consistent with the documented approximate 3.7 Hz car/location sampling. This investigation counted speed nulls (none) and coordinate x/y availability; it did not compute null counts for every remaining channel, duplicate timestamp counts, or detailed interval/gap distributions.

### Distance feasibility and limitations

OpenF1 does not return a `Distance` channel. Its `/location` x/y samples are approximate Cartesian positions and can support a possible cumulative polyline-length proxy (`sum(sqrt(dx² + dy²))`) per driver lap. However, the docs do not declare coordinate units; they warn location is approximate and lacks lateral placement. The two coordinate traces are sampled separately from car channels, and the lap start timestamp itself is explicitly approximate. Therefore the API responses establish that a coordinate-based distance proxy is possible to investigate, but they do **not** establish a defensible, calibrated metres-along-track coordinate or prove that per-distance speed traces can yet be compared accurately.

For a defensible follow-up, first validate the coordinate scale and a lap start/finish/direction convention against Silverstone geometry or a trusted lap-distance reference, inspect timestamp gaps/duplicates and coordinate noise, and quantify the effect of associating roughly 3.7 Hz location samples with similarly sampled car data. A later implementation could resample each lap independently onto a common distance axis only after establishing monotonic per-lap distance and documenting the interpolation error and missing-coverage policy. No interpolation or row-index matching was performed here; timestamps, not row numbers, were used to bound data retrieval.

### Request issue and evidence limits

The first four telemetry query attempts returned HTTP 404 because the filter was initially encoded incorrectly as an equality key such as `date>=` (which serialized as `date%3E%3D=value`). The docs' syntax is an operator suffix followed directly by its value (`date>value&date<value`); correcting the query shape produced HTTP 200 and the samples above. These 404s were caused by the malformed request shape and are not evidence that historical telemetry is unavailable. No repeated attempt was made after the corrected requests succeeded.

The successful HTTP responses and data presence establish **historical feasibility for the MVP's driver, lap-time, sector, and car-channel inputs**, without authentication in this investigation. They do not establish authoritative lap validity semantics or a validated distance coordinate. No production provider was changed and the FastF1 service remains intact. No offline test was added because this investigation introduced no reusable processing logic.

### Recommendation

Use OpenF1 as the leading MVP data-source candidate, subject to a next focused validation of lap-quality semantics and calibrated distance normalization. The minimum implementation step after approval would be a provider-neutral historical-data contract (session lookup, driver identity, laps, and raw timestamped channels) with the existing FastF1 adapter retained and an OpenF1 adapter added behind it. Keep distance-based chart claims blocked until coordinate calibration and sampling alignment are validated.

## Milestone 3A: provider-neutral contract and adapter

Added a provider-neutral record contract in `backend/app/models/historical.py` and `backend/app/services/historical_contract.py`. The typed records cover sessions, drivers, laps, car-data samples, location samples, provenance, and warnings. Provider dates remain raw strings; absent or unparseable measurements normalize to `None` with a warning where applicable. Original identifiers and raw provider rows are retained for diagnostics. `LapQuality.validity_status` and `personal_best` remain `None` unless a source actually supplies such indicators; location `coordinate_unit` remains `None` because OpenF1 does not document it.

`backend/app/services/openf1_historical.py` implements the structural `HistoricalDataProvider` contract using documented OpenF1 `/sessions`, `/drivers`, `/laps`, `/car_data`, and `/location` endpoints. All HTTP calls pass through one synchronous `httpx` client with a 5-second connect timeout and 20-second read/write/pool timeouts, explicit distinctions for HTTP status, request/transport failure, invalid JSON, and unexpected response shape, and no automatic retries in its initial 3A implementation. Milestone 3B adds conservative 2.1-second request-start pacing and at most one 429 retry, only when a usable `Retry-After` is supplied and no more than 60 seconds; an over-limit or repeated 429 is returned as an error with endpoint, status, retry metadata, and attempt count. OpenF1's time filters use the documented operator-suffix syntax (`date>` and `date<`). Existing FastF1 code in `historical_data.py`, its diagnostics, and the historical findings above remain unchanged and separate.

Lap choice is a pure function, `select_timed_non_pit_out_candidate`: it requires a numeric total, all three numeric sector durations, and an explicitly false pit-out flag, then selects the minimum duration. It is deliberately named a candidate policy and does not set official validity or personal-best status. `lap_delta_seconds` returns B minus A seconds and does not infer unavailable values.

An opt-in live probe is available from `backend`:

```powershell
.\.venv\Scripts\python.exe -m scripts.validate_openf1
```

By default it identifies the target session from returned metadata, then obtains driver/lap rows and the candidate lap car-data and location windows. The `--telemetry-only` mode reuses the previously verified session key and candidate lap metadata to avoid repeating those metadata requests. It requests only car-data windows. The command is not run by pytest and has no credentials or paid endpoint support.

### Offline test coverage and latest live probe

`backend/tests/test_openf1_historical.py` uses synthetic rows and `httpx.MockTransport` only. It covers normalization and raw timestamps, missing and invalid numeric values, unavailable validity/unit fields, candidate selection, the NOR-minus-VER delta sign, date-filter URL syntax, telemetry summary parsing, invalid JSON, request timeouts, explicit HTTP errors, pacing, Retry-After handling, one bounded retry, and stop behavior after a repeated 429. No test contacts OpenF1.

On 2026-10-24 I ran the opt-in live command once after implementing the adapter. It stopped with HTTP **429** at `/location` and did not retry. That run occurred before the date-filter regression test exposed that httpx had serialized the operator mapping as `date%3E=value`; its returned error therefore does not establish a successful correctly filtered location query or complete telemetry retrieval. The earlier endpoint calls completed far enough in the sequential command to reach that location request, but the command did not finish both drivers' complete telemetry report. The URL construction has since been corrected and the offline test verifies the correct operator-suffix form; no live request was repeated after the 429. Prior actual OpenF1 feasibility results above remain the evidence for the earlier correctly filtered historical session, driver, lap, car-data, and location results. Do not treat this later partial run as complete validation or as proof that location data is generally unavailable; the precise reason and duration for the 429 are unresolved. The prior run did not capture `Retry-After`; the adapter now reports that header if supplied.

The provider-neutral boundary and OpenF1 adapter are implemented, but official lap validity and a calibrated distance coordinate remain unresolved. The session/lap timestamp offset recorded above also remains a data-quality question. Keep the comparison API and speed-versus-distance claims out of scope until those items receive evidence-based handling.

## Milestone 3B: corrected adapter verification and API readiness

### Current OpenF1 limits and filter syntax

OpenF1's current public [homepage](https://openf1.org/) states that the free historical tier allows up to **3 REST requests per second and 30 per minute**; it lists real-time access under the paid tier. The current [API docs](https://openf1.org/docs/#time-based-filtering) show operator filters as part of the query expression, for example `date>timestamp&date<timestamp`. TRACE encodes the operator characters as `%3E`/`%3C` while keeping the operator attached to the value (`date%3Etimestamp`), which is equivalent after URL decoding and is covered by an offline URL assertion. A prior form with an equals after the operator (`date%3E=value`) was the wrong form.

### Earlier adapter invocation before pacing

On 2026-10-24, before rate-aware pacing and retry handling were implemented, I ran:

```powershell
.\.venv\Scripts\python.exe -m scripts.validate_openf1
```

The command stopped at the first candidate-lap `/car_data` request with:

```text
HTTP 429
endpoint: /car_data
Retry-After: 1
```

It made no retry. The command had already obtained responses for session lookup, drivers, and laps before reaching that first telemetry request. A 429 is consistent with request throttling and does not show that the endpoint or historical data is unavailable. No credentials, alternate IP, paid service, or circumvention was used.

### Final paced adapter validation

After adding pacing and a single bounded Retry-After-aware retry, I ran this once from `backend`:

```powershell
.\.venv\Scripts\python.exe -m scripts.validate_openf1 --telemetry-only
```

This mode waited 1.1 seconds for the prior `Retry-After: 1` cooldown and reused session key `9554`, VER #1 lap 23, and NOR #4 lap 19 from earlier real session/driver/lap responses. It avoided repeating `/sessions`, `/drivers`, and `/laps`. The adapter then retrieved car data for both known windows successfully (the command only proceeds to a `telemetry_loaded` report after successful HTTP responses and JSON normalization):

| Driver | Lap candidate | Car-data samples | Parsed timestamps | Timezone missing | Speed values | Speed range | Missing values across returned fields |
|---|---:|---:|---:|---:|---:|---:|---:|
| VER | 23 (86.203 s) | 325 | 325/325 | 0 | 325/325 | 86–325 km/h | 0 in each of 10 fields |
| NOR | 19 (86.030 s) | 320 | 320/320 | 0 | 320/320 | 89–319 km/h | 0 in each of 10 fields |

The actual returned columns were `brake`, `date`, `driver_number`, `drs`, `meeting_key`, `n_gear`, `rpm`, `session_key`, `speed`, and `throttle`. Each timestamp parsed as a timezone-aware ISO timestamp. Speed is in km/h per OpenF1's documentation. The adapter's successful result verifies the corrected telemetry query path for both candidate time windows. The lap start offsets against session `date_end` remain as previously documented, so the API should preserve raw timestamps and signal that candidate windows rely on approximate provider lap-start times.

The offline suite passed after these changes: **21 passed**. This includes mocked pacing and Retry-After tests; the live command itself is kept separate from pytest. The 429 retry did not need to occur in the successful final run; no extra request was made after success.

### Comparison readiness

- **Lap times:** The previously retrieved OpenF1 candidate values remain VER 86.203 s and NOR 86.030 s; `NOR - VER = -0.173 s`, so NOR's candidate is faster. This arithmetic is supported by the actual earlier response data.
- **Lap validity:** Still **unknown**. OpenF1's returned/documented fields do not establish that either candidate is officially valid, undeleted, or a personal best. Keep the normalized validity fields unset and label any returned laps as candidates.
- **Timestamp-based telemetry:** Verified live through the corrected adapter for both candidate windows, including actual fields, sample counts, timezone-aware timestamps, speed units, and missing-value counts above. Returning raw timestamps with approximate lap-window provenance is supportable; do not align by sample index.
- **Distance comparison:** Not supported yet. OpenF1 documents location x/y/z as approximate Cartesian coordinates and an arbitrary origin, but gives no coordinate units or calibration. It also says location lacks lateral placement. No complete raw traces were retained in the repository, and no track length, scale factor, row index, or unverified coordinate unit is used as distance. The exact minimum next experiment is one low-rate, correctly filtered location download for one selected lap after the indicated cool-down, preserving that raw response for analysis; then verify coordinate units/scale against an independent trusted Silverstone geometry or distance reference and inspect loop closure, gaps, and noise. If no independent scale is available, distance remains uncalibrated.

The full offline backend test suite passed after pacing/retry changes: **21 passed**. No production provider changed.

**Readiness decision: A — ready to implement an initial comparison API for lap times and timestamp-based telemetry.** Keep distance-based comparison explicitly deferred and lap validity unknown. The previously recorded candidate delta remains `NOR - VER = -0.173 s`, so NOR's candidate lap is faster; neither candidate is represented as an official personal best.

## Milestone 4: historical comparison workflow

### Implementation

`GET /api/compare` now accepts `year`, `event`, `session`, `driver_a`, and `driver_b`. The initial page configures the verified 2024 British Grand Prix qualifying session with VER and NOR. `event` is matched case-insensitively and exactly against OpenF1 session metadata's `location`, `circuit_short_name`, or `country_name` (the UI sends `Silverstone`). TRACE requests sessions for the selected year/session and resolves the returned key from that metadata; it does not hard-code session key 9554. It then requests the session's drivers once and laps once, chooses candidates in a separate policy function, and fetches the two selected car-data windows through the existing paced adapter. No location endpoint or distance calculation is used.

The JSON response is typed by Pydantic models in `backend/app/schemas/comparison.py`. It includes session/source provenance, local `fetched_at` (not source freshness), driver and candidate lap/sector data, timestamped telemetry, the explicit `driver_b - driver_a` delta convention, faster candidate, and warning text. Telemetry uses field names that state units: `speed_kph`, `throttle_percent`, and `brake_percent`; gear, DRS status, and RPM are also included. Provider freshness is `null`. OpenF1 API errors become explicit client errors; a final provider 429 remains HTTP 429 and forwards `Retry-After` when present. No stale or fabricated result is returned.

Candidate selection requires finite lap and all three sector times plus `is_pit_out_lap=false`, then takes the shortest candidate time. It is not an official validity or personal-best check. Each timestamp is parsed and normalized to UTC. `elapsed_seconds` is computed from the earliest returned car-data timestamp for that driver's trace. The chart connects observed samples without resampling and labels this axis; it does not imply that equal elapsed times correspond to equal circuit positions. No interpolation or row-index alignment is performed. Location units and calibrated distance remain unresolved.

### Controlled real API validation

On 2026-10-24 the new endpoint was exercised once via FastAPI `TestClient` with the real OpenF1 adapter and network. It returned HTTP 200 after session metadata, driver list, lap list, and two paced car-data queries. The session was dynamically resolved as key 9554, 2024, Silverstone, Qualifying. VER and NOR were present; the returned candidates and telemetry were:

| Driver | Candidate lap | Time | Sector 1 | Sector 2 | Sector 3 | Car-data samples | Timestamp span | Speed |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| VER | 23 | 86.203 s | 28.136 s | 34.587 s | 23.480 s | 325 | 85.881 s | 265 km/h first; 258 km/h last |
| NOR | 19 | 86.030 s | 28.025 s | 34.567 s | 23.438 s | 320 | 85.801 s | 267 km/h first; 261 km/h last |

The actual response timestamps were timezone-aware and parsed successfully. OpenF1's raw car-data fields used by the returned telemetry were `date`, `session_key`, `meeting_key`, `driver_number`, `speed`, `throttle`, `brake`, `n_gear`, `drs`, and `rpm`. The adapter's normalized telemetry fields included speed, throttle, brake, gear, DRS and RPM; the first/last returned samples contained values for all of these fields. The live response's delta was `NOR - VER = -0.17300000000000182 s` (floating-point representation of -0.173 s), so the NOR candidate was faster by 0.173 s. The response itself labels validity `unknown`, says distance is unavailable, and returns no provider freshness value. This is evidence that this specific adapter-to-endpoint-to-provider workflow retrieved usable historic timing and timestamped car data once; it does not resolve official lap validity, personal-best status, distance calibration, long-term upstream availability, or rate-limit behavior under concurrent use.

### Frontend and checks

The dashboard now offers the validated session and driver choices, actual API loading/error/rate-limit states, two candidate-lap summary cards, the delta, and an interactive hoverable speed-versus-elapsed-time SVG chart. Hover details show timestamp and available car channels. The idle, loading, error and success states do not insert mock data. The UI visibly qualifies candidate validity and distance limitations.

Checks run for Milestone 4:

- `backend\.venv\Scripts\python.exe -m pytest -q`: **26 passed** (includes the pre-existing health test, offline adapter tests, comparison service/schema tests, and mocked endpoint/rate-limit tests).
- `npm run typecheck`: passed.
- `npm run lint`: passed.
- `$env:NEXT_TEST_WASM='1'` and `$env:NEXT_TEST_WASM_DIR` set to the installed Next SWC WASM package, then `npm run build`: production build passed after correcting the CSS autoprefixer warning. The Windows compiler fallback still emits Next's experimental WASM notice.
- One live in-process HTTP endpoint run: **HTTP 200**; result summary above. The live call is separate from pytest.

The FastF1 adapter and its diagnostic scripts remain in place; the upstream access issue is unresolved. The implementation establishes a first working comparison slice, not distance-aligned telemetry or officially validated laps.

## Milestone 5: telemetry summaries and inspection

### Synchronized chart inspection

The speed chart retains the lime/blue traces and now selects a target elapsed-time coordinate from pointer hover/click or a keyboard/touch-operable range control. For each driver it displays that trace's nearest returned sample independently, including elapsed offset, raw UTC timestamp, speed in km/h, throttle percentage, brake state/value, gear, and RPM. Missing values display as unavailable. The UI reports how far apart the two sample offsets are; it does not call them temporally identical or same-position readings. Chart lines break at missing-speed records. Existing axis labels and the elapsed-time/distance caveat remain visible.

### Sample speed summaries

For each trace, minimum, maximum, and arithmetic mean speed are computed from finite non-null speed values in the API response. `valid_sample_count` states the number used. The cards call these **telemetry sample** summaries; they are not presented as official lap extrema. An empty valid set yields null min/max/mean and count zero.

### Largest nearby speed difference

The API computes `speed_difference_insight` from independently derived `elapsed_seconds` for the two returned traces. The shared range is the overlap of their elapsed-time extents. Within that range, every speed-valid sample in either trace is paired with its nearest speed-valid sample in the other; duplicate pairs are counted once. Pairs with elapsed-time separation above **0.150 seconds** are excluded. No interpolation, extrapolation, row-index matching, or circuit-position matching is done. The tolerance is a TRACE heuristic chosen below OpenF1's documented approximate 3.7 Hz sample interval; it is not a provider timing-accuracy guarantee. The result gives the maximum absolute speed difference, both actual speeds and elapsed offsets, both timestamps, the offset separation, and which sample is earlier/later **in elapsed-time coordinates**. When no shared interval or no pair within tolerance has usable speeds, the response marks the insight unavailable with a reason.

This is a nearest-sample **elapsed-time** comparison, not a same-position comparison: separate laps can be at different circuit locations at the same elapsed offset. It does not explain the lap-time difference causally or infer braking performance, skill, or sector advantage.

### Offline checks

Added `backend/tests/test_telemetry_insights.py` for unequal sample rates, non-identical timestamps and ordering, maximum-gap exclusion, missing speed values, no shared range, shared range without usable speeds, summaries with missing/empty values, and invalid tolerances. These fixtures are synthetic and do not contact OpenF1. Existing backend adapter and health tests remain unchanged. On 2026-10-24 the full backend suite passed: **34 passed** (one non-blocking Starlette/httpx TestClient deprecation warning). Frontend `npm run typecheck`, `npm run lint`, and the production `npm run build` with the documented Windows WASM environment all passed. No live OpenF1 request was made for Milestone 5; the prior Milestone 4 real-data result remains the last live verification.

Responsive CSS breakpoints were reviewed in source. A local browser viewport review at desktop/tablet/mobile widths could not be completed because the in-app browser origin approval for `127.0.0.1:3000` was declined; no alternate browser or network route was used. Actual rendered overflow/legibility at those widths remains visually unverified.

## Milestone 6: metadata-driven meeting, session and driver selection

### Provider fields and endpoints

The current [OpenF1 documentation](https://openf1.org/docs/) describes `/meetings` as Grand Prix/testing-weekend metadata, including stable `meeting_key`, meeting name, location, circuit short name, country and start date. `/sessions` exposes distinct track sessions, their stable `session_key`, `meeting_key`, session name/type, year and session start. `/drivers?session_key=...` returns participants and available acronym, full name and team fields. TRACE uses the documented `year` filters on meeting and session endpoints and joins events to sessions by the returned `meeting_key`; identical circuit labels therefore do not define identity. These docs were checked for this milestone; no live request was made here.

### Implemented behavior

- `GET /api/sessions?year=YYYY` obtains meetings and sessions from OpenF1, groups only provider-returned sessions beneath the corresponding meeting, and labels events with provider meeting name plus available location/circuit metadata. A meeting lacking its metadata row still appears from session-provided location/country information. Empty year results remain empty.
- `GET /api/drivers?session_key=...` fetches drivers for that exact session and returns typed, normalized selector options. Acronym is preferred as the identifier; if absent, the provider driver number is used. Missing names and team labels remain absent rather than being invented.
- `GET /api/compare?session_key=...&driver_a=...&driver_b=...` resolves the selected session by key, reloads driver metadata and validates both requested identifiers against the session before existing lap/telemetry processing. Same-driver comparisons remain rejected. The prior explicit `year/event/session/driver_a/driver_b` form is retained for compatibility; ambiguous event/session matches now direct callers to the key-based form. Driver defaults were removed so no session assumes VER/NOR.
- The frontend year control requests discovery for the entered year. Meeting/session changes clear downstream driver choices/results; drivers then load for the selected session. It chooses the first two participants returned for initial convenience, never a hard-coded driver pair. The comparison request sends a `session_key`; empty results and metadata errors are shown without fallback.

### Preserved limitations and verification

The candidate policy remains the fastest lap with finite total/three sector times and an explicit `is_pit_out_lap=false`; this still is not official validity or personal-best evidence. Delta remains driver B minus driver A. Timestamp-based charting and the elapsed-time speed-gap heuristic are unchanged. Distance alignment remains unavailable; no new validity, telemetry or distance claims are established by this selector work.

Mocked offline tests cover year-wide session discovery without a session-name filter, meeting normalization, joined selector response shape, same-circuit meetings and multiple sessions grouped by stable keys, driver identifiers including missing-acronym fallback, mismatched-session exclusion, stable-key comparison, unknown session and non-member driver errors, invalid years, and metadata rate-limit handling. These tests do not call OpenF1. On 2026-10-24, one controlled provider-backed check exercised the new FastAPI discovery endpoints through a shared adapter configured with automatic retries disabled. `GET /api/sessions?year=2024` returned HTTP 200 with 25 meetings and 123 sessions. Using returned metadata, it resolved the British Grand Prix meeting key 1240 and Qualifying session key 9554. Then `GET /api/drivers?session_key=9554` returned HTTP 200 with 20 drivers and included VER and NOR. The two metadata endpoints required three upstream calls total (`/meetings`, `/sessions`, `/drivers`) with the adapter's 2.1-second pacing; no retries occurred. This live check validates session/event discovery and participant listing only. It did not call the updated session-key comparison endpoint, retrieve laps or telemetry, or change prior caveats. On 2026-10-24 backend `python -m pytest -q` passed **40 tests** (one non-blocking Starlette/httpx deprecation warning). Frontend `npm run typecheck`, `npm run lint`, and `npm run build` passed using the documented Windows WASM fallback. Browser-rendered viewport review was not repeated.

## Milestone 7: cross-session verification and explicit candidate laps

### Workflow audit and live provider checks

The UI's year request feeds `/api/sessions`; meeting choices use returned meeting keys, session options use returned session keys, and `/api/drivers` is requested for the selected session. The comparison sends `session_key`, `driver_a`, and `driver_b`, then backend code resolves the session and checks both drivers against that session's returned driver metadata before loading its lap records. Candidate selection is applied per driver; selected lap start and duration define that driver's car-data query window. Timestamp strings are parsed to timezone-aware datetimes and normalized to UTC for the response. The UI checks response session/driver/lap identities before rendering.

On 2026-10-24, before adding manual lap selection, a controlled sequential live check used the real OpenF1 adapter with automatic 429 retries disabled. It made one year discovery request and two comparisons, with provider pacing and no parallel probes:

| Selected request | Returned session | Driver A candidate | Driver B candidate | Delta (B − A) | Telemetry |
|---|---|---|---|---:|---|
| 2024 British GP Qualifying, VER vs NOR | 9554, Silverstone | VER lap 23, 86.203 s; sectors 28.136 / 34.587 / 23.480 s | NOR lap 19, 86.030 s; sectors 28.025 / 34.567 / 23.438 s | −0.173 s; NOR faster | VER 325, NOR 320 samples |
| 2024 Bahrain GP Qualifying, VER vs LEC | 9468, Sakhir | VER lap 16, 89.179 s; sectors 28.535 / 38.269 / 22.375 s | LEC lap 12, 89.165 s; sectors 28.843 / 38.059 / 22.263 s | −0.014 s; LEC faster | VER 344, LEC 335 samples |

Both comparison calls returned HTTP 200 through the application adapter. Response session keys and driver identities matched the requested pair. All 645 samples in the Silverstone response and 679 in the Bahrain response had timezone-aware parsed timestamps; none of those samples lacked speed. Delta values above are computed from the returned lap times using `driver_b - driver_a`, so negative means B's candidate lap time is shorter. This confirms the dynamic session-key and different-driver pairing path using live data. It predates the manual-selection code, so no claim is made that a manually selected lap has been live-validated.

### Explicit lap selection

`GET /api/laps?session_key=...&driver_a=...&driver_b=...` returns session-bound timing options only; it does not request telemetry. Each record exposes lap number, total time, sector times, pit-out indicator, selectability, and an explanation when it cannot be manually selected. `/api/compare` accepts optional positive `lap_a` and `lap_b` values; omitted values keep the existing automatic policy. A chosen manual lap must match exactly one provider record and satisfy TRACE's comparable-record requirements: finite lap duration, all three finite sector durations, explicit `is_pit_out_lap=false`, and a timezone-aware parseable start timestamp. Duplicate/missing lap numbers, missing timing, unknown/pit-out status, or unusable start timestamp are not selectable. Candidate options are not official clean/valid-lap determinations.

The response `lap.selection_mode` is `automatic` or `manual`, with a policy explanation. The UI labels each result accordingly. Changing the session or either driver resets both lap choices to automatic; changing one lap clears the old comparison. Comparison requests are aborted on upstream selection changes, stale responses are ignored, and returned session, driver, and manually requested lap identifiers are checked before displaying a result. Failed requests clear the previous result rather than leaving stale data visible.

### Offline verification and limits

Backend tests use fake provider responses for the non-default session and driver pairing, endpoint identities, manual lap propagation and delta, timing-only option retrieval, pit-out/incomplete lap disabling, unavailable lap errors, and the existing comparison/telemetry calculations. These tests do not contact OpenF1. Frontend has no component test runner configured; the stale-request guard and selection resets were validated by source inspection and the TypeScript/lint/build checks below, not a browser-rendered interaction test.

The normal automatic policy remains the fastest returned lap with finite total and three sector times plus an explicit non-pit-out indicator. The manual choice uses those same evidence limits plus lap identity and a usable start timestamp. Neither policy establishes official lap validity or personal-best status. Telemetry windows continue to depend on OpenF1 `date_start`, documented as approximate. The chart remains elapsed-time based, and its samples are not circuit-position aligned. Distance comparison remains unavailable. FastF1's upstream access issue remains unresolved.

The cross-session checks above preceded manual selection. Manual selection and its telemetry retrieval are covered by the later controlled provider-backed run in this section.

### Manual-lap end-to-end provider verification

On 2026-10-24, I made one controlled live run through `create_comparison` with OpenF1 and a small in-process wrapper that cached `/sessions`, `/drivers`, and `/laps` after retrieving each once. The adapter was configured with its normal 2.1-second pacing and `max_429_retries=0`. It made seven sequential upstream requests total: three metadata/timing requests once, two car-data windows for the automatic comparison, then two car-data windows for the manual comparison. It did not retry and did not make a broad scan.

For the known automatic comparison, the actual response was session 9554, 2024 Silverstone Qualifying: VER driver 1 automatic lap 23 (86.203 s; sectors 28.136 / 34.587 / 23.480) and NOR driver 4 automatic lap 19 (86.030 s; sectors 28.025 / 34.567 / 23.438). `NOR - VER = -0.17300000000000182 s`; NOR's selected candidate was faster. The response contained 325 VER and 320 NOR samples; every timestamp was timezone-aware and parsed, and no speed values were missing.

Using the same retrieved provider lap list, the run identified VER lap 2 (97.518 s; sectors 31.362 / 39.005 / 27.151) as another selectable option. The second actual service response explicitly requested `lap_a=2` while leaving NOR automatic. It returned VER lap 2 with `selection_mode=manual` and NOR lap 19 with `selection_mode=automatic`; it did not substitute VER's lap 23. The manual response contained 374 VER and 320 NOR car-data samples, all with parsed timezone-aware timestamps and no missing speeds. Its actual `NOR - VER` delta was −11.488 s, so NOR's selected candidate was faster in this pair. These are real OpenF1 adapter/service responses, not mocks. As before, lap validity and personal-best status are unknown.

This provider-backed check verified session/driver/lap identity and telemetry selection through the backend service. It did not drive a browser or verify rendered React interactions. The dashboard's one-response rendering, control resets, abort guards and identity checks were inspected in code; frontend typecheck, lint and production build passed. There is no configured frontend component-test runner, so browser-level stale-request behavior remains unautomated.

### Additional hardening and final checks

The lap eligibility helper and the shared automatic candidate selector now reject non-finite total or sector timings even if a provider fixture/model is constructed without normal adapter normalization. Mocked backend tests additionally cover same lap numbers present only under a wrong session or driver, duplicate lap identifiers, non-finite timing fields, missing/unparseable/naive timestamps, empty option responses, provider errors, and retention of the existing automatic policy. All routine tests remain offline.

Final commands for this pass:

- From `backend`: `.\.venv\Scripts\python.exe -m pytest -q` — **56 passed**, one non-blocking Starlette/httpx deprecation warning.
- From `frontend`: `npm run lint` — passed; `npm run typecheck` — passed.
- From `frontend` on Windows, with `NEXT_TEST_WASM=1` and `NEXT_TEST_WASM_DIR` set to the installed `@next/swc-wasm-nodejs` directory: `npm run build` — passed. Next printed its Windows WASM experimental-option notice but completed compilation and static generation.

## Milestone 8: evidence-backed plain-English insights

### What the lap-time explanation uses

The “What this means” section reads the active `ComparisonResponse` only. It checks that both returned lap times and the returned delta are finite, verifies that the delta agrees with `driver_b lap time - driver_a lap time`, and checks the backend's `faster_driver` value before stating which candidate lap is shorter. A positive delta means driver A's recorded candidate lap is shorter; a negative delta means driver B's is shorter; zero is described as equal only when the response also identifies a tie. Incomplete or inconsistent values produce an unavailable explanation. The evidence line gives each driver's selected lap number, recorded time, automatic/manual mode, and the signed delta. Validity and personal-best status remain unverified.

### What telemetry explanations use

The sample inspector describes each selected nearest returned sample at its own elapsed-time offset. It reports only values present in the response: speed in km/h, throttle and brake readings, gear, and RPM. Missing values are called unavailable. Brake values 0% and 100% are described according to the provider's pedal encoding; other percentages remain readings. A brief note separates recorded controls from inferred acceleration.

The speed-gap explanation reuses `speed_difference_insight`, including its existing shared elapsed range, nearest speed-valid sample matching, and 0.150-second maximum separation. It reports the two measured speeds and elapsed offsets and explains their separation. Unavailable states are translated to short user-facing explanations. The text says the observations are not circuit-position aligned; it makes no causal claim about the lap-time delta.

Offline backend tests cover both delta signs, ties, missing lap data, equal speed samples, missing/non-finite speeds, no shared elapsed range, and the existing time-separation rules. The frontend has no component-test harness; its presentation logic was checked with TypeScript, lint, and production compilation. No live OpenF1 request was needed or made for this presentation-only milestone. No lap-validity or distance-alignment claim changed.
