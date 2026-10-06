"""Workflow tests for the simulated console API."""

import tempfile
import unittest
import os
from unittest.mock import patch

from fastapi.testclient import TestClient

import main


class ConsoleApiTests(unittest.TestCase):
    def setUp(self):
        self.seed_config = patch.dict(os.environ, {"SEED_DEMO": "false"})
        self.seed_config.start()
        self.temp = tempfile.TemporaryDirectory()
        main.DATABASE = main.Path(self.temp.name) / "test.db"
        self.client_context = TestClient(main.app)
        self.client = self.client_context.__enter__()
        response = self.client.post("/api/auth/login", json={"username": main.DEMO_USER, "password": main.DEMO_PASSWORD})
        self.assertEqual(response.status_code, 200)

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        self.temp.cleanup()
        self.seed_config.stop()

    def make_zone(self, name="example.com", **kwargs):
        response = self.client.post("/api/zones", json={"name": name, "type": "public", **kwargs})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def record_url(self, zone):
        return f"/api/zones/{zone['id']}/records"

    def test_zone_record_lifecycle_and_persistence(self):
        response = self.client.post("/api/zones", json={"name": "example.com", "type": "public", "comment": "Initial"})
        self.assertEqual(response.status_code, 201, response.text)
        zone = response.json()
        self.assertEqual(zone["record_count"], 2)
        self.assertEqual(len(zone["name_servers"]), 4)

        response = self.client.post(f"/api/zones/{zone['id']}/records", json={"name": "www", "type": "A", "ttl": 300, "values": ["192.0.2.1", "192.0.2.2"]})
        self.assertEqual(response.status_code, 201, response.text)
        record = response.json()
        self.assertEqual(record["name"], "www.example.com")
        self.assertEqual(self.client.get(f"/api/zones/{zone['id']}/records?q=www").json()["total"], 1)
        self.assertEqual(self.client.get("/api/zones?q=example").json()["total"], 1)

        self.assertEqual(self.client.patch(f"/api/zones/{zone['id']}", json={"comment": "Updated"}).json()["comment"], "Updated")
        response = self.client.put(f"/api/zones/{zone['id']}/records/{record['id']}", json={"name": "www", "type": "AAAA", "ttl": 60, "values": ["2001:db8::1"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["type"], "AAAA")
        self.assertEqual(self.client.delete(f"/api/zones/{zone['id']}").status_code, 409)
        self.assertEqual(self.client.delete(f"/api/zones/{zone['id']}/records/{record['id']}").status_code, 204)
        self.assertEqual(self.client.get(f"/api/zones/{zone['id']}").json()["record_count"], 2)
        self.assertEqual(self.client.delete(f"/api/zones/{zone['id']}").status_code, 204)

    def test_import_export_and_bulk_delete(self):
        zone = self.client.post("/api/zones", json={"name": "example.org", "type": "public"}).json()
        response = self.client.post(f"/api/zones/{zone['id']}/import", json={"content": "$ORIGIN example.org.\n$TTL 300\nwww IN A 192.0.2.10\nmail 600 IN MX 10 mail.example.org.\n"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["imported"], 2)
        exported = self.client.get(f"/api/zones/{zone['id']}/export?format=bind")
        self.assertIn("www 300 IN A 192.0.2.10", exported.text)
        records = self.client.get(f"/api/zones/{zone['id']}/records?page_size=100").json()["items"]
        ids = [r["id"] for r in records if not r["is_default"]]
        self.assertEqual(self.client.post(f"/api/zones/{zone['id']}/records/bulk-delete", json={"ids": ids}).json()["deleted"], 2)

    def test_bind_multiline_soa_and_quoted_comment(self):
        zone = self.client.post("/api/zones", json={"name": "example.org", "type": "public"}).json()
        content = '$ORIGIN example.org.\n@ IN SOA ns.example.org. hostmaster.example.org. (\n 1 7200 900 1209600 86400 )\nnotice IN TXT "part;still-value" ; comment\n'
        response = self.client.post(f"/api/zones/{zone['id']}/import", json={"content": content})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["imported"], 1)

    def test_auth_and_validation(self):
        self.client.post("/api/auth/logout")
        self.assertEqual(self.client.get("/api/zones").status_code, 401)
        self.client.post("/api/auth/login", json={"username": main.DEMO_USER, "password": main.DEMO_PASSWORD})
        zone = self.client.post("/api/zones", json={"name": "example.net", "type": "public"}).json()
        self.assertEqual(self.client.post(f"/api/zones/{zone['id']}/records", json={"name": "", "type": "CNAME", "ttl": 300, "values": ["www.example.net"]}).status_code, 422)
        self.assertEqual(self.client.post(f"/api/zones/{zone['id']}/records", json={"name": "app", "type": "A", "ttl": 300, "values": ["not-an-ip"]}).status_code, 422)

    def test_health_login_session_and_restart_persistence(self):
        self.assertEqual(self.client.get("/api/health").json(), {"status": "ok"})
        self.assertEqual(self.client.get("/api/auth/me").json(), {"username": main.DEMO_USER})
        zone = self.make_zone(comment="Survives restart")
        record = self.client.post(self.record_url(zone), json={"name": "www", "type": "A", "ttl": 300, "values": ["192.0.2.1"]}).json()
        session = self.client.cookies.get("session")
        # A separate client and application lifespan use the same SQLite file.
        with TestClient(main.app) as restarted:
            restarted.cookies.set("session", session)
            self.assertEqual(restarted.get("/api/auth/me").status_code, 200)
            self.assertEqual(restarted.get(f"/api/zones/{zone['id']}").json()["comment"], "Survives restart")
            saved = restarted.get(self.record_url(zone), params={"q": "www"}).json()["items"]
            self.assertEqual(saved[0]["id"], record["id"])
        self.assertEqual(self.client.post("/api/auth/login", json={"username": main.DEMO_USER, "password": "wrong-password"}).status_code, 401)
        self.assertEqual(self.client.post("/api/auth/logout").status_code, 200)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)

    def test_all_required_record_types_crud(self):
        zone = self.make_zone()
        url = self.record_url(zone)
        samples = {
            "A": ["192.0.2.1"], "AAAA": ["2001:db8::1"],
            "CNAME": ["target.example.com."], "TXT": ['"v=spf1 -all"'],
            "MX": ["10 mail.example.com."], "NS": ["ns.example.com."],
            "PTR": ["host.example.com."], "SRV": ["0 5 443 service.example.com."],
            "CAA": ['0 issue "letsencrypt.org"'],
        }
        for kind, values in samples.items():
            with self.subTest(record_type=kind):
                payload = {"name": f"record-{kind.lower()}", "type": kind, "ttl": 300, "values": values}
                response = self.client.post(url, json=payload)
                self.assertEqual(response.status_code, 201, response.text)
                record = response.json()
                listed = self.client.get(url, params={"type": kind, "q": payload["name"]}).json()
                self.assertEqual(listed["total"], 1)
                self.assertEqual(listed["items"][0]["values"], values)
                updated = self.client.put(f"{url}/{record['id']}", json={**payload, "ttl": 60})
                self.assertEqual(updated.status_code, 200, updated.text)
                self.assertEqual(updated.json()["ttl"], 60)
                self.assertEqual(self.client.delete(f"{url}/{record['id']}").status_code, 204)
                self.assertEqual(self.client.get(url, params={"q": payload["name"]}).json()["total"], 0)

    def test_private_zone_tags_and_zone_filter_pagination(self):
        self.make_zone("public.example.com")
        private = self.make_zone("private.example.com", type="private", vpc_region="ap-south-1", vpc_id="vpc-demo", tags=[{"key": "Environment", "value": "Test"}])
        self.assertEqual(private["tags"], [{"key": "Environment", "value": "Test"}])
        self.assertEqual(private["type"], "private")
        self.assertEqual(private["vpc_region"], "ap-south-1")
        self.assertEqual(self.client.get("/api/zones", params={"type": "private"}).json()["total"], 1)
        first = self.client.get("/api/zones", params={"page_size": 1, "page": 1}).json()
        second = self.client.get("/api/zones", params={"page_size": 1, "page": 2}).json()
        self.assertEqual(first["total"], 2)
        self.assertNotEqual(first["items"][0]["id"], second["items"][0]["id"])
        self.assertEqual(self.client.post("/api/zones", json={"name": "bad.example.com", "type": "private"}).status_code, 422)
        self.assertEqual(self.client.post("/api/zones", json={"name": "invalid domain"}).status_code, 422)
        self.assertEqual(self.client.delete(f"/api/zones/{private['id']}").status_code, 204)

    def test_record_pagination_duplicate_and_default_protection(self):
        zone = self.make_zone()
        url = self.record_url(zone)
        payload = {"name": "www", "type": "A", "ttl": 300, "values": ["192.0.2.1"]}
        self.assertEqual(self.client.post(url, json=payload).status_code, 201)
        self.assertEqual(self.client.post(url, json=payload).status_code, 409)
        first = self.client.get(url, params={"page_size": 1, "page": 1}).json()
        second = self.client.get(url, params={"page_size": 1, "page": 2}).json()
        self.assertEqual(first["total"], 3)
        self.assertNotEqual(first["items"][0]["id"], second["items"][0]["id"])
        default = next(r for r in self.client.get(url).json()["items"] if r["type"] == "NS")
        self.assertEqual(self.client.delete(f"{url}/{default['id']}").status_code, 403)
        self.assertEqual(self.client.put(f"{url}/{default['id']}", json={"name": "", "type": "NS", "ttl": 60, "values": ["ns.example.com."]}).status_code, 403)
        self.assertEqual(self.client.post(f"{url}/bulk-delete", json={"ids": [default["id"]]}).status_code, 422)

    def test_json_export_and_import_atomic_errors(self):
        zone = self.make_zone()
        url = self.record_url(zone)
        export = self.client.get(f"/api/zones/{zone['id']}/export?format=json")
        self.assertEqual(export.status_code, 200)
        self.assertEqual(export.json()["zone"]["id"], zone["id"])
        self.assertEqual(len(export.json()["records"]), 2)
        self.assertIn("attachment", export.headers["content-disposition"])
        invalid = self.client.post(f"/api/zones/{zone['id']}/import", json={"content": "www IN A 192.0.2.1\nbroken IN A not-an-ip"})
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(self.client.get(url).json()["total"], 2)
        mismatch = self.client.post(f"/api/zones/{zone['id']}/import", json={"content": "$ORIGIN other.example.com.\nwww IN A 192.0.2.1"})
        self.assertEqual(mismatch.status_code, 422)

    def test_missing_resources_and_unauthenticated_endpoints(self):
        zone = self.make_zone()
        url = self.record_url(zone)
        record_payload = {"name": "www", "type": "A", "ttl": 300, "values": ["192.0.2.1"]}
        self.assertEqual(self.client.get("/api/zones/missing").status_code, 404)
        self.assertEqual(self.client.put(f"{url}/missing", json=record_payload).status_code, 404)
        self.assertEqual(self.client.delete(f"{url}/missing").status_code, 404)
        self.client.post("/api/auth/logout")
        requests = [
            ("GET", "/api/auth/me", None), ("GET", "/api/zones", None),
            ("POST", "/api/zones", {"name": "new.example.com"}),
            ("GET", f"/api/zones/{zone['id']}", None),
            ("PATCH", f"/api/zones/{zone['id']}", {"comment": "edit"}),
            ("DELETE", f"/api/zones/{zone['id']}", None),
            ("GET", url, None), ("POST", url, record_payload),
            ("PUT", f"{url}/missing", record_payload), ("DELETE", f"{url}/missing", None),
            ("POST", f"{url}/bulk-delete", {"ids": ["missing"]}),
            ("POST", f"/api/zones/{zone['id']}/import", {"content": "www IN A 192.0.2.1"}),
            ("GET", f"/api/zones/{zone['id']}/export", None),
        ]
        for method, path, body in requests:
            with self.subTest(method=method, path=path):
                self.assertEqual(self.client.request(method, path, json=body).status_code, 401)


if __name__ == "__main__":
    unittest.main()
