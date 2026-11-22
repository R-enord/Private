import { NavLink, Outlet } from "react-router-dom";
import { api, useApi } from "../lib/api";
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
  ["/watchlists", "Watchlists"],
  ["/sources", "Sources", "admin"],
  ["/ops", "Operations", "admin"],
  ["/settings", "Settings"],
];

const RANK: Record<string, number> = { viewer: 0, member: 1, moderator: 2, admin: 3, owner: 4 };

export function Layout({ user, onLogout }: { user: User; onLogout: () => void }) {
  const { data: alerts } = useApi<{ unread: number }>("/api/alerts?unread=true");
  const logout = async () => {
    await api("/api/auth/logout", { method: "POST" });
    onLogout();
  };
  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20 border-b border-slate-200 bg-white/90 backdrop-blur dark:border-slate-800 dark:bg-slate-950/90">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2">
          <span className="font-bold tracking-tight">AI Intelligence Hub</span>
          <span className="rounded bg-emerald-100 px-1.5 text-[10px] font-semibold uppercase text-emerald-900 dark:bg-emerald-950 dark:text-emerald-300" title="Hard zero-cost mode: paid endpoints are blocked in code">
            local · zero-cost
          </span>
          <nav className="flex flex-wrap gap-1 text-sm">
            {NAV.filter(([, , min]) => !min || (RANK[user.role] ?? 0) >= RANK[min]).map(([to, label]) => (
              <NavLink
                key={to}
                to={to}
                end={to === "/"}
                className={({ isActive }) => `rounded px-2 py-1 ${isActive ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900" : "hover:bg-slate-100 dark:hover:bg-slate-800"}`}
              >
                {label}
              </NavLink>
            ))}
            <NavLink to="/alerts" className={({ isActive }) => `rounded px-2 py-1 ${isActive ? "bg-slate-900 text-white dark:bg-slate-100 dark:text-slate-900" : "hover:bg-slate-100 dark:hover:bg-slate-800"}`}>
              Alerts{alerts && alerts.unread > 0 ? ` (${alerts.unread})` : ""}
            </NavLink>
          </nav>
          <div className="ml-auto flex items-center gap-2 text-sm text-slate-500">
            <span title={user.email}>
              {user.display_name} · {user.role}
            </span>
            <button onClick={logout} className="underline">
              log out
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-4">
        <Outlet />
      </main>
      <footer className="mx-auto max-w-7xl px-4 pb-6 text-xs text-slate-500">
        Data comes from the enabled sources listed under Sources, each with its own attribution. arXiv metadata is CC0 (not
        affiliated with or endorsed by arXiv). AI text is generated locally and labeled; verify against original links.
      </footer>
    </div>
  );
}
