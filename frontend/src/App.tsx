import { useEffect, useState } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { api, setUnauthorizedHandler } from "./lib/api";
import type { User } from "./lib/types";
import { Layout } from "./components/Layout";
import { Feed } from "./pages/Feed";
import { Home } from "./pages/Home";
import { ItemDetail } from "./pages/ItemDetail";
import { Login } from "./pages/Login";
import { Alerts, Opportunities, Reports, Settings, StackExplorer, Timeline, Watchlists } from "./pages/Misc";
import { Ops } from "./pages/Ops";
import { Radar } from "./pages/Radar";
import { Search } from "./pages/Search";
import { Sources } from "./pages/Sources";

export default function App() {
  const [user, setUser] = useState<User | null | undefined>(undefined);
  useEffect(() => {
    setUnauthorizedHandler(() => setUser(null));
    api<{ user: User }>("/api/auth/me")
      .then((r) => setUser(r.user))
      .catch(() => setUser(null));
  }, []);
  if (user === undefined) return <p className="p-6 text-slate-500">Loading…</p>;
  if (user === null) return <Login onLogin={setUser} />;
  return (
    <BrowserRouter>
      <Routes>
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
          <Route path="*" element={<p>Not found.</p>} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
