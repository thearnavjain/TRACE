"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ApiStatus } from "@/components/api-status";
import { Comparison, DriverComparison, DriverOption, LapOptionsResponse, MeetingOption, SessionsResponse, TelemetryPoint, formatLapTime } from "@/lib/comparison";

type ApiFailure = { detail?: { code?: string; message?: string } };
type SpeedTelemetryPoint = TelemetryPoint & { speed_kph: number };

function driverLabel(driver: DriverOption): string {
  const identity = [driver.acronym, driver.full_name].filter(Boolean).join(" · ") || `Driver ${driver.driver_number ?? driver.identifier}`;
  return driver.team_name ? `${identity} — ${driver.team_name}` : identity;
}

function hasSpeed(point: TelemetryPoint): point is SpeedTelemetryPoint {
  return point.speed_kph !== null;
}

function explainLapTimeDifference(comparison: Comparison): { finding: string; evidence: string } {
  const { driver_a: a, driver_b: b, lap_time_comparison: timing } = comparison;
  const aTime = a.lap.lap_time_seconds;
  const bTime = b.lap.lap_time_seconds;
  const delta = timing.delta_seconds;
  const unavailable = { finding: "The lap-time comparison is unavailable.", evidence: "A reliable difference could not be confirmed from the returned timing fields." };
  if (![aTime, bTime, delta].every(Number.isFinite)) return unavailable;

  const calculated = bTime - aTime;
  if (Math.abs(calculated - delta) > 0.000001) return unavailable;
  const signed = `${delta > 0 ? "+" : delta < 0 ? "−" : ""}${Math.abs(delta).toFixed(3)} s`;
  const selection = (mode: "automatic" | "manual") => mode === "manual" ? "manually selected" : "automatically selected";
  const evidence = `${a.acronym} lap ${a.lap.lap_number} (${formatLapTime(aTime)}, ${selection(a.lap.selection_mode)}); ${b.acronym} lap ${b.lap.lap_number} (${formatLapTime(bTime)}, ${selection(b.lap.selection_mode)}). Signed difference (${b.acronym} − ${a.acronym}): ${signed}.`;

  if (delta === 0) {
    return timing.faster_driver === "equal"
      ? { finding: "The two recorded candidate laps have the same time.", evidence }
      : unavailable;
  }
  const faster = delta > 0 ? a : b;
  const other = delta > 0 ? b : a;
  if (timing.faster_driver !== faster.acronym) return unavailable;
  return {
    finding: `${faster.acronym}'s recorded candidate lap is ${Math.abs(delta).toFixed(3)} seconds shorter than ${other.acronym}'s.`,
    evidence,
  };
}

function explainSample(point: TelemetryPoint): string {
  const speed = point.speed_kph === null ? "speed unavailable" : `speed ${point.speed_kph.toFixed(0)} km/h`;
  const throttle = point.throttle_percent === null ? "throttle unavailable" : `throttle reading ${point.throttle_percent}%`;
  const brake = point.brake_percent === null
    ? "brake reading unavailable"
    : point.brake_percent === 0
      ? "brake reading 0% (not pressed)"
      : point.brake_percent === 100
        ? "brake reading 100% (pressed)"
        : `brake reading ${point.brake_percent}%`;
  const gear = point.gear === null ? "gear unavailable" : `gear ${point.gear}`;
  const rpm = point.rpm === null ? "engine speed unavailable" : `engine speed ${point.rpm.toLocaleString()} RPM`;
  return `At ${point.elapsed_seconds.toFixed(2)} seconds in this trace: ${speed}; ${throttle}; ${brake}; ${gear}; ${rpm}.`;
}

function explainUnavailableSpeedGap(reason: string | null): string {
  const normalized = reason?.toLowerCase() ?? "";
  if (normalized.includes("no shared elapsed-time range")) return "The traces do not overlap in elapsed time, so there is no speed comparison to show.";
  if (normalized.includes("no timestamped samples")) return "One or both traces have no usable timestamped samples.";
  if (normalized.includes("no speed-valid samples")) return "The overlapping part of the traces does not contain usable speed readings for both drivers.";
  if (normalized.includes("no speed-valid sample pairs")) return "No speed readings were close enough in elapsed time to compare.";
  return "A speed comparison could not be made from the available samples.";
}

function explainSpeedGap(comparison: Comparison): string | null {
  const insight = comparison.speed_difference_insight;
  const values = [insight.driver_a_speed_kph, insight.driver_b_speed_kph, insight.driver_a_elapsed_seconds,
    insight.driver_b_elapsed_seconds, insight.time_separation_seconds, insight.absolute_difference_kph];
  if (!insight.available || !values.every((value) => value !== null && Number.isFinite(value))) return null;
  const aSpeed = insight.driver_a_speed_kph!;
  const bSpeed = insight.driver_b_speed_kph!;
  const gap = insight.absolute_difference_kph!;
  const result = gap === 0
    ? "The closest compared samples recorded the same speed; no speed gap was observed."
    : bSpeed > aSpeed
      ? `The largest observed gap was ${gap.toFixed(0)} km/h: ${comparison.driver_b.acronym} recorded more than ${comparison.driver_a.acronym} at these samples.`
      : `The largest observed gap was ${gap.toFixed(0)} km/h: ${comparison.driver_a.acronym} recorded more than ${comparison.driver_b.acronym} at these samples.`;
  const separation = insight.time_separation_seconds! < 0.0005
    ? "Their elapsed-time offsets matched."
    : `Their elapsed-time offsets were ${insight.time_separation_seconds!.toFixed(3)} seconds apart.`;
  return `${result} ${comparison.driver_a.acronym}: ${aSpeed.toFixed(0)} km/h at ${insight.driver_a_elapsed_seconds!.toFixed(2)} s; ${comparison.driver_b.acronym}: ${bSpeed.toFixed(0)} km/h at ${insight.driver_b_elapsed_seconds!.toFixed(2)} s. ${separation}`;
}

function SpeedChart({ comparison }: { comparison: Comparison }) {
  const width = 1000;
  const height = 360;
  const margins = { top: 20, right: 28, bottom: 48, left: 58 };
  const chartWidth = width - margins.left - margins.right;
  const chartHeight = height - margins.top - margins.bottom;
  const driversToPlot = [comparison.driver_a, comparison.driver_b];
  const all = driversToPlot.flatMap((driver) => driver.telemetry);
  const speedPoints = all.filter(hasSpeed);
  const maxX = Math.max(1, ...all.map((point) => point.elapsed_seconds));
  const maxSpeed = Math.max(100, ...speedPoints.map((point) => point.speed_kph));
  const yMax = Math.ceil(maxSpeed / 50) * 50;
  const x = (value: number) => margins.left + (value / maxX) * chartWidth;
  const y = (value: number) => margins.top + chartHeight - (value / yMax) * chartHeight;
  const [targetElapsed, setTargetElapsed] = useState<number | null>(null);
  const ticks = Array.from({ length: 5 }, (_, index) => (yMax * index) / 4);
  const pointAt = (driver: DriverComparison, elapsed: number) => driver.telemetry.reduce<TelemetryPoint | null>(
    (nearest, point) => !nearest || Math.abs(point.elapsed_seconds - elapsed) < Math.abs(nearest.elapsed_seconds - elapsed) ? point : nearest,
    null,
  );
  const samples = targetElapsed === null ? null : driversToPlot.map((driver) => pointAt(driver, targetElapsed));
  const selectedX = targetElapsed === null ? null : x(targetElapsed);
  const setTargetFromPointer = (event: React.PointerEvent<SVGRectElement>) => {
    const bounds = event.currentTarget.ownerSVGElement?.getBoundingClientRect();
    if (!bounds) return;
    const chartX = ((event.clientX - bounds.left) / bounds.width) * width;
    setTargetElapsed(Math.max(0, Math.min(maxX, ((chartX - margins.left) / chartWidth) * maxX)));
  };
  const sampledOffset = samples?.[0] && samples[1]
    ? Math.abs(samples[0].elapsed_seconds - samples[1].elapsed_seconds)
    : null;

  return (
    <div className="chart-wrap">
      <svg className="speed-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Speed in kilometres per hour over elapsed time from the first returned telemetry sample">
        {ticks.map((tick) => <g key={tick}>
          <line x1={margins.left} x2={width - margins.right} y1={y(tick)} y2={y(tick)} className="chart-grid" />
          <text x={margins.left - 12} y={y(tick) + 4} className="chart-tick" textAnchor="end">{Math.round(tick)}</text>
        </g>)}
        {[0, maxX / 4, maxX / 2, (maxX * 3) / 4, maxX].map((tick) => <g key={tick}>
          <line x1={x(tick)} x2={x(tick)} y1={margins.top} y2={height - margins.bottom} className="chart-grid vertical" />
          <text x={x(tick)} y={height - margins.bottom + 22} className="chart-tick" textAnchor="middle">{tick.toFixed(1)}s</text>
        </g>)}
        {driversToPlot.map((driver, driverIndex) => {
          const color = driverIndex === 0 ? "#c6f36a" : "#8eb9ff";
          const segments: SpeedTelemetryPoint[][] = [];
          let activeSegment: SpeedTelemetryPoint[] = [];
          driver.telemetry.forEach((point) => {
            if (!hasSpeed(point)) {
              if (activeSegment.length) segments.push(activeSegment);
              activeSegment = [];
              return;
            }
            activeSegment.push(point);
          });
          if (activeSegment.length) segments.push(activeSegment);
          const selected = samples?.[driverIndex];
          return <g key={driver.acronym}>
            {segments.filter((segment) => segment.length > 1).map((segment, index) => <polyline
              key={`${driver.acronym}-segment-${index}`}
              points={segment.map((point) => `${x(point.elapsed_seconds)},${y(point.speed_kph)}`).join(" ")}
              fill="none" stroke={color} strokeWidth="2.5" strokeLinejoin="round" strokeLinecap="round"
            />)}
            {driver.telemetry.filter(hasSpeed).map((point, pointIndex) => <circle key={`${driver.acronym}-${pointIndex}`} cx={x(point.elapsed_seconds)} cy={y(point.speed_kph)} r="2.2" fill={color} opacity=".72" />)}
            {selected?.speed_kph !== null && selected && <circle cx={x(selected.elapsed_seconds)} cy={y(selected.speed_kph)} r="5" fill={color} stroke="#101211" strokeWidth="2" />}
          </g>;
        })}
        <text transform={`translate(17 ${margins.top + chartHeight / 2}) rotate(-90)`} className="chart-axis" textAnchor="middle">Speed (km/h)</text>
        <text x={margins.left + chartWidth / 2} y={height - 8} className="chart-axis" textAnchor="middle">Elapsed time from first returned sample</text>
        <rect x={margins.left} y={margins.top} width={chartWidth} height={chartHeight} fill="transparent" onPointerMove={setTargetFromPointer} onClick={setTargetFromPointer} />
        {selectedX !== null && <line x1={selectedX} x2={selectedX} y1={margins.top} y2={height - margins.bottom} className="hover-line" pointerEvents="none" />}
      </svg>
      <label className="scrubber-label" htmlFor="telemetry-scrubber">Inspect elapsed time <span>{targetElapsed === null ? "Move over chart or use slider" : `${targetElapsed.toFixed(2)} s`}</span></label>
      <input id="telemetry-scrubber" className="chart-scrubber" type="range" min="0" max={maxX} step="0.01" value={targetElapsed ?? 0} aria-label="Inspect nearest telemetry samples at elapsed time" onChange={(event) => setTargetElapsed(Number(event.target.value))} />
      {samples && <div className="sample-inspection" aria-live="polite">
        <div className="inspection-heading"><span className="field-label">NEAREST RETURNED SAMPLES</span>
          {sampledOffset !== null && <span>{sampledOffset < 0.0005 ? "Same elapsed time" : `${sampledOffset.toFixed(3)} s apart in elapsed time`}</span>}
        </div>
        <div className="sample-grid">{driversToPlot.map((driver, index) => {
          const point = samples[index];
          return <div className={`sample-card ${index === 0 ? "sample-lime" : "sample-blue"}`} key={driver.acronym}>
            <div className="sample-card-head"><strong>{driver.acronym}</strong><span>{point ? `${point.elapsed_seconds.toFixed(3)} s offset` : "No sample"}</span></div>
            {point ? <>
              <div className="sample-speed">{point.speed_kph === null ? "—" : `${point.speed_kph.toFixed(0)} km/h`}</div>
              <p className="sample-explanation">{explainSample(point)}</p>
              <time dateTime={point.timestamp}>{new Date(point.timestamp).toISOString().replace("T", " ").replace("Z", " UTC")}</time>
            </> : <p>No returned sample is available for this driver.</p>}
          </div>;
        })}</div>
        {sampledOffset !== null && <p className="sample-caveat">{sampledOffset < 0.0005 ? "The elapsed offsets match; that does not establish matching wall-clock timestamps or circuit position." : "These nearest samples are not at exactly the same elapsed time or circuit position."} Throttle readings alone do not show whether the car was accelerating.</p>}
      </div>}
    </div>
  );
}

function Metric({ driver, tone }: { driver: DriverComparison; tone: "lime" | "blue" }) {
  const summary = driver.speed_summary;
  const kph = (value: number | null) => value === null ? "—" : `${value.toFixed(0)} km/h`;
  return <article className={`driver-card ${tone}`}>
    <div className="driver-head"><span className="driver-code">{driver.acronym}</span><span className="driver-number">#{driver.driver_number}</span></div>
    <p className="driver-name">{driver.full_name ?? driver.acronym}</p>
    <p className="team-name">{driver.team_name ?? "Team unavailable"}</p>
    <div className="lap-metric"><span>{driver.lap.selection_mode === "manual" ? "Manual candidate" : "Automatic candidate"} · Lap {driver.lap.lap_number}</span><strong>{formatLapTime(driver.lap.lap_time_seconds)}</strong></div>
    <div className="sector-grid">
      {[driver.lap.sector_1_seconds, driver.lap.sector_2_seconds, driver.lap.sector_3_seconds].map((sector, index) => <div key={index}><span>S{index + 1}</span><b>{sector.toFixed(3)}<small>s</small></b></div>)}
    </div>
    <div className="speed-summary"><div className="summary-title"><span>TELEMETRY SPEED SUMMARY</span><small>{summary.valid_sample_count} valid samples</small></div><div className="summary-values"><div><span>Min sample</span><b>{kph(summary.minimum_kph)}</b></div><div><span>Mean sample</span><b>{summary.mean_kph === null ? "—" : `${summary.mean_kph.toFixed(1)} km/h`}</b></div><div><span>Max sample</span><b>{kph(summary.maximum_kph)}</b></div></div><small className="summary-note">Observed telemetry samples; not official lap extrema.</small></div>
    <span className="validity-badge">VALIDITY UNVERIFIED</span>
  </article>;
}

export function ComparisonWorkspace() {
  const [year, setYear] = useState("");
  const [meetings, setMeetings] = useState<MeetingOption[]>([]);
  const [meetingKey, setMeetingKey] = useState("");
  const [sessionKey, setSessionKey] = useState("");
  const [drivers, setDrivers] = useState<DriverOption[]>([]);
  const [driverA, setDriverA] = useState("");
  const [driverB, setDriverB] = useState("");
  const [lapA, setLapA] = useState("auto");
  const [lapB, setLapB] = useState("auto");
  const [metadataState, setMetadataState] = useState<"idle" | "loading" | "ready" | "empty" | "error">("idle");
  const [metadataError, setMetadataError] = useState("");
  const [driversState, setDriversState] = useState<"idle" | "loading" | "ready" | "empty" | "error">("idle");
  const [lapOptions, setLapOptions] = useState<LapOptionsResponse | null>(null);
  const [lapOptionsState, setLapOptionsState] = useState<"idle" | "loading" | "ready" | "empty" | "error">("idle");
  const [lapOptionsError, setLapOptionsError] = useState("");
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [state, setState] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [error, setError] = useState<{ message: string; code?: string; retry?: string } | null>(null);
  const compareAbort = useRef<AbortController | null>(null);
  const delta = comparison?.lap_time_comparison.delta_seconds;
  const base = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
  const selectedMeeting = meetings.find((meeting) => String(meeting.meeting_key ?? "unknown") === meetingKey);
  const availableSessions = selectedMeeting?.sessions ?? [];
  const selectedSession = availableSessions.find((session) => String(session.session_key) === sessionKey);
  const selectableB = drivers.filter((driver) => driver.identifier !== driverA);
  const optionsA = lapOptions?.drivers.find((driver) => driver.identifier === driverA);
  const optionsB = lapOptions?.drivers.find((driver) => driver.identifier === driverB);

  const clearComparison = useCallback(() => {
    compareAbort.current?.abort();
    compareAbort.current = null;
    setComparison(null);
    setError(null);
    setState("idle");
  }, []);

  useEffect(() => {
    let cancelled = false;
    setMetadataError("");
    setMeetings([]);
    setMeetingKey("");
    setSessionKey("");
    setDrivers([]);
    setDriverA("");
    setDriverB("");
    clearComparison();
    setLapA("auto");
    setLapB("auto");
    setLapOptions(null);
    if (!year) {
      setMetadataState("idle");
      return () => { cancelled = true; };
    }
    setMetadataState("loading");
    const controller = new AbortController();
    const timer = window.setTimeout(() => fetch(`${base}/api/sessions?year=${encodeURIComponent(year)}`, { cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        const body = await response.json() as SessionsResponse | ApiFailure;
        if (!response.ok) {
          const retry = response.headers.get("Retry-After");
          throw new Error(`${(body as ApiFailure).detail?.message ?? "Session metadata could not be loaded."}${retry ? ` Retry after ${retry} seconds.` : ""}`);
        }
        return body as SessionsResponse;
      })
      .then((body) => {
        if (cancelled) return;
        setMeetings(body.meetings);
        setMetadataState(body.meetings.length ? "ready" : "empty");
        if (body.meetings.length) {
          const first = body.meetings[0];
          setMeetingKey(String(first.meeting_key ?? "unknown"));
          setSessionKey(first.sessions[0] ? String(first.sessions[0].session_key) : "");
        }
      })
      .catch((cause: unknown) => {
        if (cancelled || controller.signal.aborted) return;
        setMetadataError(cause instanceof Error ? cause.message : "Session metadata could not be loaded.");
        setMetadataState("error");
      }), 450);
    return () => { cancelled = true; window.clearTimeout(timer); controller.abort(); };
  }, [base, year, clearComparison]);

  useEffect(() => {
    if (!sessionKey) {
      setDrivers([]);
      setDriverA("");
      setDriverB("");
      setDriversState("idle");
      return;
    }
    let cancelled = false;
    setMetadataError("");
    setDrivers([]);
    setDriverA("");
    setDriverB("");
    setLapA("auto");
    setLapB("auto");
    setLapOptions(null);
    setDriversState("loading");
    clearComparison();
    const controller = new AbortController();
    fetch(`${base}/api/drivers?session_key=${encodeURIComponent(sessionKey)}`, { cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        const body = await response.json() as { drivers: DriverOption[] } | ApiFailure;
        if (!response.ok) {
          const retry = response.headers.get("Retry-After");
          throw new Error(`${(body as ApiFailure).detail?.message ?? "Drivers could not be loaded."}${retry ? ` Retry after ${retry} seconds.` : ""}`);
        }
        return body as { drivers: DriverOption[] };
      })
      .then((body) => {
        if (cancelled || controller.signal.aborted) return;
        setDrivers(body.drivers);
        setDriversState(body.drivers.length ? "ready" : "empty");
        setDriverA(body.drivers[0]?.identifier ?? "");
        setDriverB(body.drivers.find((driver) => driver.identifier !== body.drivers[0]?.identifier)?.identifier ?? "");
      })
      .catch((cause: unknown) => {
        if (cancelled || controller.signal.aborted) return;
        setMetadataError(cause instanceof Error ? cause.message : "Drivers could not be loaded.");
        setDriversState("error");
      });
    return () => { cancelled = true; controller.abort(); };
  }, [base, sessionKey, clearComparison]);

  useEffect(() => {
    if (!sessionKey || !driverA || !driverB || driverA === driverB || driversState !== "ready") {
      setLapOptions(null);
      setLapOptionsState("idle");
      setLapOptionsError("");
      setLapA("auto");
      setLapB("auto");
      return;
    }
    let cancelled = false;
    const controller = new AbortController();
    setLapOptions(null);
    setLapOptionsState("loading");
    setLapOptionsError("");
    setLapA("auto");
    setLapB("auto");
    fetch(`${base}/api/laps?${new URLSearchParams({ session_key: sessionKey, driver_a: driverA, driver_b: driverB })}`, { cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        const body = await response.json() as LapOptionsResponse | ApiFailure;
        if (!response.ok) {
          const retry = response.headers.get("Retry-After");
          throw new Error(`${(body as ApiFailure).detail?.message ?? "Lap options could not be loaded."}${retry ? ` Retry after ${retry} seconds.` : ""}`);
        }
        return body as LapOptionsResponse;
      })
      .then((body) => {
        if (cancelled || controller.signal.aborted) return;
        const matchesCurrentSelection = String(body.session_key) === sessionKey
          && body.drivers.some((driver) => driver.identifier === driverA)
          && body.drivers.some((driver) => driver.identifier === driverB);
        if (!matchesCurrentSelection) throw new Error("Lap options did not match the current session and driver selection.");
        setLapOptions(body);
        setLapOptionsState(body.drivers.every((driver) => driver.laps.length === 0) ? "empty" : "ready");
      })
      .catch((cause: unknown) => {
        if (cancelled || controller.signal.aborted) return;
        setLapOptionsError(cause instanceof Error ? cause.message : "Lap options could not be loaded.");
        setLapOptionsState("error");
      });
    return () => { cancelled = true; controller.abort(); };
  }, [base, sessionKey, driverA, driverB, driversState]);

  async function runComparison(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setComparison(null);
    setError(null);
    setState("loading");
    const query = new URLSearchParams({ session_key: sessionKey, driver_a: driverA, driver_b: driverB });
    if (lapA !== "auto") query.set("lap_a", lapA);
    if (lapB !== "auto") query.set("lap_b", lapB);
    const controller = new AbortController();
    compareAbort.current?.abort();
    compareAbort.current = controller;
    try {
      const response = await fetch(`${base}/api/compare?${query.toString()}`, { cache: "no-store", signal: controller.signal });
      const body = await response.json() as Comparison | ApiFailure;
      if (controller.signal.aborted) return;
      if (!response.ok) {
        const failure = body as ApiFailure;
        setError({ message: failure.detail?.message ?? "The comparison could not be loaded.", code: failure.detail?.code, retry: response.headers.get("Retry-After") ?? undefined });
        setState("error");
        return;
      }
      const result = body as Comparison;
      if (controller.signal.aborted) return;
      const expectedDriverA = drivers.find((driver) => driver.identifier === driverA);
      const expectedDriverB = drivers.find((driver) => driver.identifier === driverB);
      if (String(result.session.session_key) !== sessionKey
        || result.driver_a.acronym.toUpperCase() !== driverA.toUpperCase()
        || result.driver_b.acronym.toUpperCase() !== driverB.toUpperCase()
        || (expectedDriverA?.driver_number != null && result.driver_a.driver_number !== expectedDriverA.driver_number)
        || (expectedDriverB?.driver_number != null && result.driver_b.driver_number !== expectedDriverB.driver_number)
        || (lapA !== "auto" && result.driver_a.lap.lap_number !== Number(lapA))
        || (lapB !== "auto" && result.driver_b.lap.lap_number !== Number(lapB))
        || result.driver_a.lap.selection_mode !== (lapA === "auto" ? "automatic" : "manual")
        || result.driver_b.lap.selection_mode !== (lapB === "auto" ? "automatic" : "manual")) {
        throw new Error("The API response did not match the current session and driver selection.");
      }
      setComparison(result);
      setState("ready");
    } catch (cause) {
      if (controller.signal.aborted) return;
      if (cause instanceof Error && cause.message.startsWith("The API response")) {
        setError({ message: cause.message });
        setState("error");
        return;
      }
      setError({ message: "TRACE could not reach its API. Start the backend and try again." });
      setState("error");
    } finally {
      if (compareAbort.current === controller) compareAbort.current = null;
    }
  }

  function lapOptionText(lap: NonNullable<typeof optionsA>["laps"][number], index: number): string {
    const number = lap.lap_number === null ? `Unknown lap ${index + 1}` : `Lap ${lap.lap_number}`;
    const time = lap.lap_time_seconds === null ? "time unavailable" : formatLapTime(lap.lap_time_seconds);
    return `${number} · ${time}${lap.selectable ? "" : ` · unavailable: ${lap.unavailable_reason ?? "incomplete timing metadata"}`}`;
  }

  function automaticLapLabel(options: typeof optionsA): string {
    const lapNumber = options?.automatic_candidate_lap_number;
    if (lapNumber == null) return "Automatic · no candidate returned";
    const lap = options?.laps.find((item) => item.lap_number === lapNumber);
    const time = lap?.lap_time_seconds == null ? "time unavailable" : formatLapTime(lap.lap_time_seconds);
    return `Automatic · Lap ${lapNumber} · ${time}`;
  }

  const lapTimeMeaning = comparison ? explainLapTimeDifference(comparison) : null;
  const speedGapMeaning = comparison ? explainSpeedGap(comparison) : null;

  return <main className="app-shell">
    <header className="topbar">
      <Link className="wordmark" href="/" aria-label="TRACE home">TRACE<span>.</span></Link>
      <div className="brand-subtitle">Telemetry &amp; Race Analysis for Competitive Engineering</div>
      <div className="topbar-right"><span className="session-tag">OPENF1 · HISTORICAL</span><ApiStatus /></div>
    </header>

    <section className="page-intro">
      <div className="eyebrow"><span className="signal" /> PERFORMANCE STUDY <span className="eyebrow-index">HISTORICAL SESSION / DRIVER COMPARISON</span></div>
      <h1>One lap.<br /><span>Two perspectives.</span></h1>
      <p>Compare recorded lap times and car telemetry from a single historical session. Every value shown is returned by the data source.</p>
    </section>

    <section className="workspace" aria-label="Historical lap comparison">
      <form className="selection-bar" onSubmit={runComparison}>
        <label className="session-choice"><span className="field-label">YEAR</span><input aria-label="Historical season year" type="number" min="2023" max={new Date().getFullYear()} value={year} onChange={(event) => { setYear(event.target.value); clearComparison(); }} /></label>
        <label className="session-choice event-choice"><span className="field-label">EVENT / MEETING</span><select value={meetingKey} disabled={metadataState !== "ready"} onChange={(event) => { const next = meetings.find((meeting) => String(meeting.meeting_key ?? "unknown") === event.target.value); setMeetingKey(event.target.value); setSessionKey(next?.sessions[0] ? String(next.sessions[0].session_key) : ""); clearComparison(); }}><option value="">{metadataState === "loading" ? "Loading events…" : "Select event"}</option>{meetings.map((meeting) => <option key={String(meeting.meeting_key)} value={String(meeting.meeting_key ?? "unknown")}>{meeting.meeting_name}{meeting.location ? ` · ${meeting.location}` : ""}</option>)}</select><small>{selectedMeeting ? (selectedMeeting.circuit_name ?? selectedMeeting.country_name ?? "Meeting details unavailable") : metadataState === "empty" ? "No sessions returned for this year" : "Provider meeting metadata"}</small></label>
        <label className="session-choice"><span className="field-label">SESSION</span><select value={sessionKey} disabled={!availableSessions.length} onChange={(event) => { setSessionKey(event.target.value); clearComparison(); }}><option value="">Select session</option>{availableSessions.map((session) => <option key={String(session.session_key)} value={String(session.session_key)}>{session.session_name} · key {session.session_key}</option>)}</select><small>{selectedSession?.date_start ? new Date(selectedSession.date_start).toLocaleDateString() : "Provider session metadata"}</small></label>
        <label className="driver-select"><span className="field-label">DRIVER A</span><select value={driverA} disabled={driversState !== "ready"} onChange={(event) => { setDriverA(event.target.value); if (driverB === event.target.value) setDriverB(drivers.find((driver) => driver.identifier !== event.target.value)?.identifier ?? ""); setLapA("auto"); clearComparison(); }}><option value="">{driversState === "loading" ? "Loading drivers…" : "Select driver"}</option>{drivers.map((driver) => <option key={driver.identifier} value={driver.identifier}>{driverLabel(driver)}</option>)}</select></label>
        <label className="session-choice lap-choice"><span className="field-label">LAP A</span><select value={lapA} disabled={lapOptionsState !== "ready" || !optionsA} onChange={(event) => { setLapA(event.target.value); clearComparison(); }}><option value="auto">{automaticLapLabel(optionsA)}</option>{optionsA?.laps.map((lap, index) => <option key={`${lap.lap_number ?? "unknown"}-${index}`} value={lap.lap_number === null ? `unknown-${index}` : String(lap.lap_number)} disabled={!lap.selectable}>{lapOptionText(lap, index)}</option>)}</select></label>
        <span className="versus">VS</span>
        <label className="driver-select"><span className="field-label">DRIVER B</span><select value={driverB} disabled={driversState !== "ready" || selectableB.length === 0} onChange={(event) => { setDriverB(event.target.value); setLapB("auto"); clearComparison(); }}><option value="">{driversState === "loading" ? "Loading drivers…" : "Select driver"}</option>{selectableB.map((driver) => <option key={driver.identifier} value={driver.identifier}>{driverLabel(driver)}</option>)}</select></label>
        <label className="session-choice lap-choice"><span className="field-label">LAP B</span><select value={lapB} disabled={lapOptionsState !== "ready" || !optionsB} onChange={(event) => { setLapB(event.target.value); clearComparison(); }}><option value="auto">{automaticLapLabel(optionsB)}</option>{optionsB?.laps.map((lap, index) => <option key={`${lap.lap_number ?? "unknown"}-${index}`} value={lap.lap_number === null ? `unknown-${index}` : String(lap.lap_number)} disabled={!lap.selectable}>{lapOptionText(lap, index)}</option>)}</select></label>
        <button className="compare-button" type="submit" disabled={state === "loading" || lapOptionsState === "loading" || lapOptionsState === "idle" || !sessionKey || !driverA || !driverB || driverA === driverB}>{state === "loading" ? <><span className="spinner" /> Loading</> : "Run comparison"}<span aria-hidden="true">↗</span></button>
      </form>

      {(metadataState === "idle" || metadataState === "empty" || metadataState === "error" || driversState === "empty" || driversState === "error" || lapOptionsState === "empty" || lapOptionsState === "error") && <div className="metadata-notice" role="status"><span className="field-label">{metadataState === "error" || driversState === "error" || lapOptionsState === "error" ? "METADATA UNAVAILABLE" : metadataState === "idle" ? "SELECT A HISTORICAL YEAR" : "NO SELECTABLE DATA"}</span><p>{lapOptionsError || metadataError || (driversState === "empty" ? "OpenF1 returned no drivers for this session." : lapOptionsState === "empty" ? "No lap records were returned for these drivers. Automatic comparison may not be available." : metadataState === "idle" ? "Enter a year from 2023 onward to load meetings and sessions returned by OpenF1." : `OpenF1 returned no meetings or sessions for ${year}. Choose another year.`)}</p></div>}

      {state === "idle" && <div className="empty-state"><div className="empty-orbit"><span>01</span></div><h2>Ready when you are.</h2><p>Run the comparison to retrieve real lap and telemetry data from OpenF1.</p></div>}
      {state === "loading" && <div className="loading-state" role="status"><span className="spinner large" /><div><h2>Retrieving session data</h2><p>OpenF1 requests are paced conservatively. This may take a few seconds.</p></div></div>}
      {state === "error" && error && <div className="error-state" role="alert"><span className="error-icon">!</span><div><span className="field-label">{error.code === "provider_rate_limited" ? "RATE LIMITED" : "COMPARISON UNAVAILABLE"}</span><h2>{error.message}</h2>{error.retry && <p>Provider requested a cooldown of {error.retry} seconds. Wait before retrying.</p>}<button className="text-button" onClick={(event) => { event.preventDefault(); document.querySelector<HTMLFormElement>(".selection-bar")?.requestSubmit(); }}>Try again <span>↗</span></button></div></div>}

      {state === "ready" && comparison && <div className="results">
        <div className="result-heading"><div><span className="field-label">SESSION {comparison.session.session_key}</span><h2>{comparison.session.year} {comparison.session.event}</h2><p>{comparison.session.circuit_name ?? comparison.session.location} · {comparison.session.session_name}</p></div><div className="delta-card"><span className="field-label">{comparison.driver_b.acronym} − {comparison.driver_a.acronym}</span><strong className={delta !== undefined && delta < 0 ? "positive" : ""}>{delta !== undefined && delta > 0 ? "+" : ""}{delta?.toFixed(3)}<small>s</small></strong><span>{comparison.lap_time_comparison.faster_driver === "equal" ? "Tied candidate laps" : `${comparison.lap_time_comparison.faster_driver} candidate faster`}</span></div></div>
        <div className="driver-grid"><Metric driver={comparison.driver_a} tone="lime" /><Metric driver={comparison.driver_b} tone="blue" /></div>
        <section className="insight-panel" aria-labelledby="what-this-means-title">
          <span className="insight-mark" aria-hidden="true">↳</span>
          <div className="insight-content">
            <span className="field-label" id="what-this-means-title">WHAT THIS MEANS</span>
            <p className="insight-lead"><strong>{lapTimeMeaning?.finding}</strong></p>
            <p className="insight-evidence">{lapTimeMeaning?.evidence}</p>
            <small>These are recorded candidate laps; official validity and personal-best status are unverified.</small>
            <div className="plain-speed-insight">
              <span className="field-label">SPEED READINGS AT NEARBY TIMES</span>
              <p>{speedGapMeaning ?? explainUnavailableSpeedGap(comparison.speed_difference_insight.unavailable_reason)}</p>
              <small>Elapsed-time comparison only. The traces are not aligned by circuit position.</small>
            </div>
          </div>
        </section>
        <section className="chart-panel"><div className="panel-heading"><div><span className="field-label">TELEMETRY OVERLAY</span><h2>Speed trace</h2></div><span className="unit-label">KM/H</span></div><div className="chart-legend"><span><i className="legend-lime" />{comparison.driver_a.acronym} <small>{comparison.driver_a.telemetry.length} samples</small></span><span><i className="legend-blue" />{comparison.driver_b.acronym} <small>{comparison.driver_b.telemetry.length} samples</small></span></div><SpeedChart comparison={comparison} /><p className="chart-note">Horizontal axis is elapsed time from the first returned sample in each trace. Equal elapsed times do not indicate equal track positions.</p></section>
        <div className="disclosure"><span>DATA QUALIFICATION</span><p>These are selected candidate laps under TRACE’s documented timed, complete-sector, non-pit-out policy. OpenF1 does not establish official lap validity or personal-best status. Distance comparison is unavailable.</p><small>Retrieved {new Date(comparison.provenance.fetched_at).toLocaleString()} · Source freshness is not provided by OpenF1.</small></div>
      </div>}
    </section>
    <footer className="app-footer"><span>TRACE / HISTORICAL ANALYSIS</span><span>MEASURED BY DATA. QUALIFIED BY EVIDENCE.</span></footer>
  </main>;
}
