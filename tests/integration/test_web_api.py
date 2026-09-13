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
        self.assertIn("bad_db_config", ids)
        self.assertIn("memory_exhaustion", ids)

    def test_investigate_scenario(self):
        payload = {"scenario": "bad_db_config", "mode": "offline"}
        resp = self.client.post("/api/investigate", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["incident_id"], "inc-bad_db_config")
        self.assertEqual(data["status"], "completed")
        self.assertGreater(len(data["ranked_hypotheses"]), 0)
        self.assertIn("markdown_report", data)

    def test_get_report(self):
        resp = self.client.get("/api/report/bad_db_config")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Incident Triage Report", resp.text)


if __name__ == "__main__":
    unittest.main()
