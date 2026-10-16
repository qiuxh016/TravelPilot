"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { confirmPasswordReset } from "../../lib/api";

export default function ResetPasswordPage() {
  const [token, setToken] = useState("");
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  useEffect(() => setToken(new URLSearchParams(window.location.search).get("token") ?? ""), []);
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError("");
    try { setMessage((await confirmPasswordReset(token, password)).message); }
    catch { setError("重置链接无效或已过期，请重新申请。"); }
  }
  return <main className="shell"><nav className="nav"><Link className="brand" href="/login">TravelPilot</Link></nav>
    <section className="hero"><h1>设置新密码</h1></section>
    <form className="layer auth-form" onSubmit={(event) => void submit(event)}>
      <label htmlFor="password">新密码（至少 12 个字符）</label><input id="password" type="password" minLength={12} maxLength={128} required value={password} onChange={(event) => setPassword(event.target.value)} />
      {message && <p role="status">{message}</p>}{error && <p role="alert">{error}</p>}
      <button className="primary" disabled={!token}>更新密码</button><Link href="/login">返回登录</Link>
    </form>
  </main>;
}
