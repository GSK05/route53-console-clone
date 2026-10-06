"""Registration, ownership, mock-service isolation, and migration regressions."""
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
import main
from security import session_digest, verify_password


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        main.DATABASE = main.Path(self.temp.name) / "accounts.db"
        self.env = patch.dict(os.environ, {"SEED_DEMO": "false"})
        self.env.start()
        self.context = TestClient(main.app)
        self.a = self.context.__enter__()
        self.b = TestClient(main.app)

    def tearDown(self):
        self.b.close()
        self.context.__exit__(None, None, None)
        self.env.stop()
        self.temp.cleanup()

    def signup(self, client, name, account_type="personal"):
        response = client.post("/api/auth/register", json={"username": name, "email": f"{name}@example.com", "password": "A strong password 2026!", "account_name": f"{name}'s account", "account_type": account_type})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def zone(self, client, name="example.com"):
        response = client.post("/api/zones", json={"name": name})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_signup_login_hashing_and_duplicate_identity(self):
        a = self.signup(self.a, "alice")
        b = self.signup(self.b, "bob", "organization")
        self.assertNotEqual(a["account_id"], b["account_id"])
        self.assertEqual(self.a.get("/api/zones").json()["total"], 0)
        self.assertEqual(self.b.get("/api/mock/organizations").json()["organization"]["name"], "bob's account")
        self.assertNotIn("password_hash", a)
        with main.db() as conn:
            hashes = [r[0] for r in conn.execute("SELECT password_hash FROM users ORDER BY username")]
            self.assertNotEqual(hashes[0], hashes[1])
            self.assertTrue(verify_password("A strong password 2026!", hashes[0]))
            self.assertIsNone(conn.execute("SELECT 1 FROM sessions WHERE token=?", (self.a.cookies.get("session"),)).fetchone())
        duplicate = self.b.post("/api/auth/register", json={"username": "ALICE", "email": "other@example.com", "password": "A strong password 2026!", "account_name": "Other"})
        self.assertEqual(duplicate.status_code, 409)
        self.a.post("/api/auth/logout")
        login = self.a.post("/api/auth/login", json={"username": "ALICE@EXAMPLE.COM", "password": "A strong password 2026!"})
        self.assertEqual(login.status_code, 200)
        self.assertEqual(login.json()["account_id"], a["account_id"])
        self.assertIn("HttpOnly", login.headers["set-cookie"])
        self.assertEqual(self.a.get("/api/account").headers["cache-control"], "no-store")

    def test_every_zone_and_record_operation_rejects_other_account(self):
        self.signup(self.a, "alice")
        self.signup(self.b, "bob")
        zone = self.zone(self.a)
        url = f"/api/zones/{zone['id']}"
        payload = {"name": "www", "type": "A", "ttl": 300, "values": ["192.0.2.1"]}
        record = self.a.post(url + "/records", json=payload).json()
        attempts = [
            ("GET", url, None), ("PATCH", url, {"comment": "stolen"}), ("DELETE", url, None),
            ("GET", url + "/records", None), ("POST", url + "/records", payload),
            ("PUT", url + "/records/" + record["id"], payload),
            ("DELETE", url + "/records/" + record["id"], None),
            ("POST", url + "/records/bulk-delete", {"ids": [record["id"]]}),
            ("GET", url + "/export?format=json", None), ("GET", url + "/export?format=bind", None),
            ("POST", url + "/import", {"content": "evil IN A 192.0.2.2"}),
        ]
        for method, route, body in attempts:
            with self.subTest(method=method, route=route):
                self.assertEqual(self.b.request(method, route, json=body).status_code, 404)
        self.assertEqual(self.b.get("/api/zones", params={"q": zone["id"]}).json()["total"], 0)
        own = self.zone(self.b)  # Same domain is allowed in another independent account.
        self.assertNotEqual(zone["id"], own["id"])
        self.assertEqual(self.a.get(url).json()["record_count"], 3)
        self.assertEqual(self.a.get("/api/zones").json()["total"], 1)
        self.assertEqual(self.b.get("/api/zones").json()["items"][0]["id"], own["id"])

    def test_mock_services_are_isolated_and_account_updates_are_private(self):
        a = self.signup(self.a, "alice", "organization")
        self.signup(self.b, "bob")
        iam = self.a.post("/api/mock/iam/users", json={"name": "operator", "policy": "AdministratorAccess"}).json()
        self.assertEqual(self.b.get("/api/mock/iam").json()["identities"], [])
        self.assertEqual(self.b.put(f"/api/mock/iam/users/{iam['id']}", json={"name": "operator", "policy": "AmazonRoute53ReadOnlyAccess"}).status_code, 404)
        self.assertEqual(self.b.delete(f"/api/mock/iam/users/{iam['id']}").status_code, 404)
        self.assertEqual(self.a.put(f"/api/mock/iam/users/{iam['id']}", json={"name": "operator", "policy": "AmazonRoute53ReadOnlyAccess"}).status_code, 200)
        member = self.a.post("/api/mock/organizations/accounts", json={"name": "Sandbox", "email": "sandbox@example.com"}).json()
        self.assertIsNone(self.b.get("/api/mock/organizations").json()["organization"])
        self.assertEqual(self.b.delete(f"/api/mock/organizations/accounts/{member['id']}").status_code, 404)
        self.assertEqual(self.b.post("/api/mock/organizations", json={"name": "Bob org"}).status_code, 201)
        self.assertEqual(self.b.patch("/api/mock/organizations", json={"name": "Renamed org"}).status_code, 200)
        self.assertEqual(self.a.get("/api/mock/organizations").json()["organization"]["name"], "alice's account")
        self.assertEqual(self.a.patch("/api/account", json={"name": "Alice renamed", "account_id": "foreign"}).json()["account_id"], a["account_id"])
        self.assertEqual(self.b.get("/api/account").json()["account_name"], "bob's account")
        self.zone(self.a)
        self.assertEqual(self.a.get("/api/mock/billing").json()["hosted_zones"], 1)
        self.assertEqual(self.b.get("/api/mock/billing").json()["hosted_zones"], 0)
        self.assertEqual(self.a.delete(f"/api/mock/iam/users/{iam['id']}").status_code, 204)
        self.assertEqual(self.a.delete(f"/api/mock/organizations/accounts/{member['id']}").status_code, 204)

    def test_expired_rotated_sessions_and_origin_protection(self):
        self.signup(self.a, "alice")
        old_token = self.a.cookies.get("session")
        self.a.post("/api/auth/login", json={"username": "alice", "password": "A strong password 2026!"})
        self.b.cookies.set("session", old_token)
        self.assertEqual(self.b.get("/api/account").status_code, 401)
        with main.db() as conn:
            conn.execute("UPDATE sessions SET expires_at=? WHERE token=?", ("2000-01-01T00:00:00+00:00", session_digest(self.a.cookies.get("session"))))
        self.assertEqual(self.a.get("/api/auth/me").status_code, 401)
        rejected = self.b.post("/api/auth/login", headers={"Origin": "https://untrusted.example"}, json={"username": "alice", "password": "A strong password 2026!"})
        self.assertEqual(rejected.status_code, 403)

    def test_weak_credentials_and_login_rate_limit(self):
        bad = self.a.post("/api/auth/register", json={"username": "weak", "email": "weak@example.com", "password": "short", "account_name": "Weak"})
        self.assertEqual(bad.status_code, 422)
        for _ in range(30):
            self.assertEqual(self.a.post("/api/auth/login", json={"username": "missing", "password": "wrong"}).status_code, 401)
        self.assertEqual(self.a.post("/api/auth/login", json={"username": "missing", "password": "wrong"}).status_code, 429)

    def test_new_profile_endpoints_require_authentication(self):
        for path in ("/api/account", "/api/mock/iam", "/api/mock/organizations", "/api/mock/billing"):
            self.assertEqual(self.a.get(path).status_code, 401)

    def test_legacy_zone_migration_preserves_data_and_assigns_demo_owner(self):
        # Recreate the original assignment schema in a separate database.
        main.DATABASE = main.Path(self.temp.name) / "legacy.db"
        conn = sqlite3.connect(main.DATABASE)
        conn.executescript("""
        CREATE TABLE hosted_zones (id TEXT PRIMARY KEY,name TEXT,type TEXT,comment TEXT,vpc_region TEXT,vpc_id TEXT,name_servers TEXT,created_at TEXT,updated_at TEXT);
        CREATE TABLE sessions (token TEXT PRIMARY KEY,username TEXT,expires_at TEXT);
        INSERT INTO hosted_zones VALUES ('ZLEGACY','legacy.example.com','public','Keep me',NULL,NULL,'[]','old','old');
        INSERT INTO sessions VALUES ('old-raw-token','demo','2099-01-01');
        """)
        conn.close()
        main.init_db()
        main.init_db()  # Migration is safe to repeat.
        os.environ["SEED_DEMO"] = "true"  # Explicitly enable recovery of local legacy data.
        login = self.a.post("/api/auth/login", json={"username": main.DEMO_USER, "password": main.DEMO_PASSWORD})
        self.assertEqual(login.status_code, 200, login.text)
        self.assertEqual(self.a.get("/api/zones/ZLEGACY").json()["comment"], "Keep me")
        self.signup(self.b, "newcomer")
        self.assertEqual(self.b.get("/api/zones").json()["total"], 0)
        self.assertEqual(self.b.get("/api/zones/ZLEGACY").status_code, 404)

    def test_demo_login_is_disabled_by_default_for_deployment(self):
        with main.db() as conn:
            main.ensure_demo_account(conn)
        credentials = {"username": main.DEMO_USER, "password": main.DEMO_PASSWORD}
        self.assertEqual(self.a.post("/api/auth/login", json=credentials).status_code, 401)
        with patch.dict(os.environ, {"SEED_DEMO": "true"}):
            self.assertEqual(self.a.post("/api/auth/login", json=credentials).status_code, 200)
            self.assertEqual(self.a.get("/api/account").status_code, 200)
        self.assertEqual(self.a.get("/api/account").status_code, 401)


if __name__ == "__main__":
    unittest.main()
