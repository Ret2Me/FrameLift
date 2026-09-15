import unittest
from pathlib import Path


class SchemaContractTests(unittest.TestCase):
    def test_postgres_ddl_contains_core_integrity_constraints(self) -> None:
        ddl = (
            Path(__file__).parents[1] / "db" / "migrations" / "001_initial.sql"
        ).read_text(encoding="utf-8")
        self.assertNotIn("line1/2", ddl)
        self.assertNotIn("station_lat/lon", ddl)
        self.assertIn("lease_token TEXT NOT NULL", ddl)
        self.assertIn("transmission_event_id TEXT NOT NULL", ddl)
        self.assertIn("publication_reviewed BOOLEAN NOT NULL DEFAULT FALSE", ddl)
        self.assertNotIn("CREATE UNIQUE INDEX unique_valid_event_payload", ddl)


if __name__ == "__main__":
    unittest.main()
