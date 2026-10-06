"""Workflow tests for the simulated console API."""

import tempfile
import unittest

from fastapi.testclient import TestClient

import main


class ConsoleApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        main.DATABASE = main.Path(self.temp.name) / "test.db"
        self.client_context = TestClient(main.app)
        self.client = self.client_context.__enter__()
        response = self.client.post("/api/auth/login", json={"username": "demo", "password": "route53demo"})
        self.assertEqual(response.status_code, 200)

    def tearDown(self):
        self.client_context.__exit__(None, None, None)
        self.temp.cleanup()

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
        self.client.post("/api/auth/login", json={"username": "demo", "password": "route53demo"})
        zone = self.client.post("/api/zones", json={"name": "example.net", "type": "public"}).json()
        self.assertEqual(self.client.post(f"/api/zones/{zone['id']}/records", json={"name": "", "type": "CNAME", "ttl": 300, "values": ["www.example.net"]}).status_code, 422)
        self.assertEqual(self.client.post(f"/api/zones/{zone['id']}/records", json={"name": "app", "type": "A", "ttl": 300, "values": ["not-an-ip"]}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
