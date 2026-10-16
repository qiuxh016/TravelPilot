"use client";

import Link from "next/link";
import { useState } from "react";
import { requestPasswordReset } from "../../lib/api";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(""); setMessage("");
    try { setMessage((await requestPasswordReset(email)).message); }
    catch { setError("暂时无法处理请求，请稍后重试。"); }
  }
  return <main className="shell"><nav className="nav"><Link className="brand" href="/login">TravelPilot</Link></nav>
    <section className="hero"><h1>重置密码</h1><p className="lede">输入账户邮箱，我们会发送重置链接。</p></section>
    <form className="layer auth-form" onSubmit={(event) => void submit(event)}>
      <label htmlFor="email">邮箱</label><input id="email" type="email" required value={email} onChange={(event) => setEmail(event.target.value)} />
      {message && <p role="status">{message}</p>}{error && <p role="alert">{error}</p>}
      <button className="primary">发送重置链接</button><Link href="/login">返回登录</Link>
    </form>
  </main>;
}
