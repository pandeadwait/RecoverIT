"""Integration tests for the RecoverIT FastAPI dashboard endpoints."""

from __future__ import annotations

import unittest
from fastapi.testclient import TestClient

from recoverit.web.app import app


class TestWebApi(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_get_index(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)

    def test_get_scenarios(self):
        resp = self.client.get("/api/scenarios")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 5)
        ids = [s["id"] for s in data]
        aliases = [s.get("alias") for s in data]
        self.assertIn("incident_001", ids)
        self.assertIn("bad_db_config", aliases)

    def test_investigate_scenario(self):
        # Test canonical scenario
        payload = {"scenario": "incident_001", "mode": "offline"}
        resp = self.client.post("/api/investigate", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["incident_id"], "inc-incident_001")
        self.assertEqual(data["status"], "completed")
        self.assertGreater(len(data["ranked_hypotheses"]), 0)
        self.assertIn("markdown_report", data)

        # Test legacy alias
        resp_alias = self.client.post("/api/investigate", json={"scenario": "bad_db_config", "mode": "offline"})
        self.assertEqual(resp_alias.status_code, 200)
        self.assertEqual(resp_alias.json()["incident_id"], "inc-incident_001")

    def test_get_report(self):
        resp = self.client.get("/api/report/incident_001")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Incident Triage Report", resp.text)

        # Test alias
        resp_alias = self.client.get("/api/report/bad_db_config")
        self.assertEqual(resp_alias.status_code, 200)
        self.assertIn("Incident Triage Report", resp_alias.text)


if __name__ == "__main__":
    unittest.main()
