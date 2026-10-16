"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { verifyEmail } from "../../lib/api";

export default function VerifyEmailPage() {
  const [message, setMessage] = useState("正在验证邮箱……");
  const [error, setError] = useState(false);
  useEffect(() => {
    const token = new URLSearchParams(window.location.search).get("token");
    if (!token) { setError(true); setMessage("验证链接无效。"); return; }
    void verifyEmail(token).then((result) => setMessage(result.message)).catch(() => { setError(true); setMessage("验证链接无效或已过期，请重新发送验证邮件。"); });
  }, []);
  return <main className="shell"><section className="hero"><h1>邮箱验证</h1><p role={error ? "alert" : "status"}>{message}</p><Link className="primary" href="/login">前往登录</Link></section></main>;
}
