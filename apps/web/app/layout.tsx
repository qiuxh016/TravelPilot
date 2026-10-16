import type { Metadata } from "next";
import "./globals.css";
import { AuthGate } from "../components/auth-gate";
import { productConfig } from "../lib/product-config";

export const metadata: Metadata = {
  title: productConfig.name,
  description: productConfig.description,
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body><AuthGate>{children}</AuthGate></body>
    </html>
  );
}

