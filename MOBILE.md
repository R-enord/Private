# Phone app

The phone app is the hub itself, installed as a **Progressive Web App (PWA)** from your phone's browser. It uses
the same data, account and security as the desktop UI. It needs no app store, costs nothing, and gets updates
automatically whenever you update the hub.

**How it works:** the hub on your computer collects in the background. Your phone connects to it over your home
Wi-Fi, or over your private Tailscale network when you are away. The phone never talks to GitHub, Hugging Face,
arXiv and so on; only your computer does.

## What the phone app does

| Feature | Status |
|---|---|
| Phone-sized layout: bottom tabs (Home, Radar, Search, Alerts, More), collapsible filters, scrollable tables, safe-area aware | complete (verified on emulated iPhone 15 and Pixel 7: no horizontal overflow on 9 pages) |
| Install to home screen (icon, full-screen, shortcuts to Radar/Search/Alerts) | complete. Android: "Install app" in More. iPhone: Safari → Share → "Add to Home Screen". **Needs https** for Android install; see below |
| Offline reading: pages you opened stay readable when the computer is asleep or you have no connection, with an "Offline" banner | complete (verified: service worker active, pages served offline). **Needs https** |
| QR pairing: no password typed on the phone. One-time code, 10 minutes, single use | complete |
| Device list, revoke a device, "log out all other devices" | complete |
| Live AI Radar updates, alerts badge while the app is open | complete (same SSE stream as desktop) |
| Push notifications while the app is closed | **not implemented** (see "What I could not implement") |

## Setup option A: same Wi-Fi (simplest)

1. In `.env` on the computer:
   ```
   HUB_NETWORK_MODE=lan
   HUB_BIND_HOST=0.0.0.0
   ```
   Restart (`make start`). The server prints the phone address, e.g. `phone access (lan mode): http://192.168.1.23:8000`.
2. On the computer, open **More → Devices → Create pairing code**.
3. On the phone (same Wi-Fi), scan the QR code with the camera, or open the address + `/pair` and type the code.

The app then works in the phone browser. Over plain `http`, phones do **not** allow installing as an app or
offline mode. For those, add https:

- **Local certificate (free, manual):** install [mkcert](https://github.com/FiloSottile/mkcert), then
  `mkcert -install && mkcert 192.168.1.23 mypc.local`, and set
  `HUB_TLS_CERT_FILE=/path/192.168.1.23+1.pem` and `HUB_TLS_KEY_FILE=/path/192.168.1.23+1-key.pem`. Then install
  mkcert's root CA on the phone:
  - iPhone: AirDrop `rootCA.pem`, install the profile, then Settings → General → About → Certificate Trust
    Settings.
  - Android: Settings → Security → Install certificate.

  Only do this on your own phone; that CA can vouch for any site.
- Or use option B, which gives https automatically.

Give the computer a fixed IP (a DHCP reservation in your router), or the phone address will change.

## Setup option B: Tailscale (works away from home, https included)

[Tailscale](https://tailscale.com) creates a private network between your own devices; nothing is exposed to the
internet. It has a free personal plan, but I could **not verify its current terms or limits from this build
environment**. Check them yourself before relying on it. It is an external account and service, so this is
your call.

1. Install Tailscale on the computer and the phone, and sign in to the same account.
2. On the computer: `tailscale serve --bg 8000`. This gives `https://<pc-name>.<tailnet>.ts.net`, proxied to the
   hub on loopback.
3. In `.env`, keep `HUB_NETWORK_MODE=local` and `HUB_BIND_HOST=127.0.0.1`, and add
   `HUB_EXTRA_HOSTS=["<pc-name>.<tailnet>.ts.net"]`. Restart.
4. Pair as above. The Devices page shows the `https://…ts.net` address, and install and offline both work.

Alternatively, `HUB_NETWORK_MODE=tailnet` with `HUB_BIND_HOST=<tailscale IP>` accepts only Tailscale addresses
(100.64.0.0/10, fd7a:115c:a1e0::/48), but then you still need your own certificate for https.

## Security

- **Off by default.** In `local` mode only the computer itself can connect; LAN clients get HTTP 403 (tested).
- **`lan` mode** accepts only RFC 1918 / link-local / IPv6 ULA clients. **Public IPs are always refused**, even if
  your router forwards the port by mistake (tested). Documentation and benchmark ranges are not treated as LAN.
  `HUB_ALLOWED_CLIENT_NETWORKS` lets you add an unusual LAN range explicitly.
- Registration stays disabled. A non-loopback bind outside lan/tailnet still requires
  `HUB_PUBLIC_ACCESS_APPROVED`.
- **Pairing codes:**
  - 10 characters from a 31-symbol alphabet (~49 bits), stored only as SHA-256.
  - Valid 10 minutes, single use; issuing a new code invalidates the old one.
  - Failures and successes are audit-logged.
- **Rate limits per client IP:**
  - login and pairing: 10/min;
  - API: 600/min;
  - plus the existing per-email login lockout.
- **Device sessions:**
  - each device gets its own 30-day session (`HUB_DEVICE_SESSION_DAYS`), with an HttpOnly, SameSite=Strict cookie
    (Secure over https);
  - revoking a device logs it out on its next request.
- **On the phone:**
  - the service worker never caches login, pairing, device or stream requests, or anything but GET;
  - cached API data is wiped on logout;
  - only display info (name, role; no token, no email) is kept so the app can open offline.
- CSRF header, Origin check, Host allowlist (your machine's own IPs and the configured hostnames are added
  automatically in lan/tailnet mode) and CSP all apply exactly as on desktop.

## What I could not implement, and why

| Request | Problem | What you can do instead |
|---|---|---|
| **Native App Store / Google Play app** | Publishing costs money. Per my knowledge (not re-verified in this session), Apple's developer program is a yearly fee and Google Play has a one-time registration fee; that breaks the zero-cost rule. A native iOS build also needs a Mac with Xcode, and free Apple-ID sideloading expires after a few days. | The installable PWA above (free, both platforms). |
| **Sideloaded Android APK** (free) | Possible by wrapping this same PWA (e.g. Capacitor or a Trusted Web Activity), but the build environment has no Android SDK (several GB), so I could not build or test one. It would add nothing functional over the installed PWA. | Ask, and I'll add an Android wrapper project for you to build in Android Studio. |
| **Push notifications when the app is closed** | Web Push is delivered through Google's (Android) or Apple's (iOS 16.4+, installed PWA only) push servers. That sends notification metadata to a third party, and I couldn't verify those services' terms here (your rule: unverified means disabled). It also requires https, and it can't be tested without a real phone. | In-app alerts with an unread badge work now. Push can be added as an opt-in once you approve the third-party delivery. |
| **Access from anywhere without extra software** | That needs the hub exposed to the internet (port forwarding or a public tunnel), which your rules forbid without explicit approval and a security review. | Tailscale (option B) keeps it private. |
| **Automatic https on the home network** | Browsers only trust certificates for real domain names from a public CA, or from a CA you install yourself. A LAN IP can't get a free trusted certificate automatically. | mkcert (option A) or Tailscale serve (option B). |
| **Testing on your actual phone** | No phone is connected to this build environment. Everything was verified with emulated devices (Playwright iPhone 15 and Pixel 7) over a real LAN address, plus https with a self-signed certificate. QR scanning with a real camera was not tested. | Try it with option A or B. If anything is off, tell me the phone model and browser. |
| **Collection running on the phone** | By design, collection runs on the computer. If the computer sleeps or is off, the phone shows saved data until it wakes. | Keep the computer awake (OS power settings) or run the hub on an always-on machine at home. |
