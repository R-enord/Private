// Pair a phone with a one-time QR code, and manage signed-in devices.
import QRCode from "qrcode";
import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, useApi } from "../lib/api";
import { guessDeviceName } from "../lib/pwa";
import { relativeTime } from "../lib/format";
import type { User } from "../lib/types";
import { Button, Card, ErrorBox, SectionTitle } from "../components/ui";

interface PhoneAccess {
  network_mode: string;
  enabled: boolean;
  urls: string[];
  https: boolean;
  instructions: string | null;
}
interface Device {
  id: number;
  device_name: string;
  created_via: string;
  user_agent: string;
  client_ip: string;
  created_at: string;
  last_seen_at: string | null;
  expires_at: string;
  current: boolean;
}

export function Devices() {
  const { data: access } = useApi<PhoneAccess>("/api/devices/phone-access");
  const { data: devices, reload } = useApi<Device[]>("/api/devices");
  const [url, setUrl] = useState("");
  const [code, setCode] = useState<{ code: string; expires_at: string } | null>(null);
  const [qr, setQr] = useState<string | null>(null);
  const [left, setLeft] = useState(0);
  const [err, setErr] = useState<string | null>(null);
  const base = url || access?.urls[0] || window.location.origin;
  const pairUrl = code ? `${base}/pair?code=${code.code}` : "";

  useEffect(() => {
    if (!pairUrl) return;
    QRCode.toDataURL(pairUrl, { margin: 1, width: 240, errorCorrectionLevel: "M" }).then(setQr).catch(() => setQr(null));
  }, [pairUrl]);
  useEffect(() => {
    if (!code) return;
    const t = setInterval(() => setLeft(Math.max(0, Math.round((new Date(code.expires_at).getTime() - Date.now()) / 1000))), 1000);
    return () => clearInterval(t);
  }, [code]);

  const newCode = async () => {
    setErr(null);
    try {
      setCode(await api("/api/devices/pairing-codes", { method: "POST" }));
      setLeft(600);
    } catch (e) {
      setErr((e as Error).message);
    }
  };
  const revoke = async (id: number) => {
    await api(`/api/devices/${id}/revoke`, { method: "POST" });
    reload();
  };
  return (
    <div className="max-w-3xl">
      <h1 className="mb-1 text-xl font-bold">Devices & phone app</h1>
      <p className="mb-3 text-sm text-slate-500">
        The phone app is this hub, installed from your phone's browser (Android: “Install app”; iPhone: Share → “Add to Home
        Screen”). Your phone must be able to reach this computer — on the same Wi-Fi or through your private Tailscale network.
      </p>
      <ErrorBox error={err} />
      <SectionTitle>Phone access</SectionTitle>
      <Card>
        {access && !access.enabled ? (
          <div className="text-sm">
            <p className="font-medium">Phone access is off (network mode: {access.network_mode}).</p>
            <p className="mt-1 text-slate-600 dark:text-slate-400">{access.instructions}</p>
            <pre className="mt-2 overflow-x-auto rounded bg-slate-100 p-2 text-xs dark:bg-slate-950">{`# .env — same Wi-Fi (private networks only; public IPs are always refused)
HUB_NETWORK_MODE=lan
HUB_BIND_HOST=0.0.0.0
# then restart: hub start`}</pre>
          </div>
        ) : (
          <div className="text-sm">
            <p>
              Network mode <b>{access?.network_mode}</b>. Phone address{access && access.urls.length > 1 ? "es" : ""}:
            </p>
            <ul className="mt-1">
              {(access?.urls ?? []).map((u) => (
                <li key={u}>
                  <label className="flex items-center gap-2">
                    <input type="radio" name="url" checked={base === u} onChange={() => setUrl(u)} /> <code>{u}</code>
                  </label>
                </li>
              ))}
            </ul>
            {access && !access.https && (
              <p className="mt-2 text-xs text-amber-700 dark:text-amber-300">
                Plain http on the LAN works in the phone browser, but installing as an app and offline mode need https (see
                MOBILE.md: Tailscale or a local certificate).
              </p>
            )}
          </div>
        )}
      </Card>

      <SectionTitle>Pair a phone</SectionTitle>
      <Card>
        <p className="text-sm text-slate-600 dark:text-slate-400">
          Creates a one-time code (valid 10 minutes, single use). Scan the QR code with the phone camera, or open the address on
          the phone and type the code. No password is typed on the phone.
        </p>
        <div className="mt-2">
          <Button variant="primary" onClick={newCode}>{code ? "New code" : "Create pairing code"}</Button>
        </div>
        {code && (
          <div className="mt-3 flex flex-wrap items-center gap-4">
            {qr && left > 0 && <img src={qr} alt="Pairing QR code" width={240} height={240} className="rounded bg-white p-1" />}
            <div className="text-sm">
              <div className="font-mono text-2xl tracking-widest">{left > 0 ? code.code : "expired"}</div>
              <div className="text-slate-500">{left > 0 ? `expires in ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}` : "create a new code"}</div>
              <div className="mt-1 break-all text-xs text-slate-500">{base}/pair</div>
            </div>
          </div>
        )}
      </Card>

      <SectionTitle right={<Button onClick={async () => { await api("/api/devices/revoke-others", { method: "POST" }); reload(); }}>Log out all other devices</Button>}>
        Signed-in devices
      </SectionTitle>
      <div className="flex flex-col gap-2">
        {(devices ?? []).map((d) => (
          <Card key={d.id} className="!p-3">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <b>{d.device_name}</b>
              {d.current && <span className="rounded bg-blue-100 px-1 text-xs text-blue-900 dark:bg-blue-950 dark:text-blue-200">this device</span>}
              <span className="text-xs text-slate-500">
                via {d.created_via} · {d.client_ip || "?"} · last seen {relativeTime(d.last_seen_at)} · expires {relativeTime(d.expires_at)}
              </span>
              {!d.current && <span className="ml-auto"><Button variant="danger" onClick={() => revoke(d.id)}>Revoke</Button></span>}
            </div>
            <div className="truncate text-xs text-slate-500">{d.user_agent}</div>
          </Card>
        ))}
      </div>
    </div>
  );
}

/** Opened on the phone (not logged in yet): exchanges the one-time code for this device's own session. */
export function Pair({ onPaired }: { onPaired: (u: User) => void }) {
  const [params] = useSearchParams();
  const nav = useNavigate();
  const [code, setCode] = useState(params.get("code") ?? "");
  const [name, setName] = useState(guessDeviceName(navigator.userAgent));
  const [err, setErr] = useState<string | null>(null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    try {
      const r = await api<{ user: User }>("/api/auth/pair", { method: "POST", json: { code, device_name: name } });
      onPaired(r.user);
      nav("/", { replace: true });
    } catch (ex) {
      setErr((ex as Error).message);
    }
  };
  return (
    <div className="mx-auto mt-16 max-w-sm px-4">
      <h1 className="mb-1 text-2xl font-bold">Connect this phone</h1>
      <p className="mb-4 text-sm text-slate-500">Enter the code shown on your computer under Devices → Pair a phone.</p>
      <Card>
        <form onSubmit={submit} className="flex flex-col gap-3">
          <label className="text-sm">
            Pairing code
            <input className="mt-1 w-full rounded border border-slate-300 px-2 py-2 font-mono text-lg uppercase tracking-widest dark:border-slate-700 dark:bg-slate-950" value={code} onChange={(e) => setCode(e.target.value)} autoComplete="one-time-code" required />
          </label>
          <label className="text-sm">
            Device name
            <input className="mt-1 w-full rounded border border-slate-300 px-2 py-2 dark:border-slate-700 dark:bg-slate-950" value={name} onChange={(e) => setName(e.target.value)} maxLength={100} />
          </label>
          <ErrorBox error={err} />
          <Button type="submit" variant="primary">Connect</Button>
        </form>
      </Card>
    </div>
  );
}
