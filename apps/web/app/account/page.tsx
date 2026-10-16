"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { deleteAccount, exportAccount, logout } from "../../lib/api";

export default function AccountPage() {
  const router = useRouter();
  async function download() {
    const data = await exportAccount();
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "travelpilot-account-export.json";
    anchor.click();
    URL.revokeObjectURL(url);
  }
  async function signOut() {
    try { await logout(); } finally { router.replace("/login"); }
  }
  async function removeAccount() {
    if (!window.confirm("删除账户会永久删除该账户的旅行、计划、攻略和记忆，且无法撤销。继续吗？")) return;
    await deleteAccount();
    router.replace("/login");
  }
  return <main className="shell">
    <nav className="nav"><Link className="brand" href="/trips">TravelPilot</Link><Link href="/trips">返回旅行</Link></nav>
    <section className="hero"><p className="eyebrow">Account &amp; privacy</p><h1>账户与数据</h1>
      <p className="lede">你可以下载账户数据，或永久删除账户和关联旅行数据。</p></section>
    <section className="layers">
      <article className="layer"><h2>导出数据</h2><p>下载账户旅行及其关联记录的 JSON 副本。</p>
        <button className="primary" type="button" onClick={() => void download()}>下载我的数据</button></article>
      <article className="layer"><h2>退出登录</h2><button className="secondary" type="button" onClick={() => void signOut()}>退出</button></article>
      <article className="layer"><h2>删除账户</h2><p>删除后无法恢复。</p><button className="secondary" type="button" onClick={() => void removeAccount()}>永久删除账户</button></article>
    </section>
  </main>;
}
