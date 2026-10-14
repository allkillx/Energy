import json
import os
import shutil
import tempfile
import unittest

from nos_monitor import ga_import
from nos_monitor.locations import load_substations

# Synthetic GA-shaped features: names and coordinates are made up for the test.
GA = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "properties": {"name": "Alpha Substation", "state": "New South Wales", "objectid": 1},
     "geometry": {"type": "Point", "coordinates": [150.0, -33.0]}},
    {"type": "Feature", "properties": {"name": "Alpha Terminal Station", "state": "Victoria", "objectid": 2},
     "geometry": {"type": "Point", "coordinates": [145.0, -37.0]}},
    {"type": "Feature", "properties": {"name": "Unrelated", "state": "New South Wales", "objectid": 3},
     "geometry": {"type": "Point", "coordinates": [151.0, -32.0]}},
]}


class GaImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ga_path = os.path.join(self.tmp, "ga.geojson")
        with open(self.ga_path, "w") as fh:
            json.dump(GA, fh)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_suggest_filters_by_state_and_leaves_unconfirmed(self):
        rows = ga_import.suggest({"ALPHA": ("Alpha 330kV", "NSW1"), "ZZZ": ("Nowhere", "QLD1")},
                                 ga_import.load_ga(self.ga_path))
        alpha = [r for r in rows if r["substation_id"] == "ALPHA"]
        self.assertEqual([r["source_ref"] for r in alpha], [1])          # VIC namesake excluded by state
        self.assertTrue(all(r["confirmed"] == "" for r in rows))
        self.assertEqual([r["candidate_name"] for r in rows if r["substation_id"] == "ZZZ"], [""])

    def test_apply_only_confirmed_rows_with_source(self):
        rows = ga_import.suggest({"ALPHA": ("Alpha", "NSW1")}, ga_import.load_ga(self.ga_path))
        review = os.path.join(self.tmp, "review.csv")
        ga_import.write_suggestions(rows, review, "ga.geojson")
        target = os.path.join(self.tmp, "substation_locations.csv")
        with open(target, "w") as fh:
            fh.write("substation_id,name,latitude,longitude,source,source_ref,notes\n")
        self.assertEqual(ga_import.apply_confirmed(review, target, "GA test")[0], 0)  # nothing confirmed yet
        with open(review) as fh:
            text = fh.read()
        with open(review, "w") as fh:
            fh.write(text.replace(",1,\n", ",1,yes\n"))
        n, problems = ga_import.apply_confirmed(review, target, "GA test")
        self.assertEqual((n, problems), (1, []))
        subs, probs = load_substations(target)
        self.assertEqual(subs["ALPHA"]["source"], "GA test")
        self.assertEqual((subs["ALPHA"]["lat"], subs["ALPHA"]["lon"]), (-33.0, 150.0))
        self.assertEqual(ga_import.apply_confirmed(review, target, "GA test")[0], 0)  # never overwrites


if __name__ == "__main__":
    unittest.main()
