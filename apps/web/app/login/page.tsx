"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import { ApiError, login, register, resendVerification } from "../../lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setSuccess("");
    setBusy(true);
    try {
      if (mode === "register") {
        const result = await register(email, password, inviteCode || undefined);
        setSuccess(result.message);
        setMode("login");
        setPassword("");
      } else {
        await login(email, password);
        router.replace("/trips");
      }
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "暂时无法登录，请稍后重试");
    } finally {
      setBusy(false);
    }
  }

  return <main className="shell">
    <nav className="nav"><Link className="brand" href="/login">TravelPilot</Link></nav>
    <section className="hero"><p className="eyebrow">Private travel workspace</p>
      <h1>{mode === "login" ? "登录你的账户" : "创建账户"}</h1>
      <p className="lede">每个账户的旅行和记忆仅对账户本人开放。</p>
    </section>
    <form className="layer auth-form" onSubmit={(event) => void submit(event)}>
      <label htmlFor="email">邮箱</label>
      <input id="email" type="email" autoComplete="email" required value={email} onChange={(event) => setEmail(event.target.value)} />
      <label htmlFor="password">密码（至少 12 个字符）</label>
      <input id="password" type="password" autoComplete={mode === "login" ? "current-password" : "new-password"} minLength={12} maxLength={128} required value={password} onChange={(event) => setPassword(event.target.value)} />
      {mode === "register" && <><label htmlFor="invite-code">邀请码（如有）</label><input id="invite-code" autoComplete="off" value={inviteCode} onChange={(event) => setInviteCode(event.target.value)} /></>}
      {error && <p role="alert">{error}</p>}
      {success && <p role="status">{success}</p>}
      {mode === "login" && <Link href="/forgot-password">忘记密码？</Link>}
      {mode === "login" && <button className="secondary" type="button" onClick={async () => { try { const result = await resendVerification(email); setSuccess(result.message); setError(""); } catch { setError("暂时无法发送验证邮件，请稍后重试"); } }}>重新发送验证邮件</button>}
      <button className="primary" type="submit" disabled={busy}>{busy ? "请稍候…" : mode === "login" ? "登录" : "注册"}</button>
      <button className="secondary" type="button" onClick={() => { setMode(mode === "login" ? "register" : "login"); setError(""); }}>
        {mode === "login" ? "创建新账户" : "已有账户？登录"}
      </button>
    </form>
  </main>;
}
