# Decision — finish wiring per-user session auth

**Status:** Proposed (2026-04-29)
**Owner:** Fredrik Drakelid
**Relates to:** `analysis.md` §2.1, §2.2; `prd.md` §M0.6 ("email/magic-link sufficient for internal use")

---

## Context

The audit (analysis.md §2.1) claimed there was "no authentication on any non-auth route." After spot-checking the live code, the picture is more nuanced:

- ✅ `services/api/src/api/auth.py` already has full session-cookie machinery: HMAC-SHA256 signed tokens (`create_session_token` / `verify_session_token`), `set_session_cookie` with `HttpOnly` + `SameSite=lax` + configurable `Secure`, plus `current_user_email` and `current_admin_email` Depends helpers.
- ✅ `verify-magic-link` calls `set_session_cookie` and `request-magic-link` already gates the in-band token disclosure to non-production: `token = issued.token if get_settings().app_env != "production" else None` (auth.py:304).
- ✅ Admin routes gate via `Depends(current_admin_email)` against `admin_emails` setting.
- ✅ A subset of user-scoped routes gate via `Depends(current_user_email)`: clob credentials, provider credentials, journal, billing.
- ❌ **Ten user-scoped routes are still wide open** (see §1 below).
- ❌ **Every gated route discards the authenticated email and falls through to `ensure_demo_user`**, so all authenticated users share the demo user's data (see §2 below).
- ❌ **Magic-link token still leaks in `app_env != "production"`**, including staging (see §3 below).
- ❌ **No outbound mailer in production**, so the magic-link flow is currently undeliverable to an end user (see §4 below).

Items #1 and #2 in the analysis.md 🔴 list collapse to: "finish what auth.py started." This doc scopes that work.

---

## 1. Routes still missing `Depends(current_user_email)`

Confirmed by reading `services/api/src/api/main.py` line by line:

| Method | Path | Line | Sensitivity |
|---|---|---|---|
| GET | /v1/push-preferences | 1202 | user prefs |
| PUT | /v1/push-preferences | 1294 | user prefs |
| GET | /v1/privacy-preferences | 1220 | user prefs |
| PUT | /v1/privacy-preferences | 1275 | user prefs |
| GET | /v1/polymarket-address | 1328 | wallet read |
| **PUT** | **/v1/polymarket-address** | **1417** | **wallet write — critical** |
| GET | /v1/tuning-profile | 1791 | model output config |
| **PUT** | **/v1/tuning-profile** | **1806** | **model output config — critical** |
| GET | /v1/paper-trading | 2136 | trade-mode read |
| **PUT** | **/v1/paper-trading** | **2151** | **trade-mode write — critical** |

Routes that warrant moving to `current_admin_email` (operational endpoints, not user-facing):

| Method | Path | Line | Note |
|---|---|---|---|
| GET | /v1/beta/invites | 369 | currently public; reveals invite list |
| POST | /v1/beta/invites | 400 | currently public; allows invite creation |
| GET | /v1/privacy/audit | 1259 | already aggregate-only by design |
| GET | /v1/eol-monitor | 2174 | internal audit feed |
| GET | /v1/decision-time-metrics | 2200 | internal audit feed |
| GET | /v1/source-failure-audit | 2225 | internal audit feed |

Public routes (intentionally ungated, leave as-is):

- `/healthz`, `/v1/status` — health
- `/v1/error-reports` — rate-limited + size-capped + dedup'd (resolved 2026-04-29)
- `/v1/beta/feedback` — intentional anonymous capture
- `/v1/markets/*`, `/v1/markets/{id}/*` (asof, model, history, concentration, smart-money, social-features), `/v1/quotes/{id}/asof`, `/v1/features/{id}/asof`, `/v1/event-time/{id}/asof`, `/v1/regime`, `/v1/signals`, `/v1/external-events`, `/v1/source-health`, `/v1/security/rotation-audit`, `/v1/regulatory-events`, `/v1/cost-watch`, `/v1/onchain-metrics`, `/v1/drift-monitor`, `/v1/backtest/walk-forward`, `/v1/privacy/dp-aggregates` — public read; PIT-correct or aggregate-only
- `/v1/waitlist` POST — self-service
- `/v1/billing/webhook` — Stripe-signed (resolved 2026-04-29)

---

## 2. Per-user data isolation

Every gated user-scoped route discards the authenticated email:

```python
# main.py:1387 (clob credentials, current shape)
async def update_polymarket_clob_credentials(
    payload: ClobCredentialRequest,
    user_email: str = Depends(auth_q.current_user_email),
    pg: Any = Depends(get_pg),
) -> dict[str, Any]:
    del user_email                                          # <-- discarded
    settings = get_settings()
    status = await clob_credentials_q.update_credentials(   # <-- still uses ensure_demo_user
        pool=pg, settings=settings, payload=...
    )
```

`ensure_demo_user` lives in `services/api/src/api/users.py` and currently returns the same `users.id` for everyone. Modules that call it: `clob_credentials.py:42,71,130`, `journal.py:94,142,214`, `polymarket_account.py:71,111`, `privacy_prefs.py:27,51`, `push_prefs.py:59,90`, `provider_credentials.py:130,178,221,255`, `tuning.py:101,137`. Total: **17 callsites across 7 modules.**

### Proposal

Replace `ensure_demo_user(pool, settings)` with a paired pattern:

```python
# api/users.py
async def ensure_user_by_email(pool: Pool, *, email: str, display_name: str | None = None) -> UUID:
    """Idempotent upsert. Used by every gated user-scoped route."""
    cleaned = email.strip().lower()
    if not cleaned or "@" not in cleaned:
        raise ValueError("email is required")
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO users (email, display_name)
            VALUES ($1, $2)
            ON CONFLICT (email) DO UPDATE
            SET display_name = COALESCE(EXCLUDED.display_name, users.display_name)
            RETURNING id
            """,
            cleaned,
            display_name,
        )
    assert row is not None
    return row["id"]


async def ensure_demo_user(pool: Pool, settings: Settings) -> UUID:
    """Test-only fallback. Real routes use ensure_user_by_email instead."""
    return await ensure_user_by_email(
        pool,
        email=settings.journal_demo_user_email,
        display_name=settings.journal_demo_user_name,
    )
```

Then every module function takes `email: str` and calls `ensure_user_by_email(pool, email=email)` — keeping the existing `ensure_demo_user` for tests that haven't been migrated. Routes pass the authenticated email through:

```python
# main.py:1387 (proposed shape)
async def update_polymarket_clob_credentials(
    payload: ClobCredentialRequest,
    user_email: str = Depends(auth_q.current_user_email),
    pg: Any = Depends(get_pg),
) -> dict[str, Any]:
    settings = get_settings()
    status = await clob_credentials_q.update_credentials(
        pool=pg, settings=settings, email=user_email, payload=...
    )
```

This is mechanical: 17 callsites + 7 module signature changes + ~20 tests.

### Migration order

1. Add `ensure_user_by_email` alongside `ensure_demo_user` (no breaking change).
2. Convert one module at a time (clob_credentials → tests → push → privacy → tuning → polymarket_account → journal → provider_credentials). Each conversion: function signature gets an `email: str`, route passes it through, tests construct an explicit email (`"demo@polypredictor.local"` keeps test data stable).
3. Once all 7 modules are converted, leave `ensure_demo_user` only for two purposes: the lifespan-attached `journal_auto_sync` service (which is system-level, not a user request) and any test that hasn't been migrated.

---

## 3. Magic-link in-band disclosure

Current gate at `auth.py:304`:

```python
token = issued.token if get_settings().app_env != "production" else None
```

This still leaks the token in **staging** because `app_env` will be `"staging"` there (per `render.yaml:11`). A staging deploy should not return tokens to the caller — that breaks the audit log and lets anyone with the magic-link request URL hijack the session of a stranger who happens to request a link at the same time.

### Proposal

Add an explicit setting:

```python
# settings.py
auth_magic_link_in_band: bool = False  # only true in dev / local
```

With `Settings(app_env="development")` the dev `.env.example` sets it to `True`. In staging and production it stays `False` and the response model returns `MagicLinkResponse(message="Magic link generated", token=None)`.

When `auth_magic_link_in_band=False` and no mailer is wired, log the token at `INFO` with structured fields so an operator can find it in the log stream during the email-delivery interim:

```python
log.info("auth.magic_link_issued", email=cleaned_email, token=token, expires_at=expires_at.isoformat())
```

This is **strictly worse than email** for production but unblocks staging. The PRD calls magic links sufficient for internal use; emailing comes next.

---

## 4. Outbound mailer

Out of scope for this decision doc. Two paths:

- **A.** Stub the mailer behind a `MailerProtocol` interface, default to `LoggingMailer` (writes to structured log), add `SendgridMailer` / `ResendMailer` etc. behind a single `MAILER_PROVIDER` env var. Pre-launch.
- **B.** Defer to v1 launch and rely on log-stream extraction during closed beta. Acceptable per PRD §M0.6 "internal use."

Pick A for v1 launch. Track separately.

---

## 5. Tests

Per-module test changes:

- Each test that previously called `ensure_demo_user` and asserted state should now construct an explicit email (e.g. `"alice@example.com"`) and assert that user A's data does not bleed into user B's. The data-isolation regression test is the single most valuable new test.
- Add an integration test for each formerly-ungated route: anonymous request → 401, missing cookie → 401, expired cookie → 401, valid cookie → 200.
- Add a test for the proposed `ensure_user_by_email` invariant: re-call with same email returns same `id`.

Estimated new tests: ~25 (10 routes × 2 cases + 5 module-level invariants). Most existing tests stay as-is once `ensure_demo_user` continues to work via the proposed wrapper.

---

## 6. Out of scope (deferred)

- **Per-route rate limiting** beyond `/v1/error-reports`. The audit's §3.1 about in-memory state breaking horizontal scaling applies to the existing `_MinuteRateLimiter` and `_StackDedupe`; that's a Redis-migration item, not an auth item.
- **Session refresh / sliding expiry.** Current TTL is 30 days fixed (`auth_session_ttl_seconds: int = 60 * 60 * 24 * 30`); good enough for v1.
- **Logout endpoint.** `set-cookie max_age=0` on a `/v1/auth/logout` is trivial but not blocking.
- **CSRF protection.** SameSite=lax on the session cookie + `allow_credentials=True` CORS already covers the dashboard origin. We're not yet supporting cross-origin form posts, so CSRF tokens can wait.

---

## 7. Risks

- **Test churn.** 17 callsite changes + ~25 new tests. Mechanical but touches 7 modules.
- **Frontend already assumes auth.** The new dashboard's mutations call PUT /v1/push-preferences, /v1/privacy-preferences, /v1/polymarket-address, /v1/tuning-profile, /v1/paper-trading without any auth handshake (the dashboard doesn't call `/v1/auth/request-magic-link` anywhere). Once these endpoints start requiring a session cookie, the dashboard will start getting 401s. Sequence the rollout: implement the magic-link UI flow in `apps/web` **before** flipping the gates on these routes, OR ship behind a feature flag.
- **Demo-user data orphaned.** Existing rows in `users` / `user_api_keys_encrypted` / etc. tied to `demo@polypredictor.local` keep working. New users get fresh rows. No migration needed.
- **Operator impact.** Whoever was using the operator console (legacy `/console` route, deleted) without auth now needs an admin email in `admin_emails` and a magic-link login. Add a dev `.env.example` entry for `admin_emails=admin@example.com` and document.

---

## 8. Decision request

**Approve A, B, or both:**

- **A.** Land the route-gating + per-user isolation work (sections 1–2, 5). Targets: 17 callsite changes, 7 module signature changes, 10 new `Depends(current_user_email)` insertions, 6 new `Depends(current_admin_email)` insertions, ~25 new tests. Rough estimate: 1–2 sessions.
- **B.** Land the magic-link disclosure tightening (section 3). Targets: 1 new setting, 1 boolean flip, 1 structured log line, 1 test. Rough estimate: <1 session.

Recommend doing **B first** (smaller, no frontend coupling) and **A second** behind a feature flag with a follow-up frontend pass to add the magic-link login UI.

The mailer (section 4) is a separate workitem.
