"""Live validation of 2024 British GP qualifying data for VER and NOR.

Run from ``backend`` with ``.venv/Scripts/python -m scripts.validate_fastf1``.
This script performs a real FastF1 load and is deliberately not part of pytest.
"""

from __future__ import annotations

import json
import argparse
import re
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import pandas as pd
import fastf1

# This standalone entry point is also safe when invoked outside the repository cwd.
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.historical_data import (  # noqa: E402
    compare_distance_sampling,
    configure_fastf1_cache,
    lap_time_delta,
    load_session,
    repository_root,
    select_fastest_accurate_lap,
)


def sanitize_url(url: str) -> str:
    """Keep the endpoint path while dropping credentials, query, and fragment."""
    parsed = urlsplit(str(url))
    host = parsed.hostname or "<unknown-host>"
    if parsed.port:
        host = f"{host}:{parsed.port}"
    path = re.sub(
        r"(?i)(token|access_token|auth|api[_-]?key|password|secret|cookie)(=|/|:)[^/&?#]+",
        r"\1\2[REDACTED]",
        parsed.path,
    )
    return urlunsplit((parsed.scheme, host, path, "", ""))


def sanitize_message(message: str) -> str:
    """Remove query strings and common credential fields from exception text."""
    message = re.sub(
        r"(?i)(authorization|proxy-authorization|cookie|set-cookie)(\s*[:=]\s*)[^\r\n]+",
        r"\1\2[REDACTED]",
        message,
    )
    message = re.sub(
        r"(?i)(token|api[_-]?key|password|secret)(\s*[:=]\s*)[^\s,;]+",
        r"\1\2[REDACTED]",
        message,
    )
    return re.sub(
        r"https?://[^\s\"'<>]+",
        lambda match: sanitize_url(match.group(0)),
        message,
    )


def classify_exception(exc: Exception) -> str:
    """Classify common transport and parsing failures without dumping internals."""
    import json as json_module
    import requests
    import zlib

    if isinstance(exc, requests.exceptions.Timeout):
        return "timeout"
    if isinstance(exc, requests.exceptions.SSLError):
        return "tls_error"
    if isinstance(exc, requests.exceptions.HTTPError):
        return "http_error"
    if isinstance(exc, requests.exceptions.ConnectionError):
        detail = " ".join(str(part) for part in (exc, exc.__cause__, exc.__context__) if part)
        if any(term in detail.lower() for term in ("name resolution", "getaddrinfo", "nameresolutionerror", "gaierror")):
            return "dns_error"
        if any(term in detail.lower() for term in ("certificate", "ssl", "tls")):
            return "tls_error"
        return "connection_error"
    if isinstance(exc, requests.exceptions.RequestException):
        return "http_client_error"
    if isinstance(exc, (json_module.JSONDecodeError, UnicodeDecodeError, zlib.error)):
        return "parse_or_decode_error"
    if type(exc).__name__ == "SessionNotAvailableError":
        return "no_data_returned_after_http_responses"
    return "other_error"


class UpstreamRequestTrace:
    """Capture URL/status/exception summaries without recording headers or bodies."""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def __enter__(self) -> "UpstreamRequestTrace":
        from fastf1.req import Cache

        self.cache_class = Cache
        self.original_descriptor = Cache.__dict__["requests_get"]
        original_get = self.original_descriptor.__func__

        def traced_get(cache_class: type, url: str, **kwargs: Any) -> Any:
            record: dict[str, Any] = {
                "method": "GET",
                "url": sanitize_url(url),
                "endpoint": Path(urlsplit(str(url)).path).name,
            }
            try:
                response = original_get(cache_class, url, **kwargs)
            except Exception as exc:
                record.update({
                    "failure_class": classify_exception(exc),
                    "exception_type": type(exc).__name__,
                    "exception": sanitize_message(str(exc)),
                })
                self.records.append(record)
                raise
            record["http_status"] = int(response.status_code)
            record["http_reason"] = sanitize_message(str(response.reason or ""))
            record["from_cache"] = bool(getattr(response, "from_cache", False))
            response_url = getattr(response, "url", None)
            if response_url and sanitize_url(response_url) != record["url"]:
                record["final_url"] = sanitize_url(response_url)
            if response.status_code >= 400:
                record["failure_class"] = "http_error_response"
            self.records.append(record)
            return response

        Cache.requests_get = classmethod(traced_get)
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.cache_class.requests_get = self.original_descriptor


def load_with_diagnostics() -> tuple[Any | None, Exception | None, list[dict[str, Any]]]:
    """Run one session load with FastF1's soft exception swallowing disabled."""
    from fastf1.logger import LoggingManager

    previous_debug = LoggingManager.debug
    trace = UpstreamRequestTrace()
    try:
        LoggingManager.debug = True
        with trace:
            session = load_session(2024, "British Grand Prix", "Q")
        return session, None, trace.records
    except Exception as exc:
        return None, exc, trace.records
    finally:
        LoggingManager.debug = previous_debug


def json_value(value: Any) -> Any:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, timedelta):
        return value.total_seconds()
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--diagnose-upstream",
        action="store_true",
        help="Expose the first underlying FastF1 load exception and sanitized request status metadata.",
    )
    args = parser.parse_args()

    root = repository_root()
    cache_dir = configure_fastf1_cache(root)
    expected_cache = (root / "data" / "cache").resolve()
    if cache_dir != expected_cache or cache_dir.parent != (root / "data").resolve():
        raise RuntimeError(f"Cache path validation failed: {cache_dir}")

    request_trace: list[dict[str, Any]] = []
    if args.diagnose_upstream:
        session, load_error, request_trace = load_with_diagnostics()
    else:
        try:
            session = load_session(2024, "British Grand Prix", "Q")
            load_error = None
        except Exception as exc:
            session, load_error = None, exc

    if load_error is not None:
        report = {
            "status": "session_load_failed",
            "diagnostic_mode": args.diagnose_upstream,
            "data_source": "FastF1",
            "fastf1_version": fastf1.__version__,
            "fastf1_cache": str(cache_dir),
            "year": 2024,
            "event": "British Grand Prix",
            "session": "Q",
            "failure_class": classify_exception(load_error),
            "error_type": type(load_error).__name__,
            "error": sanitize_message(str(load_error)),
        }
        if args.diagnose_upstream:
            report["request_trace"] = request_trace
            report["trace_scope"] = (
                "GET URL path, response status/reason, cache indicator, and exception summary only; "
                "request/response headers, cookies, query strings, and bodies are not recorded."
            )
        print(json.dumps(report, indent=2, allow_nan=False))
        return 2

    event = session.event
    session_metadata = {
        key: json_value(event[key])
        for key in ("EventName", "Country", "Location", "OfficialEventName", "EventDate")
        if key in event.index
    }
    session_metadata.update({
        "year": 2024,
        "session_name": session.name,
        "session_date": json_value(session.date) if hasattr(session, "date") else None,
    })
    try:
        laps = session.laps
    except Exception as exc:
        report = {
            "status": "lap_data_unavailable",
            "diagnostic_mode": args.diagnose_upstream,
            "data_source": "FastF1",
            "fastf1_version": fastf1.__version__,
            "fastf1_cache": str(cache_dir),
            "session": session_metadata,
            "requested_drivers": {
                code: {
                    "exists_in_session": None,
                    "suitable_laps_available": None,
                    "reason": "Session lap timing data did not load; driver presence cannot be established.",
                }
                for code in ("VER", "NOR")
            },
            "error_type": type(exc).__name__,
            "error": str(exc),
            "interpretation": (
                "FastF1 resolved the requested session but returned without lap timing data. "
                "See its preceding log warnings for which upstream feeds failed."
            ),
        }
        if args.diagnose_upstream:
            report["request_trace"] = request_trace
        print(json.dumps(report, indent=2, allow_nan=False))
        return 2

    session_driver_codes = sorted(
        str(code) for code in laps["Driver"].dropna().unique()
    )

    drivers = {
        code: select_fastest_accurate_lap(session, code) for code in ("VER", "NOR")
    }
    for result in drivers.values():
        telemetry = result.get("telemetry")
        if telemetry and telemetry.get("extracted"):
            telemetry["units"] = {"Speed": "km/h", "Distance": "m"}
            telemetry["distance_method"] = (
                "FastF1 Telemetry.add_distance() integrates speed over sample time; meters"
            )

    result_a = drivers["VER"].get("selected_lap")
    result_b = drivers["NOR"].get("selected_lap")
    delta = lap_time_delta(result_a, result_b)
    if delta.get("available"):
        delta["faster_driver"] = {"A": "VER", "B": "NOR", "equal": "equal"}[
            delta["faster_driver"]
        ]

    report = {
        "status": "loaded",
        "diagnostic_mode": args.diagnose_upstream,
        "data_source": "FastF1",
        "fastf1_version": fastf1.__version__,
        "fastf1_cache": str(cache_dir),
        "session": session_metadata,
        "session_driver_codes": session_driver_codes,
        "valid_lap_policy": (
            "Filter each driver with Laps.pick_accurate() (IsAccurate=True), then "
            "Laps.pick_fastest() (requires FastF1's personal-best flag)."
        ),
        "telemetry_method": (
            "Lap.get_car_data().add_distance(); original car samples retained, "
            "no resampling, position merge, row-index alignment, or interpolation."
        ),
        "drivers": drivers,
        "lap_time_delta": delta,
        "distance_comparison": compare_distance_sampling(
            drivers["VER"].get("telemetry"), drivers["NOR"].get("telemetry")
        ),
    }
    if args.diagnose_upstream:
        report["request_trace"] = request_trace
    print(json.dumps(report, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
