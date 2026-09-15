from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import unittest


SCRIPT = Path(__file__).parents[1] / "work/polyitan/download_inventory_subset.py"
SPEC = spec_from_file_location("download_inventory_subset", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def inventory(*objects):
    return {
        "source": {
            "bucket_url": "https://example.test/bucket/",
            "payload_objects_downloaded": False,
        },
        "objects": list(objects),
    }


class ExplicitSubsetTests(unittest.TestCase):
    def test_selects_only_requested_positive_objects(self):
        document = inventory(
            {"observation_id": 1, "key": "observation_1.iq", "size_bytes": 12},
            {"observation_id": 2, "key": "observation_2.iq", "size_bytes": 0},
        )
        selected = MODULE.select_explicit_subset(document, [1])
        self.assertEqual([item.observation_id for item in selected], [1])
        self.assertEqual(selected[0].url, "https://example.test/bucket/observation_1.iq")

    def test_rejects_missing_duplicate_or_invalid_selected_object(self):
        good = {"observation_id": 1, "key": "observation_1.iq", "size_bytes": 12}
        with self.assertRaises(ValueError):
            MODULE.select_explicit_subset(inventory(good), [1, 1])
        with self.assertRaises(ValueError):
            MODULE.select_explicit_subset(inventory(good), [2])
        with self.assertRaises(ValueError):
            MODULE.select_explicit_subset(
                inventory({"observation_id": 1, "key": "observation_1.iq", "size_bytes": 0}),
                [1],
            )

    def test_requires_metadata_only_https_inventory(self):
        document = inventory({"observation_id": 1, "key": "observation_1.iq", "size_bytes": 12})
        document["source"]["payload_objects_downloaded"] = True
        with self.assertRaises(ValueError):
            MODULE.select_explicit_subset(document, [1])
        document["source"]["payload_objects_downloaded"] = False
        document["source"]["bucket_url"] = "http://example.test/bucket/"
        with self.assertRaises(ValueError):
            MODULE.select_explicit_subset(document, [1])


if __name__ == "__main__":
    unittest.main()
