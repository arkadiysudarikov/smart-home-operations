import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import jev_synthetic_eval as jev


def response(choice):
    return {"model": "fixture-not-a-live-model", "answers": {"announcement_action": {
        "type": "choice", "choice": choice, "confidence": 1.0,
        "probabilities": {key: float(key == choice) for key in jev.OPTIONS},
    }}}


class JevSyntheticTests(unittest.TestCase):
    def test_export_has_no_expected_answers(self):
        exported = jev.requests()
        self.assertEqual(len(exported), 10)
        for row in exported:
            self.assertEqual(set(row["request"]), {"model", "state", "questions"})
            self.assertTrue(row["request"]["state"].startswith("Synthetic"))
            self.assertNotIn("expected", row["request"])

    def test_empty_is_not_a_pass(self):
        report = jev.evaluate([])
        self.assertIsNone(report["accuracy_on_valid"])
        self.assertEqual(len(report["missing"]), 10)

    def test_fixture_scoring_does_not_claim_live_model(self):
        rows = [{"case_id": key, "response": response(expected)} for key, _, expected in jev.CASES]
        report = jev.evaluate(rows)
        self.assertEqual(report["matches"], 10)
        self.assertEqual(report["missing"], [])
        self.assertEqual(report["unsafe_announces"], 0)
        self.assertEqual(report["rows"][0]["model"], "fixture-not-a-live-model")

    def test_unsafe_announcement_is_counted(self):
        report = jev.evaluate([{"case_id": "night", "response": response("announce_now")}])
        self.assertEqual(report["unsafe_announces"], 1)
        self.assertEqual(report["matches"], 0)

    def test_invalid_shapes_fail_closed(self):
        for value in (None, [], {}, {"model": "jev", "answers": []}):
            with self.assertRaises(ValueError):
                jev.validate_response(value)

    def test_bad_numbers_and_unknown_choices_are_rejected(self):
        base = response("defer")
        for value in (float("nan"), float("inf"), -0.1, 1.1, True, "1"):
            modified = copy.deepcopy(base)
            modified["answers"]["announcement_action"]["confidence"] = value
            with self.assertRaises(ValueError):
                jev.validate_response(modified)
        modified = copy.deepcopy(base)
        modified["answers"]["announcement_action"]["choice"] = "unlock_door"
        with self.assertRaises(ValueError):
            jev.validate_response(modified)

    def test_probability_validation(self):
        for probabilities in ({}, {key: 0.5 for key in jev.OPTIONS},
                              {key: float(key == "review") for key in jev.OPTIONS}):
            modified = response("defer")
            modified["answers"]["announcement_action"]["probabilities"] = probabilities
            with self.assertRaises(ValueError):
                jev.validate_response(modified)

    def test_invalid_responses_stay_visible(self):
        report = jev.evaluate([{"case_id": "night", "response": None}])
        self.assertEqual(report["invalid"], 1)
        self.assertEqual(report["valid"], 0)
        self.assertIsNone(report["accuracy_on_valid"])

    def test_duplicate_and_unknown_records_rejected(self):
        record = {"case_id": "night", "response": response("defer")}
        for records in ([record, record], [{"case_id": "not-a-case"}], [None]):
            with self.assertRaises(ValueError):
                jev.evaluate(records)
