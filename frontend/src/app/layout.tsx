import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "TRACE — Historical lap comparison",
  description: "Compare real Formula 1 lap times and timestamped car telemetry.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
