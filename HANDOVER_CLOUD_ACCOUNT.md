# Handoff: Subtitld Cloud account API + portal routes

**For:** the Subtitld Cloud session (`/home/jonata/Projetos/subtitld_online_addons`)
**From:** the desktop session (`/home/jonata/Projetos/subtitld`)
**Status:** desktop side DONE and shipped. `GET /api/v1/account` already
returns `email` + `balance` (confirmed working — desktop shows the real
balance). The items below are what's still needed to light up the rest of
the new dashboard.

---

## What the desktop now shows

The "Subtitld Cloud" tab (Global settings) is a full **account dashboard**
(not the old status-text line). Connected state renders:

- identity header — avatar initial, email, "CONNECTED" status;
- a **balance ring** (donut with a fill fraction) + **Top up balance** /
  **Open portal** buttons;
- a **recent-usage table** (per-job: engine icon, filename, minutes, cost);
- a 🔑 "Change API key" and ⟳ "Refresh" icon action.

Other states: **loading** (animated, during fetch), **not-connected**
(API-key entry + Connect + "Create an account"), **low-balance** (red
treatment), and **unreachable** (error card + Retry).

All of it is driven by one endpoint + three portal URLs.

---

## 1. `GET /api/v1/account` — expand the response

```
GET /api/v1/account
Authorization: Bearer <api_key>      # same APIKeyBearer() as every other route
```

### Response (200) — JSON object. Every field OPTIONAL; the desktop reads
each with `.get()` and degrades (hides the usage band when absent, shows
"—" for a missing balance, uses a default ring fill, etc.). Ship a subset,
grow over time.

| key             | type   | used for | notes |
|-----------------|--------|----------|-------|
| `email`         | string | avatar initial + identity line | ✅ already returned |
| `balance`       | number | ring center value `$X.XX` | **major units** (e.g. `5.59`), NOT cents · ✅ already returned |
| `currency`      | string | ring unit label | ISO code, e.g. `"USD"` |
| `key_name`      | string | **badge next to the email** | the label the user gave the API key in use (e.g. `"Laptop"`, `"CI"`). Omit → badge hidden |
| `plan`          | string | (reserved) | tier/label, free-form |
| `credits`       | number | ring value when no `balance` | prepaid-unit accounts |
| `ring_fraction` | number | ring arc fill, **0..1** | e.g. `balance ÷ a top-up target`. Omit → desktop uses a sensible default fill |
| `low_balance`   | bool   | red "LOW BALANCE" treatment | omit → desktop infers it (`balance < 1.0`) |
| `usage`         | array  | recent-usage table | each item: see below. Omit/empty → table hidden |

`usage[]` item shape:

| field     | type   | shown as | notes |
|-----------|--------|----------|-------|
| `file`    | string | filename column | source media name |
| `kind`    | string | engine icon | starts with `"Transcription"` → wave mark; anything else (e.g. `"Dubbing · ES"`) → dubbing mark |
| `minutes` | number | duration column (`42 min`) | |
| `cost`    | number | cost column (`−$0.63`) | **major units**, NOT cents |

### ⚠️ Cents conversion (still applies)

`billing.models.Profile.balance_cents` is a `BigIntegerField` in **cents**.
Convert at the boundary — `balance` and every `usage[].cost` must be major
units:

```python
balance = round(profile.balance_cents / 100, 2)   # 559 -> 5.59
```

Don't send raw cents, or the user sees `$559.00`.

### Suggested implementation (Django Ninja, matches your conventions)

`accounts_router` is already mounted at `/` in `cloudapp/api.py`; auth is
inherited from the `NinjaAPI` instance.

```python
# accounts/api.py
from ninja import Router, Schema

router = Router()

class UsageItem(Schema):
    file: str
    kind: str            # "Transcription" | "Dubbing · ES" | ...
    minutes: float
    cost: float          # major units

class AccountOut(Schema):
    email: str
    balance: float | None = None
    currency: str | None = "USD"
    key_name: str | None = None       # label of the API key in this request
    plan: str | None = None
    credits: float | None = None
    ring_fraction: float | None = None
    low_balance: bool | None = None
    usage: list[UsageItem] = []

@router.get("/account", response=AccountOut)
def account(request):
    api_key = request.auth                      # whatever APIKeyBearer returns
    user = api_key.user                         # adjust to your model
    profile = user.profile
    recent = (LedgerEntry.objects            # last ~5 debits, newest first
              .filter(profile=profile, amount_cents__lt=0)
              .order_by("-created_at")[:5])
    return AccountOut(
        email=user.email,
        balance=round(profile.balance_cents / 100, 2),
        currency="USD",
        key_name=api_key.name,                  # the name the user gave the key
        usage=[UsageItem(file=e.source_name, kind=e.kind_label,
                         minutes=e.minutes, cost=round(-e.amount_cents / 100, 2))
               for e in recent],
    )
```

(Adjust field names to the real `LedgerEntry`/`Job` schema. If you don't
track per-job `minutes`/`source_name` yet, omit `usage` entirely — the
desktop just hides the table.)

### Errors the desktop already handles

| status        | desktop behavior |
|---------------|------------------|
| 401 / 403     | drops to the not-connected card, "Invalid API key" inline error |
| network / DNS | **error card** with a Retry button ("Couldn't reach Subtitld Cloud") |
| 404 / 5xx     | error card with Retry (reason-specific message) |

`APIKeyBearer()` already 401s on a bad key, so that path is free.

---

## 2. Confirm / implement three portal routes

The desktop opens these in the system browser. The paths are my best guess
from your app layout (`accounts`, `billing`, `dashboard` apps) — **please
confirm they exist, or tell me the real ones** and I'll update the desktop
(`subtitld_cloud_shared.read_dashboard_url` / `read_topup_url` /
`read_signup_url`).

| desktop action      | URL it opens (relative to base) | button |
|---------------------|---------------------------------|--------|
| Open portal         | `/dashboard/`                   | dashboard "Open portal" |
| Top up balance      | `/dashboard/billing/`           | dashboard "Top up balance" |
| Create an account   | `/accounts/signup/`             | not-connected card link |

If any differ, the desktop change is a one-line edit per helper.

---

## 3. Base URL — no longer user-configurable

The desktop dropped the "Server URL" field. Production is hardcoded to
`https://cloud.subtitld.org`; developers override with the
`SUBTITLD_CLOUD_BASE_URL` env var (set to `https://draft-cloud.subtitld.org`
in dev). Nothing for you to do here — just noting that whatever host serves
`/api/v1/` also needs to serve the portal routes above, since the desktop
derives both from the same base.

---

## Desktop side — what consumes this (reference, do not edit)

- Endpoint + URL helpers: `subtitld_cloud_shared.fetch_account` /
  `read_dashboard_url` / `read_topup_url` / `read_signup_url` /
  `read_base_url`
  → `src/subtitld/modules/addons/builtin/subtitld_cloud_shared.py`
- Dashboard UI (states, ring, usage table, error/retry):
  `CloudDashboardPanel`
  → `src/subtitld/interface/cloud_dashboard.py`

Auth + base URL come from the shared cloud config slot (one API key, read
by every cloud-backed provider) — the same Bearer token the catalog/jobs
routes already accept. No new auth work.

---

## TL;DR for the cloud chat

1. **Extend `GET /api/v1/account`** with `currency`, `key_name`,
   `ring_fraction`, `low_balance`, and a `usage[]` array (`file`, `kind`,
   `minutes`, `cost`) — all optional, all major units. `email`/`balance`
   already work. (`key_name` = the label of the API key the request
   authenticated with — shown as a badge next to the email.)
2. **Confirm the 3 portal routes** (`/dashboard/`, `/dashboard/billing/`,
   `/accounts/signup/`) or send the correct paths.
