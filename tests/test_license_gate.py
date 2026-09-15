from __future__ import annotations

import unittest

from telemetry_yield.license_gate import (
    LicenseGateError,
    assert_export_allowed,
    is_export_allowed,
    unpublishable_source_ids,
    unverified_source_ids,
)
from telemetry_yield.models import LicenseMetadata


class LicenseGateTests(unittest.TestCase):
    def test_all_verified_sources_are_exportable(self) -> None:
        licenses = [LicenseMetadata("golden", "Unlicense", True, True)]
        self.assertTrue(is_export_allowed(licenses))
        assert_export_allowed(licenses)

    def test_unverified_source_blocks_export(self) -> None:
        licenses = [LicenseMetadata("camras", "CC-BY-NC-4.0", False, False)]
        self.assertFalse(is_export_allowed(licenses))
        with self.assertRaisesRegex(LicenseGateError, "camras"):
            assert_export_allowed(licenses)

    def test_mixed_sources_fail_closed_and_are_deduplicated(self) -> None:
        licenses = [
            LicenseMetadata("zenodo", "CC-BY-4.0", True, True),
            LicenseMetadata("camras", "CC-BY-NC-4.0", False, False),
            LicenseMetadata("camras", "CC-BY-NC-4.0", False, False),
            LicenseMetadata("satnogs", "CC-BY-SA-4.0", False, False),
        ]
        self.assertEqual(unverified_source_ids(licenses), ("camras", "satnogs"))

    def test_unreviewed_and_empty_provenance_fail_closed(self) -> None:
        licenses = [LicenseMetadata("satnogs", "CC-BY-SA-4.0", True, False)]
        self.assertEqual(unpublishable_source_ids(licenses), ("satnogs",))
        self.assertFalse(is_export_allowed(licenses))
        self.assertFalse(is_export_allowed([]))
        with self.assertRaisesRegex(LicenseGateError, "empty"):
            assert_export_allowed([])


if __name__ == "__main__":
    unittest.main()
