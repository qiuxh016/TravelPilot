"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { AccountProfilePanel } from "../../components/account-profile-panel";
import { createUserMemory, deleteAccount, deleteUserMemory, exportAccount, getUserMemorySettings, listUserMemories, logout, setUserMemorySettings, updateUserMemory, type UserPreferenceMemory } from "../../lib/api";

export default function AccountPage() {
  const router = useRouter();
  const [memories, setMemories] = useState<UserPreferenceMemory[]>([]);
  const [memoryEnabled, setMemoryEnabled] = useState(false);
  const [memoryText, setMemoryText] = useState("");
  const [memoryType, setMemoryType] = useState<"PREFERENCE" | "HARD_LIMIT">("PREFERENCE");
  const [editingMemory, setEditingMemory] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ text: string; error?: boolean } | null>(null);

  useEffect(() => {
    let active = true;
    void Promise.all([getUserMemorySettings(), listUserMemories()]).then(([settings, rows]) => {
      if (!active) return;
      setMemoryEnabled(settings.enabled);
      setMemories(rows);
    }).catch((error: unknown) => {
      if (active) {
        setLoadFailed(true);
        setNotice({ text: error instanceof Error ? error.message : "偏好记忆加载失败，请稍后重试。", error: true });
      }
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  async function perform(key: string, success: string, action: () => Promise<void>) {
    setBusy(key);
    setNotice(null);
    try { await action(); setNotice({ text: success }); }
    catch (error) { setNotice({ text: error instanceof Error ? error.message : "操作失败，请稍后重试。", error: true }); }
    finally { setBusy(null); }
  }

  async function toggleMemory(enabled: boolean) {
    await perform("settings", enabled ? "已开启旅行偏好记忆。" : "已关闭旅行偏好记忆。", async () => {
      const settings = await setUserMemorySettings(enabled);
      setMemoryEnabled(settings.enabled);
    });
  }
  async function addMemory() {
    const text = memoryText.trim();
    if (text.length < 2) return;
    await perform("add", "已保存。开启记忆后，旅行顾问会在规划时参考这些内容。", async () => {
      const row = await createUserMemory({ category: "GENERAL", text, constraint_type: memoryType });
      setMemories((current) => [row, ...current]); setMemoryText("");
    });
  }
  async function removeMemory(id: string) {
    await perform(`delete-memory-${id}`, "这条偏好记忆已删除。", async () => {
      await deleteUserMemory(id); setMemories((current) => current.filter((item) => item.id !== id));
      if (editingMemory === id) setEditingMemory(null);
    });
  }
  async function saveMemoryEdit(id: string) {
    const text = editText.trim();
    if (text.length < 2) return;
    await perform(`edit-memory-${id}`, "偏好记忆已更新。", async () => {
      const updated = await updateUserMemory(id, { text });
      setMemories((current) => current.map((item) => item.id === id ? updated : item));
      setEditingMemory(null);
    });
  }
  async function download() {
    await perform("export", "数据文件已生成并开始下载。", async () => {
      const data = await exportAccount();
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
      const anchor = document.createElement("a");
      anchor.href = url; anchor.download = "travelpilot-account-export.json";
      document.body.appendChild(anchor); anchor.click(); anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
  }
  async function signOut() {
    setBusy("logout"); setNotice(null);
    try { await logout(); router.replace("/login"); }
    catch (error) { setNotice({ text: error instanceof Error ? error.message : "退出登录失败，请重试。", error: true }); setBusy(null); }
  }
  async function removeAccount() {
    if (!window.confirm("删除账户会永久删除该账户的旅行、计划、攻略、分享内容和偏好记忆，且无法撤销。确定继续吗？")) return;
    setBusy("delete-account"); setNotice(null);
    try { await deleteAccount(); router.replace("/login"); }
    catch (error) { setNotice({ text: error instanceof Error ? error.message : "账户删除失败，请重试。", error: true }); setBusy(null); }
  }

  return <main className="shell account-page">
    <nav className="nav"><Link className="brand" href="/trips">TravelPilot</Link><Link href="/trips">返回旅行</Link></nav>
    <header className="hero account-heading">
      <p className="eyebrow">YOUR ACCOUNT</p><h1>账户与隐私</h1>
      <p className="lede">掌握旅行顾问记住什么、下载你的资料，或管理账户安全。</p>
      <p className="account-muted">旅行、计划和记忆默认仅你可见；只有主动加入好友圈或授权共享时，好友才能查看相应内容。旅行顾问和攻略识别会把你提交的文字发送到管理员配置的模型服务；地图搜索会把地点关键词发送到地图服务。照片上传前会去除 EXIF/GPS 元数据。删除账户会清除在线账户资料；备份副本按运营者配置的保留期限到期清理。</p>
    </header>

    {notice && <p className={`account-notice${notice.error ? " is-error" : ""}`} role={notice.error ? "alert" : "status"}>{notice.text}</p>}

    <AccountProfilePanel />

    <section className="account-section account-memory" aria-labelledby="memory-title">
      <div className="account-section-heading">
        <div><span className="account-step">01 / PERSONALIZATION</span><h2 id="memory-title">让规划更懂你的习惯</h2>
          <p>把长期偏好告诉旅行顾问。每一条都由你添加、确认和管理。</p></div>
        <span className={`account-state${memoryEnabled ? " is-on" : ""}`}><i aria-hidden="true" />{memoryEnabled ? "记忆已开启" : "记忆已关闭"}</span>
      </div>

      <div className="account-memory-controls">
        <div className="account-memory-toggle">
          <div><strong>在后续规划中使用我的偏好</strong><p id="account-memory-toggle-description">关闭后，已保存的内容仍会保留，但不会提供给旅行顾问。</p></div>
          <label className="account-switch"><input type="checkbox" aria-label="在后续规划中使用我的偏好" aria-describedby="account-memory-toggle-description" checked={memoryEnabled} disabled={loading || busy !== null} onChange={(event) => void toggleMemory(event.target.checked)} /><span aria-hidden="true" /></label>
        </div>
        <div className="account-memory-add">
          <div className="account-subheading"><span className="account-spark" aria-hidden="true">✳</span><div><strong>添加一条旅行偏好</strong><small>例如：每天最多安排三个地点；不吃辣；优先选择公共交通。</small></div></div>
          <div className="account-memory-form">
            <label>这条内容属于<select value={memoryType} onChange={(event) => setMemoryType(event.target.value as "PREFERENCE" | "HARD_LIMIT")}><option value="PREFERENCE">个人偏好</option><option value="HARD_LIMIT">必须遵守的限制</option></select></label>
            <label className="account-memory-text">你的说明<input value={memoryText} onChange={(event) => setMemoryText(event.target.value)} maxLength={1000} placeholder="写下希望旅行顾问记住的事" /></label>
            <button className="primary" type="button" disabled={busy !== null || memoryText.trim().length < 2} onClick={() => void addMemory()}>{busy === "add" ? "正在保存…" : "确认并保存"}</button>
          </div>
        </div>
      </div>

      <div className="account-saved-heading"><h3>已保存的偏好</h3><span>{loading ? "读取中…" : `${memories.length} 条`}</span></div>
      {loading ? <p className="account-muted">正在读取你的偏好记忆…</p> : loadFailed ? <div className="account-empty"><span aria-hidden="true">!</span><div><strong>暂时无法读取偏好</strong><p>请刷新页面重试；当前不会把无法读取的数据当作空记录处理。</p></div></div> : memories.length === 0 ? <div className="account-empty"><span aria-hidden="true">✎</span><div><strong>这里还没有偏好记忆</strong><p>保存后，你可以随时编辑或删除；是否用于规划由上方开关决定。</p></div></div> :
        <ul className="memory-list account-memory-list">{memories.map((memory) => <li key={memory.id}>
          <div className="account-memory-copy"><span className={`account-memory-tag${memory.constraint_type === "HARD_LIMIT" ? " is-limit" : ""}`}>{memory.constraint_type === "HARD_LIMIT" ? "必须遵守" : "偏好"}</span>
            {editingMemory === memory.id ? <input className="account-edit-input" aria-label="编辑偏好记忆" value={editText} onChange={(event) => setEditText(event.target.value)} maxLength={1000} /> : <span>{memory.text}</span>}</div>
          <span className="memory-actions">{editingMemory === memory.id ? <><button className="secondary" type="button" disabled={busy !== null || editText.trim().length < 2} onClick={() => void saveMemoryEdit(memory.id)}>{busy === `edit-memory-${memory.id}` ? "保存中…" : "保存"}</button><button className="secondary" type="button" disabled={busy !== null} onClick={() => setEditingMemory(null)}>取消</button></> : <><button className="secondary" type="button" disabled={busy !== null} onClick={() => { setEditingMemory(memory.id); setEditText(memory.text); }}>编辑</button><button className="secondary" type="button" disabled={busy !== null} onClick={() => void removeMemory(memory.id)}>{busy === `delete-memory-${memory.id}` ? "删除中…" : "删除"}</button></>}</span>
        </li>)}</ul>}
    </section>

    <section className="account-lower-grid">
      <article className="account-section account-export" aria-labelledby="export-title">
        <span className="account-step">02 / YOUR DATA</span><h2 id="export-title">带走你的旅行资料</h2>
        <p>导出为 JSON 文件，包含旅行、行程计划、收藏地点、旅行记忆、分享互动和顾问对话等账户关联数据。</p>
        <button className="secondary" type="button" disabled={busy !== null} onClick={() => void download()}>{busy === "export" ? "正在准备文件…" : "下载账户数据"}<span aria-hidden="true"> ↓</span></button>
        <small>文件仅由当前登录账户请求，建议妥善保管。</small>
      </article>
      <article className="account-section account-security" aria-labelledby="security-title">
        <span className="account-step">03 / ACCOUNT SECURITY</span><h2 id="security-title">账户安全</h2>
        <div className="account-security-row"><div><strong>退出当前账户</strong><p>在此设备结束本次登录。</p></div><button className="secondary" type="button" disabled={busy !== null} onClick={() => void signOut()}>{busy === "logout" ? "正在退出…" : "退出登录"}</button></div>
        <div className="account-danger-row"><div><strong>删除账户</strong><p>永久删除账户及关联旅行资料、分享内容和偏好记忆。此操作无法撤销。</p></div><button className="account-danger-button" type="button" disabled={busy !== null} onClick={() => void removeAccount()}>{busy === "delete-account" ? "正在删除…" : "永久删除"}</button></div>
      </article>
    </section>
  </main>;
}
