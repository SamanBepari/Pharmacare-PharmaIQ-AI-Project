import unittest
from datetime import timedelta
from tests.helpers import TODAY, build_db, user
from pharmaiq import analytics, config
from pharmaiq.sql_guard import ReadOnlySession
from pharmaiq.tools import ToolContext, ToolError, run_tool, tools_for_role


class ToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = build_db(with_kb=False)
        cls.settings = config.get_settings()

    def run_tool(self, name, args=None, role="admin"):
        u = user(self.db, role)
        with ReadOnlySession(self.db, role, u["username"]) as s:
            return run_tool(name, args or {}, ToolContext(s, role, u["username"], TODAY, self.settings))

    def test_expiring_uses_pinned_date_and_excludes_expired(self):
        r = self.run_tool("get_expiring_medicines", {"days": 30})
        names = [row[0] for row in r.primary.rows]
        self.assertEqual(names, ["Cetirizine 5mg/5ml"])
        self.assertEqual(r.primary.rows[0][5], 12)                    # days_left
        self.assertIn("2026-06-15", r.assumptions[0])

    def test_expiring_include_expired(self):
        names = [row[0] for row in self.run_tool("get_expiring_medicines", {"days": 30, "include_expired": True}).primary.rows]
        self.assertIn("Ibuprofen 400mg", names)

    def test_expired(self):
        self.assertEqual([r[0] for r in self.run_tool("get_expired_medicines").primary.rows], ["Ibuprofen 400mg"])

    def test_low_stock_rule_matches_alerts_page(self):
        names = {r[0] for r in self.run_tool("get_low_stock_items").primary.rows}
        self.assertEqual(names, {"Warfarin 5mg", "Salbutamol 100mcg"})

    def test_low_stock_category_filter(self):
        names = {r[0] for r in self.run_tool("get_low_stock_items", {"category": "inhaler"}).primary.rows}
        self.assertEqual(names, {"Salbutamol 100mcg"})

    def test_stock_level_status(self):
        rows = self.run_tool("get_stock_level", {"name": "cetiriz"}).primary.rows
        self.assertEqual(rows[0][-1], "Expiring soon")

    def test_argument_validation(self):
        with self.assertRaises(ToolError):
            self.run_tool("get_sales_summary", {"period": "last_century"})
        with self.assertRaises(ToolError):
            self.run_tool("get_low_stock_items", {"threshold": "abc"})
        with self.assertRaises(ToolError):
            self.run_tool("not_a_tool")

    def test_sql_injection_in_arguments_is_just_data(self):
        r = self.run_tool("get_stock_level", {"name": "x'; DROP TABLE medicines; --"})
        self.assertEqual(r.primary.rows, [])
        self.assertEqual(len(self.run_tool("search_medicines").primary.rows), 8)

    def test_sales_summary_totals_match_oracle(self):
        import sqlite3
        r = self.run_tool("get_sales_summary", {"period": "all_time"})
        truth = sqlite3.connect(self.db).execute("SELECT COUNT(*), SUM(quantity), ROUND(SUM(total_price),2) FROM sales").fetchone()
        self.assertEqual(tuple(r.primary.rows[0][:3]), truth)

    def test_staff_sees_only_own_sales(self):
        r = self.run_tool("get_sales_summary", {"period": "all_time", "group_by": "seller"}, role="staff")
        self.assertEqual([row[0] for row in r.primary.rows], ["staff"])
        self.assertTrue(any("own sales" in n for n in r.notes))

    def test_role_restricted_tools(self):
        for t in ("forecast_demand", "detect_anomalies", "get_reorder_suggestions"):
            self.assertNotIn(t, tools_for_role("staff"))
            with self.assertRaises(ToolError):
                self.run_tool(t, role="staff")
            self.assertIn(t, tools_for_role("pharmacist"))

    def test_forecast_has_uncertainty_and_backtest(self):
        r = self.run_tool("forecast_demand", {"horizon_days": 7})
        self.assertEqual(len(r.primary.rows), 7)
        for _, f, lo, hi in r.primary.rows:
            self.assertLessEqual(lo, f)
            self.assertLessEqual(f, hi)
        self.assertTrue(any("80%" in x for x in r.facts))
        self.assertTrue(any("Confidence" in x for x in r.facts))
        self.assertTrue(any("not guarantees" in n for n in r.notes))

    def test_forecast_refuses_with_too_little_data(self):
        r = self.run_tool("forecast_demand", {"name": "Warfarin"})
        self.assertIn("Not enough", r.facts[0])

    def test_anomalies_find_spike_and_bad_total(self):
        r = self.run_tool("detect_anomalies")
        kinds = {row[0] for row in r.primary.rows}
        self.assertIn("sales_spike", kinds)
        self.assertIn("sale_total_mismatch", kinds)

    def test_reorder_suggestions(self):
        r = self.run_tool("get_reorder_suggestions")
        self.assertIn("suggested_order_qty", r.primary.columns)
        self.assertTrue({"Warfarin 5mg", "Salbutamol 100mcg"} <= {row[0] for row in r.primary.rows})

    def test_management_summary_has_multiple_tables(self):
        self.assertGreaterEqual(len(self.run_tool("get_management_summary").tables), 5)


class AnalyticsTests(unittest.TestCase):
    def test_flat_series_gives_flat_forecast(self):
        import pandas as pd
        y = pd.Series([10.0] * 40, index=pd.date_range("2026-01-01", periods=40))
        fc = analytics.forecast_series(y, 7)
        self.assertTrue(fc.ok)
        self.assertAlmostEqual(fc.frame["forecast"].mean(), 10, delta=1.0)

    def test_short_series_rejected(self):
        import pandas as pd
        self.assertFalse(analytics.forecast_series(pd.Series([1.0] * 5, index=pd.date_range("2026-01-01", periods=5))).ok)

    def test_spike_detector(self):
        import pandas as pd
        vals = [10.0] * 40
        vals[30] = 90.0
        out = analytics.spike_anomalies(pd.Series(vals, index=pd.date_range("2026-01-01", periods=40)))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["kind"], "spike")

    def test_no_false_alarm_on_noise(self):
        import numpy as np, pandas as pd
        rng = np.random.default_rng(3)
        s = pd.Series(np.maximum(0, rng.normal(10, 1.5, 80)).round(), index=pd.date_range("2026-01-01", periods=80))
        self.assertEqual(analytics.spike_anomalies(s), [])


if __name__ == "__main__":
    unittest.main()
