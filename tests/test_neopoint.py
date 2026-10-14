import os
import tempfile
import unittest
import urllib.error
from unittest import mock

from nos_monitor import neopoint
from nos_monitor.nemweb import FetchError

FAKE_KEY = "TESTKEY-not-a-real-key/+="  # contains characters that change under URL-encoding
TEMPLATE = "https://neopoint.example/Service/Csv?f=Report&from={from}&key={key}"


def env(**extra):
    base = {neopoint.KEY_ENV: FAKE_KEY, "NEOPOINT_URL_NOS_OUTAGES": TEMPLATE}
    base.update(extra)
    return mock.patch.dict(os.environ, base, clear=False)


class RedactionTests(unittest.TestCase):
    def test_redact_plain_encoded_and_generic(self):
        enc = neopoint.urllib.parse.quote(FAKE_KEY, safe="")
        text = f"a {FAKE_KEY} b https://x/?key={enc}&apikey=zzz&token=t1"
        out = neopoint.redact(text, FAKE_KEY)
        self.assertNotIn(FAKE_KEY, out)
        self.assertNotIn(enc, out)
        self.assertNotIn("zzz", out)
        self.assertNotIn("t1", out)

    def test_build_url_requires_placeholder(self):
        with self.assertRaises(ValueError):
            neopoint.build_url("https://x/?key=abc", FAKE_KEY)
        url = neopoint.build_url(TEMPLATE, FAKE_KEY)
        self.assertNotIn("{from}", url)
        self.assertIn(neopoint.urllib.parse.quote(FAKE_KEY, safe=""), url)


class InstanceTests(unittest.TestCase):
    NOS = ("https://neopoint.com.au/Service/Csv?f=106%20Flows%20and%20Constraints%5CNOS%20Planned%20Outages%20by%20Region"
           "&from={from}%2000%3A00&period=Daily&instances={instances}&section=-1&key={key}")

    def test_instances_from_env_and_url(self):
        with env(NEOPOINT_URL_NOS_OUTAGES=self.NOS, NEOPOINT_INSTANCES_NOS_OUTAGES="NSW1, QLD1,VIC1"):
            self.assertEqual(neopoint.instances("nos_outages"), ["NSW1", "QLD1", "VIC1"])
        url = neopoint.build_url(self.NOS, FAKE_KEY, "QLD1")
        self.assertIn("instances=QLD1&section=-1", url)
        self.assertRegex(url, r"from=\d{4}-\d{2}-\d{2}%2000%3A00&period=Daily")
        with self.assertRaises(ValueError):
            neopoint.build_url(self.NOS, FAKE_KEY)  # {instances} needs a value
        with env(NEOPOINT_URL_NOS_OUTAGES=TEMPLATE):
            self.assertEqual(neopoint.instances("nos_outages"), [None])


class FetchTests(unittest.TestCase):
    def _assert_no_key(self, exc: FetchError):
        enc = neopoint.urllib.parse.quote(FAKE_KEY, safe="")
        for text in (str(exc), repr(exc.as_dict()), exc.url, exc.detail):
            self.assertNotIn(FAKE_KEY, text)
            self.assertNotIn(enc, text)

    def test_http_error_does_not_leak_key(self):
        def opener(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 401, f"Unauthorized for {req.full_url}", {}, None)

        with env():
            with self.assertRaises(FetchError) as ctx:
                neopoint.fetch("nos_outages", opener=opener)
        self.assertEqual((ctx.exception.stage, ctx.exception.status), ("download", 401))
        self.assertIsNone(ctx.exception.__cause__)  # original exception (with key in URL) not chained
        self._assert_no_key(ctx.exception)

    def test_proxy_refusal_is_reported(self):
        def opener(req, timeout=None):
            raise urllib.error.URLError("Tunnel connection failed: 403 Forbidden")

        with env():
            with self.assertRaises(FetchError) as ctx:
                neopoint.fetch("nos_outages", opener=opener)
        self.assertEqual((ctx.exception.status, ctx.exception.via_proxy), (403, True))
        self._assert_no_key(ctx.exception)

    def test_missing_configuration(self):
        with mock.patch.dict(os.environ, {neopoint.KEY_ENV: "", "NEOPOINT_URL_CONSTRAINTS": ""}):
            with self.assertRaises(FetchError) as ctx:
                neopoint.fetch("constraints")
        self.assertEqual(ctx.exception.stage, "config")
        with mock.patch.dict(os.environ, {neopoint.KEY_ENV: "", "NEOPOINT_URL_CONSTRAINTS": TEMPLATE}):
            with self.assertRaises(FetchError) as ctx:
                neopoint.fetch("constraints")
        self.assertIn(neopoint.KEY_ENV, ctx.exception.detail)

    def test_success_returns_redacted_url(self):
        class Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"A,B\n1,2\n"

        with env():
            body, url = neopoint.fetch("nos_outages", opener=lambda req, timeout=None: Resp())
        self.assertEqual(body, b"A,B\n1,2\n")
        self.assertIn("key=***", url)


class DotenvAndInspectTests(unittest.TestCase):
    def test_dotenv_does_not_override_existing_env(self):
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as fh:
            fh.write('# comment\nexport NEOPOINT_T1="from-file"\nNEOPOINT_T2=file2\n')
        try:
            with mock.patch.dict(os.environ, {"NEOPOINT_T2": "from-env"}):
                neopoint.load_dotenv(fh.name)
                self.assertEqual(os.environ["NEOPOINT_T1"], "from-file")
                self.assertEqual(os.environ["NEOPOINT_T2"], "from-env")
                os.environ.pop("NEOPOINT_T1")
        finally:
            os.remove(fh.name)

    def test_inspect_reports_what_is_there(self):
        # Hand-made CSV for the inspector only; it says nothing about NEOpoint's real layout.
        data = b"\xef\xbb\xbfWhen,Thing,Value\n13/10/2026 10:05,a,1\n13/10/2026 10:10,b,2\n"
        info = neopoint.inspect_csv(data)
        self.assertEqual(info["columns"], ["When", "Thing", "Value"])
        self.assertEqual(info["rows"], 2)
        self.assertEqual(info["time_columns"]["When"]["min"], "2026-10-13T10:05:00+10:00")
        self.assertEqual(info["time_columns"]["When"]["max"], "2026-10-13T10:10:00+10:00")
        self.assertIn("HTML", neopoint.inspect_csv(b"<html>login</html>").get("note", ""))


if __name__ == "__main__":
    unittest.main()
