import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "TravelPilot",
  description: "Context-aware travel planning workspace",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}

