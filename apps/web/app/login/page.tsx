"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { FormEvent, useState } from "react";
import { ApiError, login, register, resendVerification } from "../../lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [account, setAccount] = useState("");
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
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
        const result = await register({ email, username, display_name: displayName, password, invite_code: inviteCode || undefined });
        setSuccess(result.message);
        setMode("login");
        setAccount(email);
        setPassword("");
      } else {
        await login(account, password);
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
      {mode === "login" ? <>
        <label htmlFor="account">邮箱或用户名</label>
        <input id="account" type="text" autoComplete="username" required maxLength={320} value={account} onChange={(event) => setAccount(event.target.value)} placeholder="邮箱或用户名" />
      </> : <>
        <label htmlFor="email">邮箱</label>
        <input id="email" type="email" autoComplete="email" required value={email} onChange={(event) => setEmail(event.target.value)} />
        <label htmlFor="username">用户名</label>
        <input id="username" type="text" autoComplete="username" required minLength={3} maxLength={24} pattern="[A-Za-z0-9][A-Za-z0-9_]{2,23}" title="3–24 位字母、数字或下划线，以字母或数字开头" value={username} onChange={(event) => setUsername(event.target.value)} placeholder="例如：traveler_01" />
        <small>用户名用于登录，只能使用 3–24 位字母、数字和下划线。</small>
        <label htmlFor="display-name">昵称</label>
        <input id="display-name" type="text" autoComplete="nickname" required minLength={1} maxLength={48} value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="朋友看到的名字" />
      </>}
      <label htmlFor="password">密码（至少 12 个字符）</label>
      <input id="password" type="password" autoComplete={mode === "login" ? "current-password" : "new-password"} minLength={12} maxLength={128} required value={password} onChange={(event) => setPassword(event.target.value)} />
      {mode === "register" && <><label htmlFor="invite-code">邀请码（如有）</label><input id="invite-code" autoComplete="off" value={inviteCode} onChange={(event) => setInviteCode(event.target.value)} /></>}
      {error && <p role="alert">{error}</p>}
      {success && <p role="status">{success}</p>}
      {mode === "login" && <Link href="/forgot-password">忘记密码？</Link>}
      {mode === "login" && <button className="secondary" type="button" onClick={async () => { if (!account.includes("@")) { setError("请先输入注册邮箱，再重新发送验证邮件。"); return; } try { const result = await resendVerification(account); setSuccess(result.message); setError(""); } catch { setError("暂时无法发送验证邮件，请稍后重试"); } }}>重新发送验证邮件</button>}
      <button className="primary" type="submit" disabled={busy}>{busy ? "请稍候…" : mode === "login" ? "登录" : "注册"}</button>
      <button className="secondary" type="button" onClick={() => { if (mode === "login") setEmail(account.includes("@") ? account : ""); else setAccount(email); setMode(mode === "login" ? "register" : "login"); setError(""); setSuccess(""); }}>
        {mode === "login" ? "创建新账户" : "已有账户？登录"}
      </button>
    </form>
  </main>;
}
