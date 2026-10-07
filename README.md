# Route 53 Console Clone

A Next.js and FastAPI application that recreates the key Route 53 hosted zone and DNS record workflows. It stores data in SQLite and **does not serve DNS queries or connect to AWS**. The UI follows the assignment's supplied screenshots, including the hosted zones table, zone detail, quick record form, and edit drawer.

## Features

- Self-service signup for Personal and Organization accounts, login by username or email, logout, and a seven-day HTTP-only cookie session.
- Private workspaces: hosted zones, records, imports, exports, and mock service data are scoped to the authenticated account.
- Mock IAM identity/policy management, account settings, organizations with simulated member accounts, and account-specific billing usage.
- Public and private hosted zones with search, pagination, create, view, comment edit, and delete.
- A, AAAA, CNAME, TXT, MX, NS, PTR, SRV, and CAA records with validation and persistent CRUD.
- Route 53-style tables, filters, notifications, confirmation dialogs, navigation, and placeholder sections.
- BIND zone-file import; JSON and BIND export; dark mode; keyboard shortcuts; bulk record deletion.
- Default NS and SOA records for public zones. These are displayed but protected from editing/deletion.

The app follows AWS's hosted zone edit behavior: the description/comment can be edited, but the name and public/private type cannot be changed after creation. It supports simple routing and non-alias records. AWS-specific alias targets, VPC discovery, DNS resolution, and advanced routing policies are outside the implemented scope. IAM, Accounts, Organizations, and Billing are local simulations.

## Run locally

Requirements: Node.js 22+, Python 3.13+, and npm.

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --reload
```

In another terminal:

```powershell
cd frontend
npm ci
npm run dev
```

Open <http://localhost:3000> and choose **Create an account**. Supply an account name, unique username and email, and a password of at least 12 characters. Personal and Organization accounts both start with an empty, independent DNS workspace; Organization signup also creates a mock organization profile. `DATABASE_PATH` defaults to `backend/route53.db`. The Next.js proxy uses `BACKEND_URL`, which defaults to `http://127.0.0.1:8000`.

Alternatively, copy `.env.example` to `.env` and run `docker compose up --build` from the repository root. The Compose setup stores SQLite in the named `route53_data` volume, so data survives container recreation. Demo access is disabled by default. For local sample data only, set `SEED_DEMO=true` on the backend; this enables `demo` / `route53demo` (customizable with `DEMO_USER` and `DEMO_PASSWORD`).

### Credentials and isolation

Passwords are stored as salted PBKDF2-HMAC-SHA256 hashes with 600,000 iterations. Random session tokens are stored as SHA-256 digests; cookies are HTTP-only, SameSite=Lax, and expire after seven days. Signup and login have rate limits. Browser mutations check the request Origin against `ALLOWED_ORIGINS`; API responses use `Cache-Control: no-store`.

Every zone operation verifies ownership on the server before accessing records, tags, import, export, or bulk deletion. Requests for another account's resource return 404. Account and mock-service endpoints also enforce ownership. The UI clears workspace state when accounts change, including changes in another tab.

Use the account menu or sidebar to open Account settings, IAM, Organizations, and Billing. Mock IAM policy labels do not grant real permissions or create login credentials. Organization member accounts are simulated entries, without app logins or shared DNS access. Billing shows only your account's usage and an illustrative $0.50 per zone total; it collects no payment. Signup account types are Personal and Organization, each with an account-owner identity.

On first startup after upgrading an older database, existing zones, records, and tags remain intact and their zones are assigned to the reserved local demo account. Old sessions are invalidated. Back up the SQLite database before upgrading. Enable `SEED_DEMO=true` locally to access that legacy data; new registrations cannot see it. Changing `DEMO_PASSWORD` does not replace an already stored demo password.

Email verification, password recovery, MFA, and invitations for shared workspaces are not implemented.

## Architecture

```text
Browser → Next.js App Router UI → /api proxy → FastAPI → SQLite
```

The UI uses one client-side console component for the AWS-style shell and screens. The FastAPI service owns authentication, validation, search, pagination, and file format handling. SQLite is the durable store. The `/api` rewrite keeps requests and the session cookie on the same origin.

### Database schema

| Table | Purpose | Key fields |
| --- | --- | --- |
| `accounts` | Independent workspaces | id, name, account_type, created_at, is_demo |
| `users` | Registered credentials | id, unique username/email, password_hash, account_id |
| `sessions` | Login persistence | token digest, username, expires_at |
| `auth_attempts` | Login/signup rate limits | hashed key, attempts, expires_at |
| `mock_iam_users` | Simulated IAM identities | id, account_id, name, policy |
| `mock_organizations` | Simulated organizations | account_id, id, name |
| `mock_org_accounts` | Simulated member accounts | id, owner_account_id, name, email |
| `hosted_zones` | Public/private zones | id, account_id, name, type, comment, VPC fields, name_servers, timestamps |
| `tags` | Zone tags | zone_id, key, value |
| `records` | DNS record sets | id, zone_id, name, type, ttl, values_json, routing_policy, is_default, timestamps |

`records` has a unique constraint on `(zone_id, name, type)`, matching simple routing's single record set per name/type. Multiple values are stored in that record set. Foreign keys remove records and tags when a zone is deleted; the API first requires non-default records to be removed.

### API overview

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/auth/register` | Register an independent account and create session |
| POST | `/api/auth/login` | Create session |
| POST | `/api/auth/logout` | End session |
| GET | `/api/auth/me` | Check session |
| GET, PATCH | `/api/account` | Own profile and account name |
| GET | `/api/mock/iam` | Own mock IAM identities and principal |
| POST | `/api/mock/iam/users` | Create mock identity |
| PUT, DELETE | `/api/mock/iam/users/{id}` | Edit/delete own mock identity |
| GET, POST, PATCH | `/api/mock/organizations` | Own mock organization |
| POST | `/api/mock/organizations/accounts` | Add mock member account |
| DELETE | `/api/mock/organizations/accounts/{id}` | Remove own mock member |
| GET | `/api/mock/billing` | Own illustrative usage and cost |
| GET, POST | `/api/zones` | Search/list and create zones |
| GET, PATCH, DELETE | `/api/zones/{id}` | View, edit comment, delete zone |
| GET, POST | `/api/zones/{id}/records` | Search/list and create record sets |
| PUT, DELETE | `/api/zones/{id}/records/{record_id}` | Edit and delete a record set |
| POST | `/api/zones/{id}/records/bulk-delete` | Delete selected record sets |
| POST | `/api/zones/{id}/import` | Import BIND text (`{ "content": "..." }`) |
| GET | `/api/zones/{id}/export?format=json|bind` | Download a zone |
| GET | `/api/health` | Health check |

Interactive API docs are at `/docs` on the backend service. All zone and record endpoints require the session cookie.

## BIND import

The importer supports explicit owners, `@` for the zone root, relative owner names, and omitted owners. A record beginning with a space or tab inherits the owner of the preceding record. Explicit owners must start in the first column. Blank lines, comments, and `$TTL` directives do not reset the preceding owner.

TTL values can be integer seconds or case-insensitive `w`, `d`, `h`, `m`, and `s` units. Combined values such as `1h30m` are supported. Both `$TTL` and individual record TTLs use the same parser, with a range of 0–2147483647 seconds. An omitted TTL uses `$TTL` when supplied; otherwise it inherits the preceding record's TTL, with 300 seconds as the initial fallback. TTL and `IN` class may appear in either order.

Example file for `example.com`:

```bind
$ORIGIN example.com.
$TTL 1h
www IN A 192.0.2.10
    IN A 192.0.2.11
mail IN 30m MX 10 mail.example.com.
    30m IN MX 20 backup.example.com.
```

This creates a `www` A record set with two values and a 3600-second TTL, and a `mail` MX record set with two values and an 1800-second TTL. Invalid input fails the import before any records are committed. Error messages include the source line where possible. Existing records are not overwritten; the importer rejects conflicting name/type combinations.

Parenthesized records and comments outside quoted strings are supported. Default apex NS and SOA records are skipped, but still establish an owner for later shorthand rows. `$ORIGIN` must match the selected hosted zone. `$INCLUDE`, `$GENERATE`, nested origin changes, and arbitrary escaped owner names are not supported. Use fully qualified targets with trailing dots for domain-valued record data.

## Keyboard shortcuts

`/` focuses the current table search, `N` starts zone or record creation, `Esc` closes dialogs, and `?` shows shortcut help.

## Verification

From `backend/`, install `pip install -r requirements-dev.txt`, then run `python -m unittest discover -v`. From `frontend/`, run `npm test`, `npm run lint`, and `npm run build` after installing dependencies.

The 24 backend tests cover CRUD for all nine required record types, private zones, tags, search/filtering, pagination, authentication, persistence, JSON/BIND export, import, bulk deletion, and validation. Account tests verify credential and session hashing, duplicate identities, signup types, session rotation/expiry, rate limits, browser-origin protection, legacy migration, disabled deployment demo access, mock-service isolation, and rejection of another account's zone/record operations including import, export, and bulk deletion.

Latest local verification: all 24 API integration tests and five frontend error-handling tests pass. Frontend TypeScript checks and the production build pass. Browser interactions, exact visual similarity, and hosted deployment still require separate verification. The BIND importer supports the documented subset above.

If hosted-zone creation is rejected, the form displays the server's validation reason and field rather than only the HTTP status. Enter a domain such as `example.com`, without a URL scheme or path. Private zones also require the mocked VPC region and ID. A 422 response means the request failed validation; correct the displayed field and submit again.

## Deployment

Repository: **`GSK05/route53-console-clone`**

Live application: **https://YOUR-DOMAIN**

The application runs as two Railway services connected to the same repository:

| Service | Root directory | Port | Access |
| --- | --- | --- | --- |
| `backend` | `/backend` | `8000` | Railway private network only |
| `frontend` | `/frontend` | `3000` | Public HTTPS domain |

The backend has one instance, a health check at `/api/health`, and a persistent volume mounted at `/data`. Its environment variables are:

```dotenv
DATABASE_PATH=/data/route53.db
PORT=8000
COOKIE_SECURE=true
SEED_DEMO=false
ALLOWED_ORIGINS=https://YOUR-DOMAIN
