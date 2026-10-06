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
        self.credentials = {"username": "tester", "password": "Route53Test!2026"}
        response = self.client.post("/api/auth/register", json={**self.credentials, "email": "tester@example.com", "account_name": "Test account", "account_type": "personal"})
        self.assertEqual(response.status_code, 201, response.text)

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

    def test_bind_shorthand_owners_and_ttl_units(self):
        zone = self.make_zone()
        content = (
            "$ORIGIN example.com.\n$TTL\t1h\n"
            "www IN A 192.0.2.1\n"
            "; A comment and blank line do not reset the previous owner.\n\n"
            "\tIN A 192.0.2.2\n"
            "    1H IN AAAA 2001:db8::1\n"
            "mail IN 1h30m MX 10 mail.example.com.\n"
            "    IN 90M MX 20 backup.example.com.\n"
            "app 1w2d3h4m5s IN A 192.0.2.3\n"
            "$TTL 2H\n"
            "    TXT \"same owner; new default TTL\"\n"
            "a IN A 192.0.2.4\n"
            "mx IN A 192.0.2.5\n"
        )
        response = self.client.post(f"/api/zones/{zone['id']}/import", json={"content": content})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["imported"], 7)
        records = {(r["name"], r["type"]): r for r in self.client.get(self.record_url(zone)).json()["items"]}
        self.assertEqual(records[("www.example.com", "A")]["values"], ["192.0.2.1", "192.0.2.2"])
        self.assertEqual(records[("www.example.com", "A")]["ttl"], 3600)
        self.assertEqual(records[("www.example.com", "AAAA")]["ttl"], 3600)
        self.assertEqual(records[("mail.example.com", "MX")]["ttl"], 5400)
        self.assertEqual(len(records[("mail.example.com", "MX")]["values"]), 2)
        self.assertEqual(records[("app.example.com", "A")]["ttl"], 788645)
        self.assertEqual(records[("app.example.com", "TXT")]["ttl"], 7200)
        self.assertEqual(records[("a.example.com", "A")]["ttl"], 7200)

    def test_bind_skipped_system_record_retains_owner_and_default_ttl(self):
        zone = self.make_zone()
        content = (
            "$TTL 1d\n"
            "@ IN SOA ns.example.com. hostmaster.example.com. (\n"
            " 1 1h 15m 1w 1h )\n"
            "    IN NS ns.example.com.\n"
            "    IN A 192.0.2.1\n"
            "short 30m IN A 192.0.2.2\n"
            "next IN A 192.0.2.3\n"
        )
        response = self.client.post(f"/api/zones/{zone['id']}/import", json={"content": content})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["imported"], 3)
        records = {r["name"]: r for r in self.client.get(self.record_url(zone), params={"type": "A"}).json()["items"]}
        self.assertEqual(records["example.com"]["ttl"], 86400)
        self.assertEqual(records["short.example.com"]["ttl"], 1800)
        self.assertEqual(records["next.example.com"]["ttl"], 86400)

    def test_bind_ttl_inheritance_without_directive(self):
        zone = self.make_zone()
        response = self.client.post(f"/api/zones/{zone['id']}/import", json={"content": "www 30m A 192.0.2.1\n    A 192.0.2.2\nnext A 192.0.2.3"})
        self.assertEqual(response.status_code, 200, response.text)
        records = self.client.get(self.record_url(zone), params={"type": "A"}).json()["items"]
        self.assertEqual([r["ttl"] for r in records], [1800, 1800])

    def test_bind_ttl_boundaries_and_units(self):
        zone = self.make_zone()
        values = [("0", 0), ("1s", 1), ("2M", 120), ("3h", 10800), ("4D", 345600), ("1w", 604800), ("2147483647", 2147483647)]
        content = "\n".join(f"ttl-{i} {ttl} IN A 192.0.2.1" for i, (ttl, _) in enumerate(values))
        response = self.client.post(f"/api/zones/{zone['id']}/import", json={"content": content})
        self.assertEqual(response.status_code, 200, response.text)
        records = {r["name"]: r["ttl"] for r in self.client.get(self.record_url(zone), params={"type": "A"}).json()["items"]}
        for i, (_, expected) in enumerate(values):
            self.assertEqual(records[f"ttl-{i}.example.com"], expected)

    def test_bind_invalid_shorthand_and_ttl_fail_without_partial_import(self):
        zone = self.make_zone()
        url = f"/api/zones/{zone['id']}/import"
        missing_owner = self.client.post(url, json={"content": "    IN A 192.0.2.1\n"})
        self.assertEqual(missing_owner.status_code, 422)
        self.assertIn("preceding record", missing_owner.json()["detail"]["errors"][0])
        bad_rows = [
            "$TTL 1y", "$TTL -1", "$TTL 1h30mgarbage", "$TTL 2147483648",
            "bad 9999w IN A 192.0.2.2", "bad 1h 2h IN A 192.0.2.2",
            "bad IN IN A 192.0.2.2", "bad IN 1.5h A 192.0.2.2",
            "    2h IN A 192.0.2.2",  # Same name/type with inconsistent TTL.
        ]
        for row in bad_rows:
            with self.subTest(row=row):
                response = self.client.post(url, json={"content": "valid 1h IN A 192.0.2.1\n" + row})
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(self.client.get(self.record_url(zone)).json()["total"], 2)

    def test_auth_and_validation(self):
        self.client.post("/api/auth/logout")
        self.assertEqual(self.client.get("/api/zones").status_code, 401)
        self.client.post("/api/auth/login", json=self.credentials)
        zone = self.client.post("/api/zones", json={"name": "example.net", "type": "public"}).json()
        self.assertEqual(self.client.post(f"/api/zones/{zone['id']}/records", json={"name": "", "type": "CNAME", "ttl": 300, "values": ["www.example.net"]}).status_code, 422)
        self.assertEqual(self.client.post(f"/api/zones/{zone['id']}/records", json={"name": "app", "type": "A", "ttl": 300, "values": ["not-an-ip"]}).status_code, 422)

    def test_health_login_session_and_restart_persistence(self):
        self.assertEqual(self.client.get("/api/health").json(), {"status": "ok"})
        self.assertEqual(self.client.get("/api/auth/me").json()["username"], self.credentials["username"])
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
        self.assertEqual(self.client.post("/api/auth/login", json={"username": self.credentials["username"], "password": "wrong-password"}).status_code, 401)
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

    def test_hosted_zone_form_payload_and_validation_details(self):
        payload = {"name": " EXAMPLE.COM. ", "type": "public", "comment": "Form submission", "vpc_region": None, "vpc_id": None, "tags": [{"key": "Environment", "value": "Demo"}]}
        created = self.client.post("/api/zones", json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["name"], "example.com")
        for name in ("https://example.com", "example.com/path", "myzone", "invalid domain.com", ""):
            with self.subTest(name=name):
                response = self.client.post("/api/zones", json={**payload, "name": name})
                self.assertEqual(response.status_code, 422)
                detail = response.json()["detail"][0]
                self.assertEqual(detail["loc"], ["body", "name"])
                self.assertIn("example.com", detail["msg"])

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
