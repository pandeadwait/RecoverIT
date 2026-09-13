"""Unit tests for live Git and FileLog adapters."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from collectors.changes.git_adapter import LocalGitChangeAdapter
from collectors.logs.file_adapter import FileLogAdapter
from contracts.collection.query_plan import EvidenceQuery
from contracts.enums import InformationValue, SourceStatus, SourceType


class TestLocalGitChangeAdapter(unittest.TestCase):
    def test_git_adapter_queries_local_repo(self):
        adapter = LocalGitChangeAdapter(repo_path=".")
        self.assertTrue(adapter.is_git_repository())
        cap = adapter.get_capability()
        self.assertTrue(cap.available)
        self.assertEqual(cap.source_type, SourceType.CHANGES)

        query = EvidenceQuery(
            query_id="q_git_test",
            source_type=SourceType.CHANGES,
            question="Get recent commits",
            parameters={"limit": 2},
            expected_information_value=InformationValue.HIGH,
        )
        res = adapter.query(query)
        self.assertEqual(res.source_status, SourceStatus.OK)
        self.assertGreater(len(res.records), 0)
        rec = res.records[0]
        self.assertTrue(rec.source_record_id.startswith("commit-"))
        self.assertIn("commit_sha", rec.payload)
        self.assertIn("author", rec.payload)


class TestFileLogAdapter(unittest.TestCase):
    def test_file_log_adapter_parses_errors(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            log_file = Path(tmp_dir) / "app.log"
            log_file.write_text(
                "2026-09-12T10:00:00Z [INFO] app: starting up\n"
                "2026-09-12T10:01:00Z [ERROR] app: connection failed to db:5432\n"
                "2026-09-12T10:02:00Z [CRITICAL] app: connection pool exhausted\n",
                encoding="utf-8",
            )

            adapter = FileLogAdapter(log_path=log_file, service_name="test-service")
            self.assertTrue(adapter.exists())

            query = EvidenceQuery(
                query_id="q_log_test",
                source_type=SourceType.LOGS,
                question="Find errors",
                parameters={"limit": 10},
                expected_information_value=InformationValue.HIGH,
            )
            res = adapter.query(query)
            self.assertEqual(res.source_status, SourceStatus.OK)
            self.assertEqual(len(res.records), 2)  # Only ERROR and CRITICAL
            self.assertEqual(res.records[0].payload["level"], "error")
            self.assertEqual(res.records[1].payload["level"], "critical")


if __name__ == "__main__":
    unittest.main()
