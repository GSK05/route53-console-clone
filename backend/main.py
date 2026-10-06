"""SQLite-backed Route 53 console simulation. No DNS requests are served."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import secrets
import shlex
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from fastapi import Cookie, FastAPI, HTTPException, Response, Depends, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field, field_validator
from security import hash_password, verify_password, session_digest, DUMMY_PASSWORD_HASH

DATABASE = Path(os.getenv("DATABASE_PATH", Path(__file__).parent / "route53.db"))
DEMO_USER = os.getenv("DEMO_USER", "demo")
DEMO_PASSWORD = os.getenv("DEMO_PASSWORD", "route53demo")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"
RECORD_TYPES = {"A", "AAAA", "CNAME", "TXT", "MX", "NS", "PTR", "SRV", "CAA", "SOA"}
DOMAIN_RE = re.compile(r"^(?=.{1,253}\.?$)(?:[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.?$", re.I)

app = FastAPI(title="Route 53 Clone API", version="1.0.0")


@app.middleware("http")
async def api_browser_policy(request: Request, call_next):
    origin = request.headers.get("origin")
    allowed = set(os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(","))
    if request.method not in {"GET", "HEAD", "OPTIONS"} and origin and origin not in allowed:
        return PlainTextResponse("Origin is not allowed", status_code=403)
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@contextmanager
def db():
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db():
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS accounts (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, account_type TEXT NOT NULL,
            created_at TEXT NOT NULL, is_demo INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            email TEXT NOT NULL UNIQUE COLLATE NOCASE, password_hash TEXT NOT NULL,
            account_id TEXT NOT NULL UNIQUE REFERENCES accounts(id), created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS auth_attempts (
            key TEXT PRIMARY KEY, attempts INTEGER NOT NULL, expires_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS mock_iam_users (
            id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id),
            name TEXT NOT NULL, policy TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(account_id,name)
        );
        CREATE TABLE IF NOT EXISTS mock_organizations (
            account_id TEXT PRIMARY KEY REFERENCES accounts(id), id TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS mock_org_accounts (
            id TEXT PRIMARY KEY, owner_account_id TEXT NOT NULL REFERENCES mock_organizations(account_id),
            name TEXT NOT NULL, email TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY, username TEXT NOT NULL, expires_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS hosted_zones (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, type TEXT NOT NULL CHECK(type IN ('public','private')),
            comment TEXT NOT NULL DEFAULT '', vpc_region TEXT, vpc_id TEXT,
            name_servers TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tags (
            zone_id TEXT NOT NULL REFERENCES hosted_zones(id) ON DELETE CASCADE,
            key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(zone_id,key)
        );
        CREATE TABLE IF NOT EXISTS records (
            id TEXT PRIMARY KEY, zone_id TEXT NOT NULL REFERENCES hosted_zones(id) ON DELETE CASCADE,
            name TEXT NOT NULL, type TEXT NOT NULL, ttl INTEGER NOT NULL,
            values_json TEXT NOT NULL, routing_policy TEXT NOT NULL DEFAULT 'Simple',
            is_default INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE(zone_id,name,type)
        );
        CREATE INDEX IF NOT EXISTS idx_zone_name ON hosted_zones(name);
        CREATE INDEX IF NOT EXISTS idx_records_zone ON records(zone_id,name,type);
        """)
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(hosted_zones)")}
        if "account_id" not in columns:
            conn.execute("ALTER TABLE hosted_zones ADD COLUMN account_id TEXT REFERENCES accounts(id)")
        legacy = conn.execute("SELECT COUNT(*) FROM hosted_zones WHERE account_id IS NULL").fetchone()[0]
        if legacy:
            account_id = ensure_demo_account(conn)
            conn.execute("UPDATE hosted_zones SET account_id=? WHERE account_id IS NULL", (account_id,))
            # Sessions issued before account ownership existed must be re-authenticated.
            conn.execute("DELETE FROM sessions")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_zone_account ON hosted_zones(account_id,name)")


@app.on_event("startup")
def startup():
    init_db()
    if os.getenv("SEED_DEMO", "false").lower() == "true":
        seed_demo()


def now():
    return datetime.now(timezone.utc).isoformat()


def require_auth(session: str | None = Cookie(default=None)):
    if not session:
        raise HTTPException(401, "Please sign in")
    with db() as conn:
        row = conn.execute("SELECT users.account_id,accounts.is_demo,sessions.expires_at FROM sessions JOIN users ON users.username=sessions.username JOIN accounts ON accounts.id=users.account_id WHERE sessions.token=?", (session_digest(session),)).fetchone()
    if not row or row["expires_at"] <= now() or (row["is_demo"] and not demo_access_enabled()):
        raise HTTPException(401, "Session expired")
    return row["account_id"]


def demo_access_enabled():
    return os.getenv("SEED_DEMO", "false").lower() == "true"


def public_user(conn, account_id: str):
    row = conn.execute("SELECT users.id,users.username,users.email,accounts.id AS account_id,accounts.name AS account_name,accounts.account_type,accounts.is_demo,users.created_at FROM users JOIN accounts ON users.account_id=accounts.id WHERE accounts.id=?", (account_id,)).fetchone()
    if not row:
        raise HTTPException(401, "Please sign in")
    result = dict(row)
    result["is_demo"] = bool(result["is_demo"])
    result["role"] = "Account owner"
    return result


def ensure_demo_account(conn):
    row = conn.execute("SELECT account_id FROM users WHERE username=?", (DEMO_USER,)).fetchone()
    if row:
        return row["account_id"]
    account_id, created = "000000000001", now()
    conn.execute("INSERT INTO accounts VALUES (?,?,?,?,?)", (account_id, "Demo account", "personal", created, 1))
    conn.execute("INSERT INTO users VALUES (?,?,?,?,?,?)", (secrets.token_hex(12), DEMO_USER, "demo@route53.invalid", hash_password(DEMO_PASSWORD), account_id, created))
    return account_id


def limit_auth_attempts(conn, request: Request, kind: str):
    address = request.client.host if request.client else "unknown"
    key = session_digest(f"{kind}:{address}")
    row = conn.execute("SELECT * FROM auth_attempts WHERE key=?", (key,)).fetchone()
    limit = 10 if kind == "register" else 30
    if row and row["expires_at"] > now():
        if row["attempts"] >= limit:
            raise HTTPException(429, "Too many attempts. Please try again in 15 minutes.")
        conn.execute("UPDATE auth_attempts SET attempts=attempts+1 WHERE key=?", (key,))
    else:
        expires = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
        conn.execute("INSERT OR REPLACE INTO auth_attempts VALUES (?,?,?)", (key, 1, expires))


def issue_session(conn, response: Response, username: str, previous: str | None = None):
    if previous:
        conn.execute("DELETE FROM sessions WHERE token=?", (session_digest(previous),))
    conn.execute("DELETE FROM sessions WHERE expires_at<=?", (now(),))
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    conn.execute("INSERT INTO sessions VALUES (?,?,?)", (session_digest(token), username, expires))
    response.set_cookie("session", token, httponly=True, secure=COOKIE_SECURE, samesite="lax", max_age=604800, path="/")
    response.headers["Cache-Control"] = "no-store"


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9._-]+$")
    email: str = Field(max_length=254)
    password: str = Field(min_length=12, max_length=128)
    account_name: str = Field(min_length=1, max_length=100)
    account_type: Literal["personal", "organization"] = "personal"

    @field_validator("username", "email")
    @classmethod
    def normalize_identity(cls, value):
        return value.strip().lower()

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Enter a valid email address")
        return value

    @field_validator("account_name")
    @classmethod
    def valid_account_name(cls, value):
        if not value.strip():
            raise ValueError("Account name is required")
        return value.strip()


@app.post("/api/auth/register", status_code=201)
def register(body: RegisterIn, response: Response, request: Request, session: str | None = Cookie(default=None)):
    with db() as conn:
        limit_auth_attempts(conn, request, "register")
    if body.username == DEMO_USER.strip().lower():
        raise HTTPException(409, "This username is reserved for the local demo. Choose another username.")
    created, account_id = now(), str(secrets.randbelow(900_000_000_000) + 100_000_000_000)
    password_hash = hash_password(body.password)
    with db() as conn:
        try:
            conn.execute("INSERT INTO accounts VALUES (?,?,?,?,?)", (account_id, body.account_name, body.account_type, created, 0))
            conn.execute("INSERT INTO users VALUES (?,?,?,?,?,?)", (secrets.token_hex(12), body.username, body.email, password_hash, account_id, created))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Username or email is already registered")
        if body.account_type == "organization":
            conn.execute("INSERT INTO mock_organizations VALUES (?,?,?,?)", (account_id, "o-" + secrets.token_hex(6), body.account_name, created))
        issue_session(conn, response, body.username, session)
        return public_user(conn, account_id)


@app.post("/api/auth/login")
def login(body: LoginIn, response: Response, request: Request, session: str | None = Cookie(default=None)):
    with db() as conn:
        limit_auth_attempts(conn, request, "login")
    with db() as conn:
        identifier = body.username.strip().lower()
        user = conn.execute("SELECT users.*,accounts.is_demo FROM users JOIN accounts ON accounts.id=users.account_id WHERE username=? OR email=?", (identifier, identifier)).fetchone()
        valid = verify_password(body.password, user["password_hash"] if user else DUMMY_PASSWORD_HASH)
        if not user or not valid or (user["is_demo"] and not demo_access_enabled()):
            raise HTTPException(401, "Invalid username or password")
        issue_session(conn, response, user["username"], session)
        return public_user(conn, user["account_id"])


@app.post("/api/auth/logout")
def logout(response: Response, session: str | None = Cookie(default=None)):
    if session:
        with db() as conn:
            conn.execute("DELETE FROM sessions WHERE token=?", (session_digest(session),))
    response.delete_cookie("session", path="/")
    return {"ok": True}


@app.get("/api/auth/me")
def me(response: Response, account_id: str = Depends(require_auth)):
    response.headers["Cache-Control"] = "no-store"
    with db() as conn:
        return public_user(conn, account_id)


class AccountEdit(BaseModel):
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("Name is required")
        return value.strip()


@app.get("/api/account")
def account_profile(account_id: str = Depends(require_auth)):
    with db() as conn:
        return public_user(conn, account_id)


@app.patch("/api/account")
def edit_account(body: AccountEdit, account_id: str = Depends(require_auth)):
    with db() as conn:
        conn.execute("UPDATE accounts SET name=? WHERE id=?", (body.name, account_id))
        return public_user(conn, account_id)


class MockIamIn(BaseModel):
    name: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9._-]+$")
    policy: Literal["AdministratorAccess", "AmazonRoute53ReadOnlyAccess"] = "AmazonRoute53ReadOnlyAccess"


@app.get("/api/mock/iam")
def mock_iam(account_id: str = Depends(require_auth)):
    with db() as conn:
        principal = public_user(conn, account_id)
        identities = [dict(r) for r in conn.execute("SELECT id,name,policy,created_at FROM mock_iam_users WHERE account_id=? ORDER BY name", (account_id,))]
    return {"principal": {"name": principal["username"], "role": "Account owner", "arn": f"arn:aws:iam::{account_id}:root"}, "identities": identities, "policies": ["AdministratorAccess", "AmazonRoute53ReadOnlyAccess"], "mock": True}


@app.post("/api/mock/iam/users", status_code=201)
def add_mock_iam(body: MockIamIn, account_id: str = Depends(require_auth)):
    item_id = secrets.token_hex(12)
    with db() as conn:
        try:
            conn.execute("INSERT INTO mock_iam_users VALUES (?,?,?,?,?)", (item_id, account_id, body.name, body.policy, now()))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "An IAM identity with this name already exists in your account")
    return {"id": item_id, "name": body.name, "policy": body.policy, "mock": True}


@app.put("/api/mock/iam/users/{item_id}")
def update_mock_iam(item_id: str, body: MockIamIn, account_id: str = Depends(require_auth)):
    with db() as conn:
        if not conn.execute("SELECT 1 FROM mock_iam_users WHERE id=? AND account_id=?", (item_id, account_id)).fetchone():
            raise HTTPException(404, "IAM identity not found")
        try:
            conn.execute("UPDATE mock_iam_users SET name=?,policy=? WHERE id=? AND account_id=?", (body.name, body.policy, item_id, account_id))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "An IAM identity with this name already exists in your account")
    return {"id": item_id, "name": body.name, "policy": body.policy, "mock": True}


@app.delete("/api/mock/iam/users/{item_id}", status_code=204)
def delete_mock_iam(item_id: str, account_id: str = Depends(require_auth)):
    with db() as conn:
        deleted = conn.execute("DELETE FROM mock_iam_users WHERE id=? AND account_id=?", (item_id, account_id))
        if not deleted.rowcount:
            raise HTTPException(404, "IAM identity not found")


@app.get("/api/mock/organizations")
def mock_organization(account_id: str = Depends(require_auth)):
    with db() as conn:
        org = conn.execute("SELECT id,name,created_at FROM mock_organizations WHERE account_id=?", (account_id,)).fetchone()
        members = [dict(r) for r in conn.execute("SELECT id,name,email,created_at FROM mock_org_accounts WHERE owner_account_id=? ORDER BY name", (account_id,))]
    return {"organization": dict(org) if org else None, "accounts": members, "mock": True}


@app.post("/api/mock/organizations", status_code=201)
def create_mock_organization(body: AccountEdit, account_id: str = Depends(require_auth)):
    org_id = "o-" + secrets.token_hex(6)
    with db() as conn:
        try:
            conn.execute("INSERT INTO mock_organizations VALUES (?,?,?,?)", (account_id, org_id, body.name, now()))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Your account already has a mock organization")
    return {"id": org_id, "name": body.name, "mock": True}


@app.patch("/api/mock/organizations")
def edit_mock_organization(body: AccountEdit, account_id: str = Depends(require_auth)):
    with db() as conn:
        result = conn.execute("UPDATE mock_organizations SET name=? WHERE account_id=?", (body.name, account_id))
        if not result.rowcount:
            raise HTTPException(404, "Organization not found")
    return {"name": body.name, "mock": True}


class MockOrgAccountIn(AccountEdit):
    email: str = Field(max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


@app.post("/api/mock/organizations/accounts", status_code=201)
def add_mock_org_account(body: MockOrgAccountIn, account_id: str = Depends(require_auth)):
    member_id = str(secrets.randbelow(900_000_000_000) + 100_000_000_000)
    with db() as conn:
        if not conn.execute("SELECT 1 FROM mock_organizations WHERE account_id=?", (account_id,)).fetchone():
            raise HTTPException(404, "Create an organization first")
        conn.execute("INSERT INTO mock_org_accounts VALUES (?,?,?,?,?)", (member_id, account_id, body.name, body.email, now()))
    return {"id": member_id, "name": body.name, "email": body.email, "mock": True}


@app.delete("/api/mock/organizations/accounts/{member_id}", status_code=204)
def remove_mock_org_account(member_id: str, account_id: str = Depends(require_auth)):
    with db() as conn:
        deleted = conn.execute("DELETE FROM mock_org_accounts WHERE id=? AND owner_account_id=?", (member_id, account_id))
        if not deleted.rowcount:
            raise HTTPException(404, "Organization account not found")


@app.get("/api/mock/billing")
def mock_billing(account_id: str = Depends(require_auth)):
    with db() as conn:
        zones = conn.execute("SELECT COUNT(*) FROM hosted_zones WHERE account_id=?", (account_id,)).fetchone()[0]
        records = conn.execute("SELECT COUNT(*) FROM records JOIN hosted_zones ON records.zone_id=hosted_zones.id WHERE hosted_zones.account_id=?", (account_id,)).fetchone()[0]
    return {"mock": True, "currency": "USD", "period": datetime.now(timezone.utc).strftime("%B %Y"), "hosted_zones": zones, "record_sets": records, "dns_queries": 0, "illustrative_total": round(zones * 0.5, 2), "payment_status": "No charges — simulation only"}


class Tag(BaseModel):
    key: str = Field(min_length=1, max_length=128)
    value: str = Field(default="", max_length=256)


class ZoneIn(BaseModel):
    name: str
    type: Literal["public", "private"] = "public"
    comment: str = Field(default="", max_length=256)
    vpc_region: str | None = None
    vpc_id: str | None = None
    tags: list[Tag] = Field(default_factory=list, max_length=50)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str):
        value = value.strip().lower().rstrip(".")
        if not DOMAIN_RE.fullmatch(value) or "." not in value:
            raise ValueError("Enter a domain such as example.com, without https://, paths, or spaces")
        return value


class ZoneEdit(BaseModel):
    comment: str = Field(max_length=256)


def zone_dict(conn, row):
    result = dict(row)
    result["name_servers"] = json.loads(result["name_servers"])
    result["record_count"] = conn.execute("SELECT COUNT(*) FROM records WHERE zone_id=?", (row["id"],)).fetchone()[0]
    result["tags"] = [dict(tag) for tag in conn.execute("SELECT key,value FROM tags WHERE zone_id=? ORDER BY key", (row["id"],))]
    return result


def get_zone(conn, zone_id, account_id):
    row = conn.execute("SELECT * FROM hosted_zones WHERE id=? AND account_id=?", (zone_id, account_id)).fetchone()
    if not row:
        raise HTTPException(404, "Hosted zone not found")
    return row


@app.get("/api/zones")
def list_zones(q: str = "", type: str = "all", page: int = 1, page_size: int = 10, _: str = Depends(require_auth)):
    page, page_size = max(page, 1), min(max(page_size, 1), 100)
    where = "WHERE account_id=? AND (name LIKE ? OR comment LIKE ? OR id LIKE ?)"
    params: list = [_] + [f"%{q}%"] * 3
    if type in ("public", "private"):
        where += " AND type=?"
        params.append(type)
    with db() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM hosted_zones {where}", params).fetchone()[0]
        rows = conn.execute(f"SELECT * FROM hosted_zones {where} ORDER BY name,id LIMIT ? OFFSET ?", (*params, page_size, (page - 1) * page_size)).fetchall()
        return {"items": [zone_dict(conn, row) for row in rows], "total": total, "page": page, "page_size": page_size}


@app.post("/api/zones", status_code=201)
def create_zone(body: ZoneIn, _: str = Depends(require_auth)):
    if body.type == "private" and not (body.vpc_region and body.vpc_id):
        raise HTTPException(422, "A VPC region and VPC ID are required for private hosted zones")
    zone_id = "Z" + secrets.token_hex(10).upper()
    created = now()
    servers = [f"ns-{secrets.randbelow(1900)+100}.awsdns-{n}.{tld}" for n, tld in zip(("01", "02", "03", "04"), ("com", "net", "org", "co.uk"))] if body.type == "public" else []
    with db() as conn:
        conn.execute("INSERT INTO hosted_zones (id,name,type,comment,vpc_region,vpc_id,name_servers,created_at,updated_at,account_id) VALUES (?,?,?,?,?,?,?,?,?,?)", (zone_id, body.name, body.type, body.comment, body.vpc_region, body.vpc_id, json.dumps(servers), created, created, _))
        conn.executemany("INSERT INTO tags VALUES (?,?,?)", [(zone_id, t.key, t.value) for t in body.tags])
        if body.type == "public":
            for record_type, values, ttl in (("NS", servers, 172800), ("SOA", [f"{servers[0]}. hostmaster.{body.name}. 1 7200 900 1209600 86400"], 900)):
                conn.execute("INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?,?)", (secrets.token_hex(12), zone_id, body.name, record_type, ttl, json.dumps(values), "Simple", 1, created, created))
        return zone_dict(conn, get_zone(conn, zone_id, _))


@app.get("/api/zones/{zone_id}")
def read_zone(zone_id: str, _: str = Depends(require_auth)):
    with db() as conn:
        return zone_dict(conn, get_zone(conn, zone_id, _))


@app.patch("/api/zones/{zone_id}")
def edit_zone(zone_id: str, body: ZoneEdit, _: str = Depends(require_auth)):
    with db() as conn:
        get_zone(conn, zone_id, _)
        conn.execute("UPDATE hosted_zones SET comment=?,updated_at=? WHERE id=?", (body.comment, now(), zone_id))
        return zone_dict(conn, get_zone(conn, zone_id, _))


@app.delete("/api/zones/{zone_id}", status_code=204)
def delete_zone(zone_id: str, _: str = Depends(require_auth)):
    with db() as conn:
        get_zone(conn, zone_id, _)
        count = conn.execute("SELECT COUNT(*) FROM records WHERE zone_id=? AND is_default=0", (zone_id,)).fetchone()[0]
        if count:
            raise HTTPException(409, "Delete non-default records before deleting this hosted zone")
        conn.execute("DELETE FROM hosted_zones WHERE id=?", (zone_id,))


class RecordIn(BaseModel):
    name: str = ""
    type: str
    ttl: int = Field(default=300, ge=0, le=2147483647)
    values: list[str] = Field(min_length=1)

    @field_validator("type")
    @classmethod
    def valid_type(cls, value):
        if value.upper() not in RECORD_TYPES - {"SOA"}:
            raise ValueError("Unsupported record type")
        return value.upper()


def normalized_name(name: str, zone_name: str):
    name = name.strip().lower()
    absolute = name.endswith(".")
    name = name.rstrip(".")
    if name in ("", "@"):
        return zone_name
    if absolute and name != zone_name and not name.endswith("." + zone_name):
        raise HTTPException(422, "Record name must be inside the hosted zone")
    fqdn = name if name == zone_name or name.endswith("." + zone_name) else f"{name}.{zone_name}"
    if (fqdn != zone_name and not fqdn.endswith("." + zone_name)) or not DOMAIN_RE.fullmatch(fqdn):
        raise HTTPException(422, "Record name must be inside the hosted zone")
    return fqdn


def validated_record(body: RecordIn, zone_name: str):
    name = normalized_name(body.name, zone_name)
    values = [value.strip() for value in body.values if value.strip()]
    if not values:
        raise HTTPException(422, "Enter at least one value")
    if body.type == "CNAME" and (name == zone_name or len(values) != 1):
        raise HTTPException(422, "CNAME needs one value and cannot be at the zone apex")
    for value in values:
        if body.type in ("A", "AAAA"):
            try:
                address = ipaddress.ip_address(value)
                if address.version != (4 if body.type == "A" else 6):
                    raise ValueError()
            except ValueError:
                raise HTTPException(422, f"Invalid {body.type} address: {value}")
        elif body.type in ("CNAME", "NS", "PTR") and not DOMAIN_RE.fullmatch(value):
            raise HTTPException(422, f"Invalid domain value: {value}")
        elif body.type == "MX" and not re.fullmatch(r"\d{1,5}\s+\S+", value):
            raise HTTPException(422, "MX values must be priority and mail server")
        elif body.type == "SRV" and not re.fullmatch(r"\d+\s+\d+\s+\d+\s+\S+", value):
            raise HTTPException(422, "SRV values must be priority weight port target")
        elif body.type == "CAA" and not re.fullmatch(r'\d+\s+\S+\s+.+', value):
            raise HTTPException(422, "CAA values must be flag tag value")
    return name, values


def record_dict(row):
    result = dict(row)
    result["values"] = json.loads(result.pop("values_json"))
    result["is_default"] = bool(result["is_default"])
    return result


@app.get("/api/zones/{zone_id}/records")
def list_records(zone_id: str, q: str = "", type: str = "all", page: int = 1, page_size: int = 10, _: str = Depends(require_auth)):
    page, page_size = max(page, 1), min(max(page_size, 1), 100)
    where = "WHERE zone_id=? AND (name LIKE ? OR type LIKE ? OR values_json LIKE ?)"
    params: list = [zone_id, f"%{q}%", f"%{q}%", f"%{q}%"]
    if type != "all":
        where += " AND type=?"
        params.append(type.upper())
    with db() as conn:
        get_zone(conn, zone_id, _)
        total = conn.execute(f"SELECT COUNT(*) FROM records {where}", params).fetchone()[0]
        rows = conn.execute(f"SELECT * FROM records {where} ORDER BY name,type LIMIT ? OFFSET ?", (*params, page_size, (page - 1) * page_size)).fetchall()
        return {"items": [record_dict(row) for row in rows], "total": total, "page": page, "page_size": page_size}


@app.post("/api/zones/{zone_id}/records", status_code=201)
def create_record(zone_id: str, body: RecordIn, _: str = Depends(require_auth)):
    with db() as conn:
        zone = get_zone(conn, zone_id, _)
        name, values = validated_record(body, zone["name"])
        record_id, created = secrets.token_hex(12), now()
        try:
            conn.execute("INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?,?)", (record_id, zone_id, name, body.type, body.ttl, json.dumps(values), "Simple", 0, created, created))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "A record with this name and type already exists")
        return record_dict(conn.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone())


@app.put("/api/zones/{zone_id}/records/{record_id}")
def edit_record(zone_id: str, record_id: str, body: RecordIn, _: str = Depends(require_auth)):
    with db() as conn:
        zone = get_zone(conn, zone_id, _)
        row = conn.execute("SELECT * FROM records WHERE id=? AND zone_id=?", (record_id, zone_id)).fetchone()
        if not row:
            raise HTTPException(404, "Record not found")
        if row["is_default"]:
            raise HTTPException(403, "Default NS and SOA records cannot be edited")
        name, values = validated_record(body, zone["name"])
        try:
            conn.execute("UPDATE records SET name=?,type=?,ttl=?,values_json=?,updated_at=? WHERE id=?", (name, body.type, body.ttl, json.dumps(values), now(), record_id))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "A record with this name and type already exists")
        return record_dict(conn.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone())


@app.delete("/api/zones/{zone_id}/records/{record_id}", status_code=204)
def delete_record(zone_id: str, record_id: str, _: str = Depends(require_auth)):
    with db() as conn:
        get_zone(conn, zone_id, _)
        row = conn.execute("SELECT is_default FROM records WHERE id=? AND zone_id=?", (record_id, zone_id)).fetchone()
        if not row:
            raise HTTPException(404, "Record not found")
        if row["is_default"]:
            raise HTTPException(403, "Default NS and SOA records cannot be deleted")
        conn.execute("DELETE FROM records WHERE id=?", (record_id,))


class BulkDelete(BaseModel):
    ids: list[str] = Field(min_length=1)


@app.post("/api/zones/{zone_id}/records/bulk-delete")
def bulk_delete(zone_id: str, body: BulkDelete, _: str = Depends(require_auth)):
    with db() as conn:
        get_zone(conn, zone_id, _)
        placeholders = ",".join("?" for _ in body.ids)
        rows = conn.execute(f"SELECT id,is_default FROM records WHERE zone_id=? AND id IN ({placeholders})", (zone_id, *body.ids)).fetchall()
        if len(rows) != len(set(body.ids)) or any(row["is_default"] for row in rows):
            raise HTTPException(422, "Selection contains missing or default records")
        conn.execute(f"DELETE FROM records WHERE zone_id=? AND id IN ({placeholders})", (zone_id, *body.ids))
        return {"deleted": len(rows)}


@app.get("/api/zones/{zone_id}/export")
def export_zone(zone_id: str, format: Literal["json", "bind"] = "json", _: str = Depends(require_auth)):
    with db() as conn:
        zone = zone_dict(conn, get_zone(conn, zone_id, _))
        records = [record_dict(r) for r in conn.execute("SELECT * FROM records WHERE zone_id=? ORDER BY name,type", (zone_id,))]
    if format == "json":
        content = json.dumps({"zone": zone, "records": records}, indent=2)
        media = "application/json"
    else:
        lines = [f"$ORIGIN {zone['name']}.", "$TTL 300", ""]
        for record in records:
            owner = "@" if record["name"] == zone["name"] else record["name"][: -(len(zone["name"]) + 1)]
            for value in record["values"]:
                lines.append(f"{owner} {record['ttl']} IN {record['type']} {value}")
        content, media = "\n".join(lines) + "\n", "text/plain"
    extension = "json" if format == "json" else "zone"
    return PlainTextResponse(content, media_type=media, headers={"Content-Disposition": f'attachment; filename="{zone["name"]}.{extension}"'})


class ZoneImportIn(BaseModel):
    content: str


def bind_lines(content: str):
    """Preserve owner omission while joining records and stripping comments."""
    chunks, start_line, depth, owner_omitted = [], 1, 0, False
    for line_number, raw in enumerate(content.splitlines(), 1):
        quoted, escaped, clean = False, False, []
        for char in raw:
            if char == '"' and not escaped:
                quoted = not quoted
            if char == ";" and not quoted and not escaped:
                break
            if not quoted and not escaped and char == "(":
                depth += 1
                char = " "
            elif not quoted and not escaped and char == ")":
                depth -= 1
                if depth < 0:
                    raise HTTPException(422, f"Line {line_number}: unexpected closing parenthesis")
                char = " "
            clean.append(char)
            escaped = char == "\\" and not escaped
            if char != "\\":
                escaped = False
        fragment = "".join(clean).strip()
        if fragment and not chunks:
            start_line = line_number
            owner_omitted = bool(raw and raw[0].isspace())
        if fragment:
            chunks.append(fragment)
        if depth <= 0 and chunks:
            yield start_line, " ".join(chunks), owner_omitted
            chunks, depth = [], 0
    if chunks:
        raise HTTPException(422, "Unclosed parenthesized record in zone file")


def parse_bind_ttl(value: str):
    """Read integer seconds or BIND unit sequences such as 1h30m."""
    if not re.fullmatch(r"(?:[0-9]+[wdhms]?)+", value, re.I):
        raise ValueError(f"Invalid TTL '{value}'; use seconds or w/d/h/m/s units")
    units = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    ttl = sum(int(amount) * units[unit.lower()] for amount, unit in re.findall(r"([0-9]+)([wdhms]?)", value, re.I))
    if ttl > 2147483647:
        raise ValueError("TTL must be between 0 and 2147483647 seconds")
    return ttl


def parse_bind_header(parts: list[str], owner_omitted: bool, last_owner: str | None, zone_name: str):
    if owner_omitted:
        if last_owner is None:
            raise ValueError("An omitted owner needs a preceding record with an explicit owner")
        owner, index = last_owner, 0
    else:
        owner, index = normalized_name(parts[0], zone_name), 1
    ttl, class_seen = None, False
    while index < len(parts) and parts[index].upper() not in RECORD_TYPES:
        field = parts[index]
        if field.upper() in {"IN", "CH", "HS"}:
            if field.upper() != "IN":
                raise ValueError("Only IN-class records are supported")
            if class_seen:
                raise ValueError("Record class is specified more than once")
            class_seen = True
        else:
            if ttl is not None:
                raise ValueError("TTL is specified more than once")
            ttl = parse_bind_ttl(field)
        index += 1
    if index >= len(parts) or index + 1 >= len(parts):
        raise ValueError("Record type and value are required")
    return owner, parts[index].upper(), ttl, parts[index + 1:]


@app.post("/api/zones/{zone_id}/import")
def import_zone(zone_id: str, body: ZoneImportIn, _: str = Depends(require_auth)):
    if len(body.content.encode("utf-8")) > 1024 * 1024:
        raise HTTPException(413, "Zone file exceeds 1 MB")
    text = body.content.lstrip("\ufeff")
    with db() as conn:
        zone = get_zone(conn, zone_id, _)
        default_ttl, last_ttl, last_owner = None, 300, None
        pending, errors = [], []
        for line_number, line, owner_omitted in bind_lines(text):
            try:
                parts = shlex.split(line)
                if not parts:
                    continue
                if parts[0].upper() == "$ORIGIN":
                    if len(parts) != 2 or parts[1].rstrip(".").lower() != zone["name"]:
                        raise ValueError(f"Origin must match {zone['name']}")
                    continue
                if parts[0].upper() == "$TTL":
                    if len(parts) != 2:
                        raise ValueError("$TTL requires a single TTL value")
                    default_ttl = parse_bind_ttl(parts[1])
                    continue
                if parts[0].startswith("$"):
                    raise ValueError(f"Unsupported directive: {parts[0]}")
                fqdn, kind, explicit_ttl, rdata = parse_bind_header(parts, owner_omitted, last_owner, zone["name"])
                item_ttl = explicit_ttl if explicit_ttl is not None else (default_ttl if default_ttl is not None else last_ttl)
                # Skipped system records still establish an owner for shorthand rows.
                last_owner, last_ttl = fqdn, item_ttl
                if kind == "SOA":
                    continue
                value = " ".join(rdata)
                if kind == "TXT":
                    value = '"' + value.replace('"', '\\"') + '"'
                if fqdn == zone["name"] and kind == "NS":
                    continue
                candidate = RecordIn(name=fqdn, type=kind, ttl=item_ttl, values=[value])
                validated_record(candidate, zone["name"])
                pending.append((fqdn, kind, item_ttl, value))
            except (ValueError, HTTPException) as error:
                detail = error.detail if isinstance(error, HTTPException) else str(error)
                errors.append(f"Line {line_number}: {detail}")
        if errors:
            raise HTTPException(422, {"errors": errors})
        grouped = {}
        for name, kind, item_ttl, value in pending:
            key = (name, kind)
            if key in grouped and grouped[key][0] != item_ttl:
                raise HTTPException(422, "Records with the same name and type must have the same TTL")
            grouped.setdefault(key, (item_ttl, []))[1].append(value)
        for (name, kind) in grouped:
            if conn.execute("SELECT 1 FROM records WHERE zone_id=? AND name=? AND type=?", (zone_id, name, kind)).fetchone():
                raise HTTPException(409, f"Record already exists: {name} {kind}")
        created = now()
        for (name, kind), (item_ttl, values) in grouped.items():
            validated_record(RecordIn(name=name, type=kind, ttl=item_ttl, values=values), zone["name"])
            conn.execute("INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?,?)", (secrets.token_hex(12), zone_id, name, kind, item_ttl, json.dumps(values), "Simple", 0, created, created))
        return {"imported": len(grouped)}


@app.get("/api/health")
def health():
    return {"status": "ok"}


def seed_demo():
    """Create a small first-run dataset for the hosted demo only."""
    with db() as conn:
        account_id = ensure_demo_account(conn)
        if conn.execute("SELECT COUNT(*) FROM hosted_zones WHERE account_id=?", (account_id,)).fetchone()[0]:
            return
    public = create_zone(ZoneIn(name="example.com", type="public", comment="Public website and mail records"), _=account_id)
    private = create_zone(ZoneIn(name="internal.example.com", type="private", comment="Private application records", vpc_region="us-east-1", vpc_id="vpc-0123456789abcdef0"), _=account_id)
    for zone_id, record in (
        (public["id"], RecordIn(name="www", type="A", ttl=300, values=["192.0.2.10"])),
        (public["id"], RecordIn(name="mail", type="MX", ttl=300, values=["10 mail.example.com"])),
        (public["id"], RecordIn(name="", type="TXT", ttl=300, values=['"v=spf1 -all"'])),
        (private["id"], RecordIn(name="app", type="A", ttl=60, values=["10.0.1.25"])),
    ):
        create_record(zone_id, record, _=account_id)
