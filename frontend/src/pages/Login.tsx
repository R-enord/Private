import { useState } from "react";
import { api, useApi } from "../lib/api";
import type { User } from "../lib/types";
import { Button, Card, ErrorBox } from "../components/ui";

export function Login({ onLogin }: { onLogin: (u: User) => void }) {
  const { data: status } = useApi<{ has_owner: boolean; setup_hint: string | null }>("/api/auth/status");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      const r = await api<{ user: User }>("/api/auth/login", { method: "POST", json: { email, password } });
      onLogin(r.user);
    } catch (err) {
      setError((err as Error).message);
    }
  };
  return (
    <div className="mx-auto mt-24 max-w-sm">
      <h1 className="mb-1 text-2xl font-bold">AI Intelligence Hub</h1>
      <p className="mb-4 text-sm text-slate-500">Private, local-only installation. Registration is disabled.</p>
      {status && !status.has_owner && (
        <div className="mb-3 rounded border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
          No owner account exists yet. {status.setup_hint}
        </div>
      )}
      <Card>
        <form onSubmit={submit} className="flex flex-col gap-3">
          <label className="text-sm">
            Email
            <input className="mt-1 w-full rounded border border-slate-300 px-2 py-1.5 dark:border-slate-700 dark:bg-slate-950" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
          </label>
          <label className="text-sm">
            Password
            <input className="mt-1 w-full rounded border border-slate-300 px-2 py-1.5 dark:border-slate-700 dark:bg-slate-950" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
          </label>
          <ErrorBox error={error} />
          <Button type="submit" variant="primary">
            Log in
          </Button>
        </form>
      </Card>
    </div>
  );
}
