"use client";

import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError, getCurrentUser } from "../lib/api";
import { WorkspaceShell } from "./workspace-shell";

const publicPaths = ["/login", "/forgot-password", "/verify-email", "/reset-password"];

export function AuthGate({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [status, setStatus] = useState<"checking" | "ready" | "unavailable">("checking");
  const [retryCount, setRetryCount] = useState(0);
  const isPublicPath = publicPaths.includes(pathname);

  useEffect(() => {
    if (isPublicPath) {
      return;
    }

    let cancelled = false;
    setStatus("checking");
    void getCurrentUser()
      .then(() => {
        if (!cancelled) setStatus("ready");
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        if (error instanceof ApiError && error.status === 401) {
          router.replace("/login");
          return;
        }
        setStatus("unavailable");
      });

    return () => { cancelled = true; };
  }, [isPublicPath, pathname, retryCount, router]);

  if (isPublicPath) return children;
  if (status === "checking") {
    return <main className="shell"><p>正在验证登录状态……</p></main>;
  }
  if (status === "unavailable") {
    return (
      <main className="shell" role="alert">
        <h1>暂时无法连接服务</h1>
        <p>登录状态检查没有收到服务器响应。请确认 TravelPilot API 正常运行后重试。</p>
        <button className="primary" type="button" onClick={() => setRetryCount((count) => count + 1)}>
          重试连接
        </button>
      </main>
    );
  }
  return <WorkspaceShell>{children}</WorkspaceShell>;
}
