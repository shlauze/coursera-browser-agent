import unittest

from v2.policy import ItemAction, decide


class PolicyTests(unittest.TestCase):
    def test_known_types(self):
        self.assertEqual(decide("Reading").action, ItemAction.READING)
        self.assertEqual(decide("Video • 7 min").action, ItemAction.VIDEO)
        self.assertEqual(decide("Lab • 1h").action, ItemAction.UNGRADED_LAB)
        self.assertEqual(decide("Dialogue • 15 min").action, ItemAction.DIALOGUE)
        self.assertEqual(decide("Role Play • 15 min").action, ItemAction.ROLE_PLAY)

    def test_grading_precedes_generic_lab(self):
        self.assertEqual(decide("Programming Assignment • 2h").action, ItemAction.PROGRAMMING_LAB)
        self.assertEqual(decide("Graded Lab").action, ItemAction.PROGRAMMING_LAB)

    def test_all_assignment_types_remain_in_plan(self):
        for kind in ("Quiz", "Graded Assignment", "Practice Assignment", "Assignment", "Exam"):
            self.assertEqual(decide(kind).action, ItemAction.ASSESSMENT)
        self.assertEqual(decide("Peer-graded Assignment").action, ItemAction.PEER)

    def test_title_cannot_make_unknown_item_safe(self):
        self.assertEqual(decide("Unknown Widget", "Read about video labs").action, ItemAction.REVIEW_REQUIRED)
        self.assertEqual(decide("", "Lab - challenge").action, ItemAction.REVIEW_REQUIRED)

    def test_restored_plugin_types(self):
        for kind in ("Ungraded Plugin", "Ungraded Widget", "ungradedWidget"):
            self.assertEqual(decide(kind).action, ItemAction.PLUGIN)


if __name__ == "__main__":
    unittest.main()
