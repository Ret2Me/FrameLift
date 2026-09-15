import subprocess
import unittest
from unittest.mock import patch

from telemetry_yield.environment import _tool_version, collect_environment


class EnvironmentTests(unittest.TestCase):
    def test_tool_version_timeout_is_recorded_instead_of_aborting_inventory(self) -> None:
        with patch(
            "telemetry_yield.environment.subprocess.run",
            side_effect=subprocess.TimeoutExpired("tool", 5),
        ):
            self.assertEqual(
                _tool_version("example", "/usr/bin/example"),
                "unavailable:version-command-timeout",
            )

    def test_records_resource_boundary(self) -> None:
        report = collect_environment()
        self.assertGreater(report["cpu_count"], 0)
        self.assertGreater(report["memory_bytes"], 0)
        self.assertGreater(report["disk"]["free_bytes"], 0)


if __name__ == "__main__":
    unittest.main()
