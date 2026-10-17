"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { getAccountAvatarBlob, getCurrentUser, listTrips, type AccountUser, type Trip } from "../lib/api";
import { TripSelectionProvider, tripOptionLabel } from "./trip-selection-context";

type Theme = "light" | "dark";

function NavIcon({ name }: { name: string }) {
  const paths: Record<string, string> = {
    trips: "M4 7.5h16v13H4z M8 7.5V5h8v2.5 M8 11v5 M12 11v5 M16 11v5",
    create: "M12 5v14 M5 12h14",
    places: "M20 10c0 5-8 11-8 11S4 15 4 10a8 8 0 1 1 16 0Z M12 10a2.5 2.5 0 1 0 0-.01",
    advisor: "M20 11.5a7.5 7.5 0 0 1-7.5 7.5H7l-4 2v-6A7.5 7.5 0 1 1 20 11.5Z M8 11h.01 M12 11h.01 M16 11h.01",
    overview: "M3.5 12 12 4l8.5 8 M5.5 10.5V20h13v-9.5 M9.5 20v-6h5v6",
    plan: "M5 5h14v15H5z M8 9h8 M8 12.5h8 M8 16h5 M8 3v4 M16 3v4",
    events: "M12 3 3.5 19h17L12 3Z M12 9v4 M12 16h.01",
    memory: "M5 5.5A2.5 2.5 0 0 1 7.5 3H20v15H7.5A2.5 2.5 0 0 0 5 20.5v-15ZM5 6h12 M8 9h8 M8 12h6",
    friends: "M16 20v-1.5a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4V20 M9.5 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7ZM17 11a3 3 0 1 0 0-6 M17 14.5a4 4 0 0 1 4 4V20",
    moments: "M20 7v10a2 2 0 0 1-2 2H6l-3 2V7a2 2 0 0 1 2-2h13a2 2 0 0 1 2 2ZM7 10h10 M7 14h7 M8 5V3 M16 5V3",
    account: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8ZM4 21a8 8 0 0 1 16 0",
  };
  return <svg aria-hidden="true" className="workspace-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"><path d={paths[name]} /></svg>;
}

export function WorkspaceShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [tripId, setTripId] = useState("");
  const [trips, setTrips] = useState<Trip[]>([]);
  const [theme, setTheme] = useState<Theme>("light");
  const [user, setUser] = useState<AccountUser | null>(null);
  const [avatarUrl, setAvatarUrl] = useState("");

  useEffect(() => {
    let active = true;
    let objectUrl = "";
    const refreshProfile = async (event?: Event) => {
      const updated = (event as CustomEvent<{ user?: AccountUser }> | undefined)?.detail?.user;
      try {
        const current = updated ?? await getCurrentUser();
        if (!active) return;
        setUser(current);
        if (current.has_avatar) {
          const next = await getAccountAvatarBlob();
          if (!active) { URL.revokeObjectURL(next); return; }
          if (objectUrl) URL.revokeObjectURL(objectUrl);
          objectUrl = next; setAvatarUrl(next);
        } else {
          if (objectUrl) URL.revokeObjectURL(objectUrl);
          objectUrl = ""; setAvatarUrl("");
        }
      } catch { /* Keep the account shortcut available even if profile details cannot load. */ }
    };
    void refreshProfile();
    window.addEventListener("travelpilot:profile-updated", refreshProfile);
    return () => { active = false; window.removeEventListener("travelpilot:profile-updated", refreshProfile); if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, []);

  useEffect(() => {
    let active = true;
    void listTrips().then((rows) => { if (active) setTrips(rows); }).catch(() => { if (active) setTrips([]); });
    return () => { active = false; };
  }, [pathname]);

  useEffect(() => {
    const routeTripSegment = pathname.match(/^\/trips\/([^/]+)/)?.[1] ?? "";
    const routeTripId = routeTripSegment === "new" ? "" : routeTripSegment;
    const queryTripId = new URLSearchParams(window.location.search).get("tripId") ?? "";
    const rememberedTripId = window.localStorage.getItem("travelpilot_selected_trip_id") ?? window.localStorage.getItem("travelpilot_last_trip_id") ?? "";
    const contextualTripId = routeTripId || queryTripId;
    const selectedTripId = contextualTripId || (trips.some((trip) => trip.id === rememberedTripId) ? rememberedTripId : "");
    setTripId(selectedTripId);
    window.localStorage.setItem("travelpilot_selected_trip_id", selectedTripId);

    const savedTheme = window.localStorage.getItem("travelpilot_theme");
    const initialTheme: Theme = savedTheme === "dark" ? "dark" : "light";
    setTheme(initialTheme);
    document.documentElement.dataset.theme = initialTheme;
  }, [pathname, trips]);

  const paths = useMemo(() => {
    const tripPath = tripId ? `/trips/${tripId}` : "/trips";
    return {
      places: tripId ? `/places?tripId=${encodeURIComponent(tripId)}` : "/places",
      overview: tripPath,
      plan: tripId ? `${tripPath}/plan` : "/trips",
      events: tripId ? `${tripPath}/events` : "/trips",
      memory: tripId ? `${tripPath}/memory` : "/trips",
    };
  }, [tripId]);

  function isActive(href: string, key: string) {
    if (key === "trips") return pathname === "/trips";
    if (key === "places") return pathname === "/places";
    if (key === "account") return pathname === "/account";
    if (key === "friends") return pathname === "/friends";
    if (key === "moments") return pathname === "/moments";
    if (key === "overview") return pathname !== "/trips/new" && /^\/trips\/[^/]+$/.test(pathname);
    return href !== "/trips" && pathname === href;
  }

  function setSelectedTheme(nextTheme: Theme) {
    setTheme(nextTheme);
    document.documentElement.dataset.theme = nextTheme;
    window.localStorage.setItem("travelpilot_theme", nextTheme);
  }

  function selectTrip(nextTripId: string) {
    setTripId(nextTripId);
    window.localStorage.setItem("travelpilot_selected_trip_id", nextTripId);
    const routeMatch = pathname.match(/^\/trips\/([^/]+)(?:\/(plan|events|memory|import|sharing))?$/);
    if (routeMatch && routeMatch[1] !== "new") {
      router.push(nextTripId ? `/trips/${nextTripId}${routeMatch[2] ? `/${routeMatch[2]}` : ""}` : "/places");
      return;
    }
    if (pathname === "/places") {
      router.replace(nextTripId ? `/places?tripId=${encodeURIComponent(nextTripId)}` : "/places");
    }
    if (pathname === "/advisor" && !new URLSearchParams(window.location.search).has("sessionId")) {
      router.replace(nextTripId ? `/advisor?tripId=${encodeURIComponent(nextTripId)}` : "/advisor");
    }
  }

  const links = [
    { key: "trips", label: "我的旅行", href: "/trips", icon: "trips" },
    { key: "create", label: "创建旅行", href: "/trips/new", icon: "create" },
    { key: "places", label: "地点探索", href: paths.places, icon: "places" },
    { key: "advisor", label: "AI 旅行顾问", href: "/advisor", icon: "advisor" },
    { key: "overview", label: "旅行概览", href: paths.overview, icon: "overview", needsTrip: true },
    { key: "plan", label: "行程计划", href: paths.plan, icon: "plan", needsTrip: true },
    { key: "events", label: "现实事件", href: paths.events, icon: "events", needsTrip: true },
    { key: "memory", label: "旅行记忆", href: paths.memory, icon: "memory", needsTrip: true },
    { key: "friends", label: "好友圈与分享", href: "/friends", icon: "friends" },
    { key: "moments", label: "旅行动态", href: "/moments", icon: "moments" },
    { key: "account", label: "账户与隐私", href: "/account", icon: "account" },
  ];

  return <TripSelectionProvider value={{ trips, selectedTripId: tripId, selectTrip }}><div className="workspace-layout">
    <aside className="workspace-sidebar" aria-label="主导航">
      <Link className="workspace-brand" href="/trips"><span className="brand-mark">T</span><span>TravelPilot</span></Link>
      <nav className="workspace-links">
        {links.map((item) => <Link
          className={`workspace-link${isActive(item.href, item.key) ? " is-active" : ""}${item.needsTrip && !tripId ? " needs-trip" : ""}`}
          href={item.href}
          aria-current={isActive(item.href, item.key) ? "page" : undefined}
          aria-disabled={item.needsTrip && !tripId ? true : undefined}
          title={item.needsTrip && !tripId ? "进入一条旅行后即可使用" : undefined}
          key={item.key}
        >
          <NavIcon name={item.icon} />
          <span>{item.label}</span>
          {item.needsTrip && !tripId ? <small>先选旅行</small> : null}
        </Link>)}
      </nav>
      <div className="theme-control" aria-label="主题切换">
        <span className="theme-caption">外观</span>
        <div className="theme-options">
          <button type="button" className={theme === "light" ? "selected" : ""} aria-pressed={theme === "light"} onClick={() => setSelectedTheme("light")}>
            <span aria-hidden="true">☼</span> 浅色
          </button>
          <button type="button" className={theme === "dark" ? "selected" : ""} aria-pressed={theme === "dark"} onClick={() => setSelectedTheme("dark")}>
            <span aria-hidden="true">◐</span> 深色
          </button>
        </div>
      </div>
    </aside>
    <div className="workspace-main">
      <div className="workspace-topbar"><label className="workspace-trip-switch"><span>当前旅行</span><select aria-label="切换当前旅行" value={tripId} onChange={(event) => selectTrip(event.target.value)}><option value="">自由探索</option>{trips.map((trip) => <option key={trip.id} value={trip.id}>{tripOptionLabel(trip, trips)}</option>)}</select></label><Link className="workspace-account-link" href="/account" aria-label="打开账号设置">
        <span className="workspace-account-copy"><strong>{user?.display_name || user?.email?.split("@")[0] || "我的账号"}</strong><small>账号设置</small></span>
        <span className="workspace-account-avatar">{avatarUrl ? <img src={avatarUrl} alt="" /> : <span>{(user?.display_name || user?.email || "旅").slice(0, 1).toUpperCase()}</span>}</span>
      </Link></div>
      {children}
    </div>
  </div></TripSelectionProvider>;
}
