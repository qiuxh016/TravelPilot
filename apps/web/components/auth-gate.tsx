"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { getCurrentUser, setAccessToken } from "../lib/api";
import { WorkspaceShell } from "./workspace-shell";

export function AuthGate({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (["/login", "/forgot-password", "/verify-email", "/reset-password"].includes(pathname)) {
      setReady(true);
      return;
    }
    void getCurrentUser().then(() => setReady(true)).catch(() => {
      setAccessToken(null);
      router.replace("/login");
    });
  }, [pathname, router]);

  if (!ready && !["/login", "/forgot-password", "/verify-email", "/reset-password"].includes(pathname)) return <main className="shell"><p>正在验证登录状态……</p></main>;
  if (["/login", "/forgot-password", "/verify-email", "/reset-password"].includes(pathname)) return children;
  return <WorkspaceShell>{children}</WorkspaceShell>;
}
