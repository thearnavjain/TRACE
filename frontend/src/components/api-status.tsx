"use client";

import { useEffect, useState } from "react";

type Health = { status: string; service: string };

export function ApiStatus() {
  const [state, setState] = useState<"checking" | "online" | "offline">("checking");

  useEffect(() => {
    const baseUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8000";
    fetch(`${baseUrl}/api/health`)
      .then(async (response) => {
        if (!response.ok) throw new Error("API unavailable");
        return (await response.json()) as Health;
      })
      .then((health) => setState(health.status === "ok" ? "online" : "offline"))
      .catch(() => setState("offline"));
  }, []);

  const label = state === "checking" ? "Checking API" : state === "online" ? "API connected" : "API unavailable";

  return <div className={`api-status ${state}`} role="status"><span />{label}</div>;
}
