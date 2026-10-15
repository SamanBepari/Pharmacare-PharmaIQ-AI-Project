import io
import re
import unittest
from tests.helpers import build_db


class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import app as A
        cls.A = A
        cls.db = build_db()
        A.DATABASE = cls.db
        A.app.config["PHARMAIQ_DB"] = cls.db
        A.app.config["TESTING"] = True

    def client(self, user, pw):
        c = self.A.app.test_client()
        self.assertEqual(c.post("/login", data={"username": user, "password": pw}).status_code, 302)
        token = re.search(r'name="csrf-token" content="([^"]+)"', c.get("/ai").get_data(as_text=True)).group(1)
        return c, {"X-CSRF-Token": token}

    def test_requires_login(self):
        c = self.A.app.test_client()
        self.assertEqual(c.post("/api/ai/ask", json={"question": "x"}).status_code, 401)
        self.assertEqual(c.get("/ai").status_code, 302)

    def test_api_key_shortcut_not_accepted(self):
        c = self.A.app.test_client()
        r = c.post("/api/ai/ask", json={"question": "x"}, headers={"X-API-Key": self.A.app.secret_key})
        self.assertEqual(r.status_code, 401)

    def test_csrf_required(self):
        c, h = self.client("admin", "admin123")
        self.assertEqual(c.post("/api/ai/ask", json={"question": "low stock"}).status_code, 400)
        self.assertEqual(c.post("/api/ai/ask", json={"question": "low stock"}, headers={"X-CSRF-Token": "wrong"}).status_code, 400)
        self.assertEqual(c.post("/api/ai/ask", json={"question": "low stock"}, headers=h).status_code, 200)

    def test_bad_payload(self):
        c, h = self.client("admin", "admin123")
        self.assertEqual(c.post("/api/ai/ask", json={"question": 5}, headers=h).status_code, 400)

    def test_role_access_to_pages_and_apis(self):
        st, _ = self.client("staff", "staff123")
        self.assertEqual(st.get("/ai").status_code, 200)
        self.assertEqual(st.get("/ai/insights").status_code, 302)
        self.assertEqual(st.get("/ai/audit").status_code, 302)
        self.assertEqual(st.get("/api/ai/insights/forecast").status_code, 403)
        self.assertEqual(st.post("/api/ai/evaluate").status_code, 403)
        ph, _ = self.client("pharmacist", "pharma123")
        self.assertEqual(ph.get("/ai/insights").status_code, 200)
        self.assertEqual(ph.get("/ai/audit").status_code, 302)

    def test_tools_endpoint_is_role_filtered(self):
        st, _ = self.client("staff", "staff123")
        names = {t["name"] for t in st.get("/api/ai/tools").get_json()}
        self.assertNotIn("forecast_demand", names)
        ad, _ = self.client("admin", "admin123")
        self.assertIn("forecast_demand", {t["name"] for t in ad.get("/api/ai/tools").get_json()})

    def test_export_owner_only_and_formula_safe(self):
        ad, h = self.client("admin", "admin123")
        rid = ad.post("/api/ai/ask", json={"question": "Show low stock items"}, headers=h).get_json()["evidence"]["result_id"]
        self.assertEqual(ad.get(f"/api/ai/export/{rid}.xlsx").status_code, 200)
        self.assertEqual(ad.get(f"/api/ai/export/{rid}.csv").status_code, 200)
        st, _ = self.client("staff", "staff123")
        self.assertEqual(st.get(f"/api/ai/export/{rid}.xlsx").status_code, 404)
        self.assertEqual(ad.get(f"/api/ai/export/{rid}.exe").status_code, 404)
        from pharmaiq.exports import _safe
        self.assertEqual(_safe("=CMD()"), "'=CMD()")

    def test_upload_flow_and_visibility(self):
        ad, h = self.client("admin", "admin123")
        r = ad.post("/ai/knowledge/upload", data={"csrf_token": h["X-CSRF-Token"], "doc_type": "sop", "min_role": "admin",
                    "document": (io.BytesIO(b"# Vault SOP\n\nThe vault combination is rotated every Tuesday morning.\n"), "vault.md")},
                    content_type="multipart/form-data", follow_redirects=True)
        self.assertIn("searchable passages", r.get_data(as_text=True))
        self.assertTrue(ad.post("/api/ai/ask", json={"question": "When is the vault combination rotated?"}, headers=h).get_json()["sources"])
        st, hs = self.client("staff", "staff123")
        titles = [s["title"] for s in st.post("/api/ai/ask", json={"question": "When is the vault combination rotated?"}, headers=hs).get_json()["sources"]]
        self.assertFalse(any("Vault" in t for t in titles))
        bad = ad.post("/ai/knowledge/upload", data={"doc_type": "sop", "document": (io.BytesIO(b"x"), "a.md")}, content_type="multipart/form-data", follow_redirects=True)
        self.assertIn("Security token", bad.get_data(as_text=True))

    def test_staff_cannot_upload_or_delete(self):
        st, hs = self.client("staff", "staff123")
        r = st.post("/ai/knowledge/upload", data={"csrf_token": hs["X-CSRF-Token"], "document": (io.BytesIO(b"hi there"), "a.txt")},
                    content_type="multipart/form-data")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/", r.headers["Location"])
        self.assertEqual(st.post("/ai/knowledge/delete/1", data={"csrf_token": hs["X-CSRF-Token"]}).status_code, 302)

    def test_existing_app_still_works(self):
        ad, _ = self.client("admin", "admin123")
        for p in ("/", "/medicines", "/alerts", "/sales", "/reports", "/logs", "/users", "/barcode"):
            self.assertEqual(ad.get(p).status_code, 200, p)
        self.assertEqual(ad.get("/api/stats").status_code, 200)


if __name__ == "__main__":
    unittest.main()
