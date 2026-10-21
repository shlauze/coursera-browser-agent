import unittest
from unittest.mock import Mock, patch
from v2.discovery import CourseCard, discover_in_progress


class QuickStartTests(unittest.TestCase):
    def test_first_page_prefers_resume_without_pagination(self):
        first = CourseCard('New course', 'Get started', 0, 1, 0)
        resumed = CourseCard('Started course', 'Resume', 10, 1, 1)
        with patch('v2.discovery._wait_for_queue'), patch('v2.discovery._load_all_visible'), \
             patch('v2.discovery._read_page_cards', return_value=[first, resumed]), \
             patch('v2.discovery._next_page') as next_page:
            result = discover_in_progress(Mock(), first_page_only=True)
        self.assertEqual(result, [resumed, first])
        next_page.assert_not_called()

    def test_no_resume_keeps_first_get_started(self):
        cards = [CourseCard('First course', 'Get started', 0, 1, 0), CourseCard('Second course', 'Get started', 0, 1, 1)]
        with patch('v2.discovery._wait_for_queue'), patch('v2.discovery._load_all_visible'), \
             patch('v2.discovery._read_page_cards', return_value=cards), \
             patch('v2.discovery._next_page') as next_page:
            result = discover_in_progress(Mock(), first_page_only=True)
        self.assertEqual(result, cards)
        next_page.assert_not_called()
