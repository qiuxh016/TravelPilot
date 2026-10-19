import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Keep dev and production build artifacts separate when both commands run.
  // A second isolated dev server can be used for browser acceptance without
  // colliding with the user's already-running .next-dev process.
  distDir: process.env.TRAVELPILOT_TEST_NEXT === "1"
    ? ".next-dev-test"
    : process.env.NODE_ENV === "development" ? ".next-dev" : ".next",
};

export default nextConfig;

