import { useEffect, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { ApiError, api, setUnauthorizedHandler } from "./lib/api";
import type { User } from "./lib/types";
import { Layout } from "./components/Layout";
import { ArticleEditor, ArticleView, Community } from "./pages/Community";
import { Devices, Pair } from "./pages/Devices";
import { Feed } from "./pages/Feed";
import { Media } from "./pages/Media";
import { Home } from "./pages/Home";
import { ItemDetail } from "./pages/ItemDetail";
import { Login } from "./pages/Login";
import { Alerts, Opportunities, Reports, Settings, StackExplorer, Timeline, Watchlists } from "./pages/Misc";
import { Ops } from "./pages/Ops";
import { Radar } from "./pages/Radar";
import { Search } from "./pages/Search";
import { Sources } from "./pages/Sources";

// Display info only (no token, no email) so the installed app can open offline and show saved pages.
// Authorization is always enforced by the server; a revoked device gets 401 as soon as it is back online.
const REMEMBER_KEY = "hub.user";
function remembered(): User | null {
  try {
    return JSON.parse(localStorage.getItem(REMEMBER_KEY) ?? "null") as User | null;
  } catch {
    return null;
  }
}
function remember(u: User | null) {
  if (u) localStorage.setItem(REMEMBER_KEY, JSON.stringify({ id: u.id, handle: u.handle, display_name: u.display_name, role: u.role, email: "" }));
  else localStorage.removeItem(REMEMBER_KEY);
}

export default function App() {
  const [user, setUserState] = useState<User | null | undefined>(undefined);
  const setUser = (u: User | null) => {
    remember(u);
    setUserState(u);
  };
  useEffect(() => {
    setUnauthorizedHandler(() => {
      remember(null);
      setUserState(null);
    });
    api<{ user: User }>("/api/auth/me")
      .then((r) => {
        remember(r.user);
        setUserState(r.user);
      })
      .catch((e: unknown) => {
        // Network failure (offline / hub asleep): keep using the app with on-device data.
        const offline = e instanceof ApiError && (e.status === 0 || e.status === 503);
        if (!offline) remember(null);
        setUserState(offline ? remembered() : null);
      });
  }, []);
  if (user === undefined) return <p className="p-6 text-slate-500">Loading…</p>;
  if (user === null)
    return (
      <BrowserRouter>
        <Routes>
          <Route path="pair" element={<Pair onPaired={setUser} />} />
          <Route path="*" element={<Login onLogin={setUser} />} />
        </Routes>
      </BrowserRouter>
    );
  return (
    <BrowserRouter>
      <Routes>
        <Route path="pair" element={<Navigate to="/" replace />} />
        <Route element={<Layout user={user} onLogout={() => setUser(null)} />}>
          <Route index element={<Home />} />
          <Route path="radar" element={<Radar />} />
          <Route path="feed" element={<Feed />} />
          <Route path="search" element={<Search />} />
          <Route path="items/:id" element={<ItemDetail user={user} />} />
          <Route path="sources" element={<Sources />} />
          <Route path="ops" element={<Ops />} />
          <Route path="settings" element={<Settings user={user} />} />
          <Route path="watchlists" element={<Watchlists />} />
          <Route path="alerts" element={<Alerts />} />
          <Route path="reports" element={<Reports user={user} />} />
          <Route path="opportunities" element={<Opportunities />} />
          <Route path="timeline" element={<Timeline />} />
          <Route path="stack" element={<StackExplorer />} />
          <Route path="community" element={<Community />} />
          <Route path="community/new" element={<ArticleEditor />} />
          <Route path="community/edit/:id" element={<ArticleEditor />} />
          <Route path="community/:slug" element={<ArticleView />} />
          <Route path="devices" element={<Devices />} />
          <Route path="media" element={<Media user={user} />} />
          <Route path="*" element={<p>Not found.</p>} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
