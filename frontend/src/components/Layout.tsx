import { useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { api, useApi } from "../lib/api";
import { clearApiCache, isIOS, isStandalone, useInstallPrompt, useOffline } from "../lib/pwa";
import type { User } from "../lib/types";

const NAV: [string, string, string?][] = [
  ["/", "Home"],
  ["/radar", "AI Radar"],
  ["/feed", "Feed"],
  ["/search", "Search"],
  ["/opportunities", "Opportunities"],
  ["/timeline", "Timeline"],
  ["/stack", "Stack Explorer"],
  ["/reports", "Reports"],
  ["/community", "Community"],
  ["/watchlists", "Watchlists"],
  ["/devices", "Devices"],
  ["/sources", "Sources", "admin"],
  ["/ops", "Operations", "admin"],
  ["/settings", "Settings"],
];
// Inline SVG icons: identical on iOS/Android (emoji glyphs differ per platform), inherit text color.
const ICON_PATHS: Record<string, string> = {
  home: "M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z",
  radar: "M12 12m-9 0a9 9 0 1 0 18 0a9 9 0 1 0-18 0M12 12m-4.5 0a4.5 4.5 0 1 0 9 0a4.5 4.5 0 1 0-9 0M12 12l6-6",
  search: "M11 11m-7 0a7 7 0 1 0 14 0a7 7 0 1 0-14 0M16.5 16.5L21 21",
  bell: "M6 16V11a6 6 0 0 1 12 0v5l2 2H4zM10 20a2 2 0 0 0 4 0",
  menu: "M4 6h16M4 12h16M4 18h16",
};
function Icon({ name }: { name: string }) {
  return (
    <svg aria-hidden viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d={ICON_PATHS[name]} />
    </svg>
  );
}
const TABS: [string, string, string][] = [
  ["/", "Home", "home"],
  ["/radar", "Radar", "radar"],
  ["/search", "Search", "search"],
  ["/alerts", "Alerts", "bell"],
];
const RANK: Record<string, number> = { viewer: 0, member: 1, moderator: 2, admin: 3, owner: 4 };

export function Layout({ user, onLogout }: { user: User; onLogout: () => void }) {
  const { data: alerts } = useApi<{ unread: number }>("/api/alerts?unread=true");
  const offline = useOffline();
  const { canInstall, install } = useInstallPrompt();
  const [more, setMore] = useState(false);
  const loc = useLocation();
  const links = NAV.filter(([, , min]) => !min || (RANK[user.role] ?? 0) >= RANK[min]);
  const unread = alerts?.unread ?? 0;
  const logout = async () => {
    await api("/api/auth/logout", { method: "POST" }).catch(() => undefined);
    await clearApiCache();
    onLogout();
  };
  const linkCls = ({ isActive }: { isActive: boolean }) =>
    `rounded px-2 py-1 ${isActive ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900" : "hover:bg-slate-100 dark:hover:bg-slate-800"}`;
  const showIosHint = isIOS() && !isStandalone();
  return (
    <div className="min-h-screen pb-[calc(4rem+env(safe-area-inset-bottom))] md:pb-0">
      <header className="sticky top-0 z-20 border-b border-slate-200 bg-white/90 pt-[env(safe-area-inset-top)] backdrop-blur dark:border-slate-800 dark:bg-slate-950/90">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2">
          <span className="font-bold tracking-tight">AI Intelligence Hub</span>
          <span className="rounded bg-emerald-100 px-1.5 text-[10px] font-semibold uppercase text-emerald-900 dark:bg-emerald-950 dark:text-emerald-300" title="Hard zero-cost mode: paid endpoints are blocked in code">
            local · zero-cost
          </span>
          <nav className="hidden flex-wrap gap-1 text-sm md:flex">
            {links.map(([to, label]) => (
              <NavLink key={to} to={to} end={to === "/"} className={linkCls}>
                {label}
              </NavLink>
            ))}
            <NavLink to="/alerts" className={linkCls}>
              Alerts{unread > 0 ? ` (${unread})` : ""}
            </NavLink>
          </nav>
          <div className="ml-auto hidden items-center gap-2 text-sm text-slate-500 md:flex">
            <span title={user.email}>
              {user.display_name} · {user.role}
            </span>
            <button onClick={logout} className="underline">
              log out
            </button>
          </div>
          <button onClick={() => window.location.reload()} className="ml-auto rounded px-2 py-1 text-lg md:hidden" aria-label="Refresh">
            ↻
          </button>
        </div>
      </header>

      {offline && (
        <div role="status" className="bg-amber-100 px-4 py-1.5 text-center text-sm text-amber-900 dark:bg-amber-950 dark:text-amber-200">
          ▲ Offline or the hub computer is unreachable — showing what is saved on this device.
        </div>
      )}

      <main className="mx-auto max-w-7xl px-3 py-3 md:px-4 md:py-4">
        <Outlet />
      </main>
      <footer className="mx-auto hidden max-w-7xl px-4 pb-6 text-xs text-slate-500 md:block">
        Data comes from the enabled sources listed under Sources, each with its own attribution. arXiv metadata is CC0 (not
        affiliated with or endorsed by arXiv). AI text is generated locally and labeled; verify against original links.
      </footer>

      {/* Phone navigation */}
      <nav
        aria-label="Primary"
        className="fixed inset-x-0 bottom-0 z-30 grid grid-cols-5 border-t border-slate-200 bg-white pb-[env(safe-area-inset-bottom)] dark:border-slate-800 dark:bg-slate-950 md:hidden"
      >
        {TABS.map(([to, label, icon]) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            onClick={() => setMore(false)}
            className={({ isActive }) => `relative flex h-16 flex-col items-center justify-center gap-0.5 text-xs ${isActive && !more ? "text-blue-600 dark:text-blue-400" : "text-slate-600 dark:text-slate-400"}`}
          >
            <Icon name={icon} />
            {label}
            {to === "/alerts" && unread > 0 && (
              <span className="absolute right-[22%] top-2 min-w-5 rounded-full bg-rose-600 px-1 text-center text-[10px] font-semibold text-white">{unread > 99 ? "99+" : unread}</span>
            )}
          </NavLink>
        ))}
        <button
          onClick={() => setMore(!more)}
          aria-expanded={more}
          className={`flex h-16 flex-col items-center justify-center gap-0.5 text-xs ${more || !TABS.some(([t]) => t === loc.pathname) ? "text-blue-600 dark:text-blue-400" : "text-slate-600 dark:text-slate-400"}`}
        >
          <Icon name="menu" />
          More
        </button>
      </nav>

      {more && (
        <div className="fixed inset-0 z-20 bg-black/40 md:hidden" onClick={() => setMore(false)}>
          <div
            className="absolute inset-x-0 bottom-[calc(4rem+env(safe-area-inset-bottom))] max-h-[75vh] overflow-y-auto rounded-t-2xl bg-white p-4 shadow-xl dark:bg-slate-900"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-3 text-sm text-slate-500">
              {user.display_name} · {user.role}
            </div>
            <div className="grid grid-cols-2 gap-2">
              {links
                .filter(([to]) => !TABS.some(([t]) => t === to))
                .map(([to, label]) => (
                  <NavLink
                    key={to}
                    to={to}
                    onClick={() => setMore(false)}
                    className={({ isActive }) => `rounded-lg border px-3 py-3 text-sm ${isActive ? "border-blue-500 bg-blue-50 dark:bg-blue-950" : "border-slate-200 dark:border-slate-700"}`}
                  >
                    {label}
                  </NavLink>
                ))}
            </div>
            {canInstall && (
              <button onClick={install} className="mt-3 w-full rounded-lg bg-blue-600 py-3 text-sm font-medium text-white">
                Install app on this phone
              </button>
            )}
            {showIosHint && (
              <p className="mt-3 rounded-lg bg-slate-100 p-3 text-xs text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                To install on iPhone: tap the Share button in Safari, then “Add to Home Screen”.
              </p>
            )}
            <button onClick={logout} className="mt-3 w-full rounded-lg border border-slate-300 py-3 text-sm dark:border-slate-700">
              Log out
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
