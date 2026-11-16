import json
import unittest
from pathlib import Path
from fieldtrial_analytics.pipeline import run
from fieldtrial_analytics.parser import load_records
from fieldtrial_analytics.engine import calculate

def fixture():
    return json.loads((Path(__file__).parents[1] / "fixtures" / "input.json").read_text())

class TestCore(unittest.TestCase):
    def test_pipeline_and_modules(self):
        payload = fixture()
        rows = load_records(payload)
        self.assertTrue(rows)
        data = calculate(rows, payload)
        self.assertIsInstance(data, dict)
        self.assertTrue(data)
        result = run(payload)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["project"], 'fieldtrial-analytics')

        assert result["treatment_means"]["A"] == 11.0 and result["missing_plots"] == ["P5"]
