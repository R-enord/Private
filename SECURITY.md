# Security

## Threat model (single-owner local install)

| Threat | Control |
|---|---|
| Malicious web page attacks the local server (CSRF, DNS rebinding) | Host-header allowlist (`TrustedHostMiddleware`). State-changing `/api` calls require `X-Hub-Request: 1`, which forces a CORS preflight that is never granted, plus a same-origin `Origin`. Session cookie is `HttpOnly; SameSite=Strict`. No CORS. |
| Other users or devices on the network | Binds `127.0.0.1`. A non-loopback bind or registration refuses to start without `HUB_PUBLIC_ACCESS_APPROVED=true`. Docker publishes on loopback only. |
| Untrusted fetched content (pages, READMEs, feeds, MCP descriptions, comments) | Stored as data, never executed. Feed and summary text is stripped to plain text (nh3 with no tags). The UI renders source text as text. Links are emitted only if `http(s)` with no embedded credentials. Raw payloads are shown as escaped JSON. No discovered repo, agent, MCP server or installer is ever run or installed. |
| Prompt injection into AI tasks | Source text is wrapped as `<source_material>` data with angle brackets neutralized. The system prompt states retrieved text is evidence, never instructions. The model has **no tools**, and its output is schema-constrained. A grounding validator drops sentences with facts absent from the source, plus URLs, commands and install steps. Output is rendered as text and labeled AI-generated. Tested with an adversarial feed and adversarial model output. |
| SSRF via feeds or redirects | Per-source host allowlist. Every redirect hop is re-checked. Private, loopback, link-local and metadata IPs and internal hostnames are refused. Scheme must be https (plain http only for local Ollama). Response size cap of 15 MB. |
| Paid or metered calls (cost) | Hard zero-cost guard before I/O (see API_INTEGRATIONS.md). |
| Credential theft | scrypt password hashes (n=2¹⁵, r=8, p=1). Session tokens are 256-bit random values stored only as a SHA-256 hash. Login lockout after 5 failures per 15 minutes. Optional API tokens are read from env, sent only to their own host, and never logged or stored. `.env` is git-ignored. |
| XSS via community content or reports | Markdown rendered with raw HTML disabled, then nh3 allowlist sanitization (`javascript:` and `data:` URLs dropped, `rel="noopener noreferrer nofollow"`). The UI injects HTML only through `serverSanitizedHtml()`, enforced by an ESLint rule. Strict CSP: `script-src 'self'`, `object-src 'none'`, `frame-ancestors 'none'`. |
| Privilege misuse | Central permission table and ownership checks (`hub/security/auth.py`). Other users' private resources return 404. Moderation state cannot be overridden by authors. Admin actions are written to `audit_log`. |
| Supply chain | Locked dependencies (`uv.lock`, `pnpm-lock.yaml`). pnpm v10 blocks install scripts by default. No auto-updates. |
| "Popular = safe" | Stars and likes are not treated as proof of security or quality; adoption is discounted for inorganic-looking patterns and every score is explained. |

## Phone access (lan / tailnet modes)

Off by default. When enabled:
- Only private (RFC 1918, link-local, ULA) or Tailscale client addresses are accepted, and public IPs get 403.
- Pairing codes are one-time, short-lived and hashed.
- Login and pairing are rate-limited per IP.
- Each device has its own revocable session.
- The service worker never caches auth, pairing, device or stream responses, and cached data is wiped on logout.

Details are in MOBILE.md. Over plain http on a LAN, traffic is unencrypted on your Wi-Fi; prefer https via
mkcert or Tailscale.

## Secrets

- Put secrets in `.env` (git-ignored) or the process environment. Never commit them, paste them into issues or
  chat, or put them in screenshots.
- The DB password reaches `pg_dump`/`pg_restore` via `PGPASSWORD` only. The request log redacts query
  parameters named like `token`, `key`, `secret`, `password` or `auth`.

## Verified by tests

`tests/test_http_and_cost_guard.py` covers:
- paid hosts, SSRF targets, schemes and redirects;
- auth header scoping;
- size cap and rate limits.

`tests/test_api.py` covers:
- auth required, registration disabled, cookie flags, lockout;
- CSRF and Origin checks, DNS-rebinding Host rejection, CSP headers;
- role and ownership authorization.

`tests/test_ingest.py::test_malicious_feed_is_neutralized` and `tests/test_ai.py` cover injection and
grounding. `tests/test_community.py` covers XSS payloads, ownership and moderation.

## Known limitations

- There is no rate limiting on API endpoints beyond login lockout. That is acceptable for loopback single-user
  use, but **required before public exposure**.
- Session revocation is per session; there is no "log out everywhere" UI yet.
- The 1.5B local model can still produce wrong but grounded-looking statements using numbers that do appear in
  the source. Summaries are labeled and link to the source text shown on the same page.
- `hub restore` is destructive by design (it requires `--yes`).

## Before any public deployment (do not skip; requires explicit owner approval)

1. External security review of auth, authorization and sanitization; dependency audit (`pip-audit`,
   `pnpm audit`).
2. TLS reverse proxy (Caddy or nginx) and `secure` cookies (automatic over https). Set `HUB_ALLOWED_HOSTS` to
   the public hostname.
3. API rate limiting and abuse controls; registration with email verification; CAPTCHA-free alternatives
   such as invite codes.
4. Separate DB role with least privilege; backups encrypted and stored off-machine.
5. Review every source's terms for *public redistribution* (current terms were reviewed for personal use).
   Some, like arXiv e-prints and site excerpts, have stricter rules for public display.
6. Privacy policy, content policy, moderation staffing, and data export and deletion for users.
7. Only then set `HUB_PUBLIC_ACCESS_APPROVED=true` and the registration flag.

## Reporting

This is a private project. If you find a security issue, note it in a private issue or contact the owner
directly.
