import unittest
from tests.helpers import build_db
from pharmaiq import config, evaluate


class EvaluationGateTests(unittest.TestCase):
    """Quality gate: the shipped evaluation sets must keep passing on the fixture database."""

    @classmethod
    def setUpClass(cls):
        cls.report = evaluate.run_all(build_db(), config.get_settings())

    def failures(self, suite):
        return [r for r in self.report[suite] if not r["passed"]]

    def test_sql_correctness(self):
        self.assertEqual(self.failures("sql"), [])

    def test_safety_and_guard(self):
        self.assertEqual(self.failures("safety"), [])

    def test_rag_quality(self):
        self.assertEqual(self.failures("rag"), [])
        self.assertGreaterEqual(self.report["summary"]["rag_hit_at_3"], 0.9)


if __name__ == "__main__":
    unittest.main()
