import io
import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer

from nos_monitor import sample
from nos_monitor.locations import Locations
from nos_monitor.market import observe_outage, summarise_dispatch
from nos_monitor.mms_csv import parse_text
from nos_monitor.server import make_handler
from nos_monitor.service import Monitor
from nos_monitor.store import Store

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _zip(name: str, text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, text)
    return buf.getvalue()


class FakeNemweb:
    """Serves a configurable listing + files; can be switched to fail."""

    def __init__(self):
        self.files = {}
        self.fail = False

    def put(self, name, data):
        self.files[name] = data

    def __call__(self, url):
        if self.fail:
            raise OSError("simulated network failure")
        if url.endswith("/"):
            rows = "".join(
                f' Monday, October 12, 2026  9:01 AM  {len(d)} <A HREF="{url}{n}">{n}</A><br>' for n, d in self.files.items()
            )
            return f"<pre>{rows}</pre>".encode()
        return self.files[url.rsplit("/", 1)[1]]


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.data = os.path.join(self.tmp, "data")
        os.makedirs(self.data)
        with open(os.path.join(self.data, "substation_locations.csv"), "w") as fh:
            fh.write("substation_id,name,latitude,longitude,source,source_ref,notes\n"
                     "DEMO_NSW_A,A,-33.4,150.2,test source,,\n"
                     "DEMO_QLD_A,A,-27.3,152.4,,,\n"          # no source -> ignored
                     "DEMO_VIC_A,A,10,10,test,,\n")            # outside Australia -> ignored
        self.fake = FakeNemweb()
        self.store = Store(os.path.join(self.tmp, "nos.sqlite"))
        self.monitor = Monitor(self.store, Locations(self.data), self.tmp, getter=self.fake,
                               network_url="https://nemweb.test/Network/", enable_market=False)

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp)

    def test_fetch_diff_and_keep_last_good_on_failure(self):
        self.fake.put("PUBLIC_NETWORK_202610120830_01.zip", _zip("a.CSV", sample.network_csv(1)[1]))
        r1 = self.monitor.refresh(force=True)["network"]
        self.assertTrue(r1["ok"] and r1["new"], r1)

        # same file again -> no new snapshot
        r = self.monitor.refresh(force=True)["network"]
        self.assertTrue(r["ok"])
        self.assertFalse(r["new"])
        self.assertEqual(len(self.store.snapshot_list()), 1)

        self.fake.put("PUBLIC_NETWORK_202610120900_02.zip", _zip("b.CSV", sample.network_csv(2)[1]))
        r2 = self.monitor.refresh(force=True)["network"]
        self.assertEqual(r2["changes"], 5)

        self.fake.fail = True
        r3 = self.monitor.refresh(force=True)["network"]
        self.assertFalse(r3["ok"])
        state = self.monitor.state()
        self.assertEqual(state["source"]["file"], "PUBLIC_NETWORK_202610120900_02.zip")
        self.assertTrue(state["stale"])
        self.assertIn("simulated network failure", state["last_error"])
        self.assertTrue(state["outages"])

    def test_empty_or_missing_outage_table_is_rejected(self):
        self.fake.put("PUBLIC_NETWORK_202610120830_01.zip", _zip("a.CSV", sample.network_csv(1)[1]))
        self.monitor.refresh(force=True)
        self.fake.put("PUBLIC_NETWORK_202610120900_02.zip",
                      _zip("b.CSV", "C,X\nI,NETWORK,OUTAGEDETAIL,3,OUTAGEID\nC,END\n"))
        r = self.monitor.refresh(force=True)["network"]
        self.assertFalse(r["ok"])
        self.assertIn("is empty", r["error"])
        self.assertEqual(self.monitor.state()["source"]["file"], "PUBLIC_NETWORK_202610120830_01.zip")

    def test_locations_only_use_sourced_coordinates(self):
        self.fake.put("PUBLIC_NETWORK_202610120830_01.zip", _zip("a.CSV", sample.network_csv(1)[1]))
        self.monitor.refresh(force=True)
        by = {o["outage_id"]: o for o in self.monitor.state()["outages"]}
        self.assertTrue(by["DEMO-1001"]["mapped"])
        self.assertFalse(by["DEMO-1002"]["mapped"])   # QLD row had no source
        self.assertFalse(by["DEMO-1003"]["mapped"])   # VIC row outside Australia
        self.assertEqual(len(self.monitor.locations.problems), 2)

    def test_market_observation_without_membership_is_unconfirmed(self):
        market = summarise_dispatch(parse_text(sample.dispatch_csv()[1]))
        self.assertEqual(market["binding_constraints"][0]["constraint_id"], "DEMO_N>>L1_A")
        rec = {"constraint_sets": [{"id": "DEMO-N-SET1"}, {"id": "UNKNOWN"}], "regions": ["NSW1"]}
        obs = observe_outage(rec, market, {"DEMO-N-SET1": ["DEMO_N>>L1_A"]})
        self.assertEqual([c["status"] for c in obs["constraint_sets"]], ["binding", "unknown"])
        self.assertIn("NSW1-QLD1", obs["interconnectors"])
        self.assertFalse(observe_outage(rec, None, {})["available"])


class ServerTests(unittest.TestCase):
    def test_page_and_api(self):
        tmp = tempfile.mkdtemp()
        store = Store(os.path.join(tmp, "nos.sqlite"))
        try:
            monitor = Monitor(store, Locations(os.path.join(ROOT, "data")), tmp, enable_network=False, enable_market=False)
            path = os.path.join(tmp, sample.network_csv(1)[0])
            with open(path, "w") as fh:
                fh.write(sample.network_csv(1)[1])
            monitor.import_file(path, kind="sample")
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(monitor))
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            base = f"http://127.0.0.1:{httpd.server_address[1]}"
            try:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                html = opener.open(base + "/").read().decode()
                self.assertIn("NEM Transmission Outage Monitor", html)
                self.assertEqual(opener.open(base + "/app.js").status, 200)
                self.assertEqual(opener.open(base + "/vendor/leaflet/leaflet.js").status, 200)
                state = json.loads(opener.open(base + "/api/state").read())
                self.assertEqual(len(state["outages"]), 7)
                self.assertEqual(state["source"]["kind"], "sample")
                req = urllib.request.Request(base + "/api/refresh?force=1", method="POST")
                self.assertIn("refresh_result", json.loads(opener.open(req).read()))
                with self.assertRaises(urllib.error.HTTPError):
                    opener.open(base + "/../nos_monitor/service.py")
            finally:
                httpd.shutdown()
                httpd.server_close()
        finally:
            store.close()
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
