"""Integration tests for InvestigationRunner."""

from __future__ import annotations

import unittest
from pathlib import Path

from recoverit.runner import InvestigationRunner


class TestInvestigationRunner(unittest.IsolatedAsyncioTestCase):
    async def test_run_scenario_bad_db_config(self):
        runner = InvestigationRunner()
        # Test canonical ID
        result = await runner.run_scenario("incident_001", mode="offline")
        self.assertEqual(result.incident_id, "inc-incident_001")
        self.assertEqual(result.service, "payment-api")
        self.assertEqual(result.status, "completed")
        self.assertGreater(len(result.ranked_hypotheses), 0)
        self.assertEqual(result.ranked_hypotheses[0]["rank"], 1)

        # Test legacy alias resolves to same canonical incident
        result_alias = await runner.run_scenario("bad_db_config", mode="offline")
        self.assertEqual(result_alias.incident_id, "inc-incident_001")
        self.assertEqual(result_alias.service, "payment-api")

        # Check markdown report generation
        report = result.to_markdown_report()
        self.assertIn("# Incident Triage Report: inc-incident_001", report)
        self.assertIn("## Ranked Root Cause Hypotheses", report)
        self.assertIn("payment-api", report)

    async def test_run_target_demo_service(self):
        runner = InvestigationRunner()
        demo_dir = Path("examples/demo_service")
        if demo_dir.exists() and (demo_dir / ".git").exists():
            result = await runner.run_target(
                repo_path=demo_dir,
                log_path=demo_dir / "logs" / "payment-api.log",
                service_name="payment-api",
                mode="offline",
            )
            self.assertEqual(result.service, "payment-api")
            self.assertEqual(result.status, "completed")
            self.assertGreater(len(result.ranked_hypotheses), 0)


if __name__ == "__main__":
    unittest.main()
