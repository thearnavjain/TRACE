export type TelemetryPoint = {
  timestamp: string;
  elapsed_seconds: number;
  speed_kph: number | null;
  throttle_percent: number | null;
  brake_percent: number | null;
  gear: number | null;
  drs_status: number | null;
  rpm: number | null;
};

export type DriverComparison = {
  acronym: string;
  full_name: string | null;
  team_name: string | null;
  driver_number: number;
  lap: {
    lap_number: number;
    lap_time_seconds: number;
    sector_1_seconds: number;
    sector_2_seconds: number;
    sector_3_seconds: number;
    validity: "unknown";
    selection_mode: "automatic" | "manual";
    selection_policy: string;
  };
  telemetry: TelemetryPoint[];
  speed_summary: {
    minimum_kph: number | null;
    maximum_kph: number | null;
    mean_kph: number | null;
    valid_sample_count: number;
  };
};

export type Comparison = {
  session: {
    session_key: string | number;
    year: number;
    session_name: string;
    event: string;
    location: string | null;
    circuit_name: string | null;
    date_start: string | null;
  };
  driver_a: DriverComparison;
  driver_b: DriverComparison;
  lap_time_comparison: {
    convention: "driver_b_minus_driver_a";
    delta_seconds: number;
    faster_driver: string | "equal";
  };
  speed_difference_insight: {
    available: boolean;
    unavailable_reason: string | null;
    comparison_tolerance_seconds: number;
    shared_elapsed_range_seconds: [number, number] | null;
    driver_a_speed_kph: number | null;
    driver_b_speed_kph: number | null;
    driver_b_minus_driver_a_kph: number | null;
    absolute_difference_kph: number | null;
    driver_a_elapsed_seconds: number | null;
    driver_b_elapsed_seconds: number | null;
    time_separation_seconds: number | null;
    time_separation_basis: "elapsed_seconds";
    earlier_in_elapsed_time_driver: string | null;
    later_in_elapsed_time_driver: string | null;
    driver_a_timestamp: string | null;
    driver_b_timestamp: string | null;
  };
  provenance: {
    provider: "openf1";
    fetched_at: string;
    source_freshness: null;
  };
  warnings: string[];
};

export type SessionOption = {
  session_key: string | number;
  meeting_key: string | number | null;
  year: number;
  session_name: string;
  session_type: string | null;
  date_start: string | null;
};

export type MeetingOption = {
  meeting_key: string | number | null;
  meeting_name: string;
  meeting_official_name: string | null;
  country_name: string | null;
  location: string | null;
  circuit_name: string | null;
  date_start: string | null;
  sessions: SessionOption[];
};

export type SessionsResponse = { year: number; source: string; meetings: MeetingOption[] };
export type DriverOption = {
  identifier: string;
  driver_number: number | null;
  acronym: string | null;
  full_name: string | null;
  team_name: string | null;
};
export type DriversResponse = { session_key: string | number; source: string; drivers: DriverOption[] };
export type LapOption = {
  lap_number: number | null;
  lap_time_seconds: number | null;
  sector_1_seconds: number | null;
  sector_2_seconds: number | null;
  sector_3_seconds: number | null;
  is_pit_out_lap: boolean | null;
  selectable: boolean;
  unavailable_reason: string | null;
};
export type DriverLapOptions = {
  identifier: string;
  acronym: string | null;
  driver_number: number;
  automatic_candidate_lap_number: number | null;
  laps: LapOption[];
};
export type LapOptionsResponse = {
  session_key: string | number;
  selection_policy: string;
  validity_note: string;
  drivers: DriverLapOptions[];
};

export function formatLapTime(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${(seconds - minutes * 60).toFixed(3).padStart(6, "0")}`;
}
