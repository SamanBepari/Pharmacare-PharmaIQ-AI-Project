import unittest
from tests.helpers import build_db
from pharmaiq.sql_guard import ReadOnlySession, SQLValidationError, validate_sql


class SQLGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = build_db(with_kb=False)

    def run_sql(self, sql, role="admin", user="admin", params=None, **kw):
        with ReadOnlySession(self.db, role, user, **kw) as s:
            return s.execute(sql, params)

    def assert_blocked(self, sql, code=None, role="admin"):
        with self.assertRaises(SQLValidationError) as cm:
            self.run_sql(sql, role)
        if code:
            self.assertEqual(cm.exception.code, code)

    def test_valid_select_runs(self):
        r = self.run_sql("SELECT medicine_name FROM medicines WHERE product_qty < :n", params={"n": 10})
        self.assertEqual(len(r.rows), 2)
        self.assertEqual(r.tables_used, ["medicines"])

    def test_trailing_semicolon_ok(self):
        self.assertEqual(len(self.run_sql("SELECT 1;").rows), 1)

    def test_dml_ddl_blocked(self):
        for sql in ("DELETE FROM medicines", "UPDATE medicines SET price=0", "DROP TABLE medicines",
                    "INSERT INTO medicines (medicine_name) VALUES ('x')", "CREATE TABLE x(a)", "PRAGMA table_info(medicines)",
                    "ATTACH DATABASE 'x.db' AS x", "VACUUM"):
            self.assert_blocked(sql, "not_select" if not sql.startswith(("SELECT",)) else None)

    def test_multi_statement_blocked(self):
        self.assert_blocked("SELECT 1; DROP TABLE medicines", "multi_statement")

    def test_comments_blocked(self):
        self.assert_blocked("SELECT 1 -- x", "comment")
        self.assert_blocked("SELECT /* x */ 1", "comment")

    def test_internal_tables_and_schema_prefix(self):
        self.assert_blocked("SELECT name FROM sqlite_master", "internal_table")
        self.assert_blocked("SELECT * FROM main.medicines", "schema_prefix")
        self.assert_blocked("SELECT * FROM temp.sales", "schema_prefix")

    def test_unknown_function_blocked(self):
        self.assert_blocked("SELECT randomblob(8)", "function")
        self.assert_blocked("SELECT load_extension('x')")

    def test_users_table_never_readable(self):
        for role in ("admin", "pharmacist", "staff"):
            self.assert_blocked("SELECT password FROM users", "not_authorized", role)
            self.assert_blocked("SELECT username FROM medicines UNION SELECT password FROM users", "not_authorized", role)

    def test_activity_logs_admin_only_and_no_ip(self):
        self.assertEqual(self.run_sql("SELECT username, action FROM activity_logs").columns, ["username", "action"])
        self.assert_blocked("SELECT * FROM activity_logs", "not_authorized", "admin")      # ip_address column denied
        self.assert_blocked("SELECT username FROM activity_logs", "not_authorized", "pharmacist")
        self.assert_blocked("SELECT username FROM activity_logs", "not_authorized", "staff")

    def test_staff_row_level_filter(self):
        staff = self.run_sql("SELECT DISTINCT sold_by FROM sales", "staff", "staff").rows
        self.assertEqual(staff, [["staff"]])
        admin = {r[0] for r in self.run_sql("SELECT DISTINCT sold_by FROM sales").rows}
        self.assertTrue({"admin", "pharmacist", "staff"} <= admin)

    def test_staff_cannot_bypass_row_filter_with_schema_prefix(self):
        self.assert_blocked("SELECT * FROM main.sales", "schema_prefix", "staff")

    def test_row_limit_and_truncation(self):
        r = self.run_sql("SELECT id FROM sales", max_rows=10)
        self.assertEqual(len(r.rows), 10)
        self.assertTrue(r.truncated)

    def test_timeout(self):
        with self.assertRaises(SQLValidationError) as cm:
            self.run_sql("SELECT COUNT(*) FROM sales a, sales b, sales c, sales d", timeout_s=0.2)
        self.assertEqual(cm.exception.code, "timeout")

    def test_database_unchanged_after_attacks(self):
        before = self.run_sql("SELECT COUNT(*) FROM medicines").rows[0][0]
        for sql in ("DELETE FROM medicines", "SELECT 1; DELETE FROM medicines"):
            try:
                self.run_sql(sql)
            except SQLValidationError:
                pass
        self.assertEqual(self.run_sql("SELECT COUNT(*) FROM medicines").rows[0][0], before)

    def test_raw_write_blocked_even_bypassing_validation(self):
        with ReadOnlySession(self.db, "admin", "admin") as s:
            with self.assertRaises(Exception):
                s.conn.execute("DELETE FROM medicines")

    def test_validate_returns_clean_sql(self):
        self.assertEqual(validate_sql("  SELECT 1 ;  "), "SELECT 1")

    def test_like_and_dates_allowed(self):
        r = self.run_sql("SELECT medicine_name FROM medicines WHERE LOWER(medicine_name) LIKE LOWER(:p) AND julianday(exp_date) > julianday('2020-01-01')",
                         params={"p": "%para%"})
        self.assertEqual(len(r.rows), 1)


if __name__ == "__main__":
    unittest.main()
