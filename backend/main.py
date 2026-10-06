"""SQLite-backed Route 53 console simulation. No DNS requests are served."""

from __future__ import annotations

import hmac
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

from fastapi import Cookie, FastAPI, HTTPException, Response, Depends
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field, field_validator

DATABASE = Path(os.getenv("DATABASE_PATH", Path(__file__).parent / "route53.db"))
DEMO_USER = os.getenv("DEMO_USER", "demo")
DEMO_PASSWORD = os.getenv("DEMO_PASSWORD", "route53demo")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"
RECORD_TYPES = {"A", "AAAA", "CNAME", "TXT", "MX", "NS", "PTR", "SRV", "CAA", "SOA"}
DOMAIN_RE = re.compile(r"^(?=.{1,253}\.?$)(?:[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?\.)*[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.?$", re.I)

app = FastAPI(title="Route 53 Clone API", version="1.0.0")


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
        row = conn.execute("SELECT username,expires_at FROM sessions WHERE token=?", (session,)).fetchone()
    if not row or row["expires_at"] <= now():
        raise HTTPException(401, "Session expired")
    return row["username"]


class LoginIn(BaseModel):
    username: str
    password: str


@app.post("/api/auth/login")
def login(body: LoginIn, response: Response):
    if not (hmac.compare_digest(body.username, DEMO_USER) and hmac.compare_digest(body.password, DEMO_PASSWORD)):
        raise HTTPException(401, "Invalid username or password")
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    with db() as conn:
        conn.execute("INSERT INTO sessions VALUES (?,?,?)", (token, DEMO_USER, expires))
    response.set_cookie("session", token, httponly=True, secure=COOKIE_SECURE, samesite="lax", max_age=604800, path="/")
    return {"username": DEMO_USER}


@app.post("/api/auth/logout")
def logout(response: Response, session: str | None = Cookie(default=None)):
    if session:
        with db() as conn:
            conn.execute("DELETE FROM sessions WHERE token=?", (session,))
    response.delete_cookie("session", path="/")
    return {"ok": True}


@app.get("/api/auth/me")
def me(username: str = Depends(require_auth)):
    return {"username": username}


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


def get_zone(conn, zone_id):
    row = conn.execute("SELECT * FROM hosted_zones WHERE id=?", (zone_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Hosted zone not found")
    return row


@app.get("/api/zones")
def list_zones(q: str = "", type: str = "all", page: int = 1, page_size: int = 10, _: str = Depends(require_auth)):
    page, page_size = max(page, 1), min(max(page_size, 1), 100)
    where = "WHERE (name LIKE ? OR comment LIKE ? OR id LIKE ?)"
    params: list = [f"%{q}%"] * 3
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
        conn.execute("INSERT INTO hosted_zones VALUES (?,?,?,?,?,?,?,?,?)", (zone_id, body.name, body.type, body.comment, body.vpc_region, body.vpc_id, json.dumps(servers), created, created))
        conn.executemany("INSERT INTO tags VALUES (?,?,?)", [(zone_id, t.key, t.value) for t in body.tags])
        if body.type == "public":
            for record_type, values, ttl in (("NS", servers, 172800), ("SOA", [f"{servers[0]}. hostmaster.{body.name}. 1 7200 900 1209600 86400"], 900)):
                conn.execute("INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?,?)", (secrets.token_hex(12), zone_id, body.name, record_type, ttl, json.dumps(values), "Simple", 1, created, created))
        return zone_dict(conn, get_zone(conn, zone_id))


@app.get("/api/zones/{zone_id}")
def read_zone(zone_id: str, _: str = Depends(require_auth)):
    with db() as conn:
        return zone_dict(conn, get_zone(conn, zone_id))


@app.patch("/api/zones/{zone_id}")
def edit_zone(zone_id: str, body: ZoneEdit, _: str = Depends(require_auth)):
    with db() as conn:
        get_zone(conn, zone_id)
        conn.execute("UPDATE hosted_zones SET comment=?,updated_at=? WHERE id=?", (body.comment, now(), zone_id))
        return zone_dict(conn, get_zone(conn, zone_id))


@app.delete("/api/zones/{zone_id}", status_code=204)
def delete_zone(zone_id: str, _: str = Depends(require_auth)):
    with db() as conn:
        get_zone(conn, zone_id)
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
        get_zone(conn, zone_id)
        total = conn.execute(f"SELECT COUNT(*) FROM records {where}", params).fetchone()[0]
        rows = conn.execute(f"SELECT * FROM records {where} ORDER BY name,type LIMIT ? OFFSET ?", (*params, page_size, (page - 1) * page_size)).fetchall()
        return {"items": [record_dict(row) for row in rows], "total": total, "page": page, "page_size": page_size}


@app.post("/api/zones/{zone_id}/records", status_code=201)
def create_record(zone_id: str, body: RecordIn, _: str = Depends(require_auth)):
    with db() as conn:
        zone = get_zone(conn, zone_id)
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
        zone = get_zone(conn, zone_id)
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
        get_zone(conn, zone_id)
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
        get_zone(conn, zone_id)
        placeholders = ",".join("?" for _ in body.ids)
        rows = conn.execute(f"SELECT id,is_default FROM records WHERE zone_id=? AND id IN ({placeholders})", (zone_id, *body.ids)).fetchall()
        if len(rows) != len(set(body.ids)) or any(row["is_default"] for row in rows):
            raise HTTPException(422, "Selection contains missing or default records")
        conn.execute(f"DELETE FROM records WHERE zone_id=? AND id IN ({placeholders})", (zone_id, *body.ids))
        return {"deleted": len(rows)}


@app.get("/api/zones/{zone_id}/export")
def export_zone(zone_id: str, format: Literal["json", "bind"] = "json", _: str = Depends(require_auth)):
    with db() as conn:
        zone = zone_dict(conn, get_zone(conn, zone_id))
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
        zone = get_zone(conn, zone_id)
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
        if conn.execute("SELECT COUNT(*) FROM hosted_zones").fetchone()[0]:
            return
    public = create_zone(ZoneIn(name="example.com", type="public", comment="Public website and mail records"), _="demo")
    private = create_zone(ZoneIn(name="internal.example.com", type="private", comment="Private application records", vpc_region="us-east-1", vpc_id="vpc-0123456789abcdef0"), _="demo")
    for zone_id, record in (
        (public["id"], RecordIn(name="www", type="A", ttl=300, values=["192.0.2.10"])),
        (public["id"], RecordIn(name="mail", type="MX", ttl=300, values=["10 mail.example.com"])),
        (public["id"], RecordIn(name="", type="TXT", ttl=300, values=['"v=spf1 -all"'])),
        (private["id"], RecordIn(name="app", type="A", ttl=60, values=["10.0.1.25"])),
    ):
        create_record(zone_id, record, _="demo")
