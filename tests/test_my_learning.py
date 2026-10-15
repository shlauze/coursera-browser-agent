"""Offline checks for My Learning course selection and certificate routing."""
import asyncio
import importlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
learning = importlib.import_module("my_learning")


def card(slug, title, action="get_started"):
    return {"id":slug, "url":"https://www.coursera.org/learn/" + slug,
            "title":title, "source":"Course · 0% complete", "action":action,
            "title_url":"https://www.coursera.org/learn/" + slug + "/home/welcome"}


class FakeTab:
    def __init__(self, pages, url="https://www.coursera.org/learn/previous/home"):
        self.pages = pages
        self.url = url
        self.page = 1
        self.commands = []
        self.certificate = None
        self.completion_title = None
        self.stay_on_learning_after_click = False
        self.stay_after_certificate_click = False
        self.transient_empty = False
        self.certificate_page_title = None
        self.certificate_page_completed_by = None
        self.certificate_page_course_id = None
        self.records_redirect = False
        self.targets = {"managed":{"type":"page", "targetId":"managed", "url":self.url}}
        self.spawned_targets_on_click = []
        self.spawned_targets_on_certificate_click = []

    async def call(self, method, params=None):
        if method == "Target.getTargets":
            return {"targetInfos":list(self.targets.values())}
        if method == "Target.closeTarget":
            self.commands.append((method, dict(params)))
            removed = self.targets.pop(params["targetId"], None)
            return {"success":removed is not None}
        self.commands.append((method, dict(params)))
        self.url = params["url"]
        if self.records_redirect and "/account/accomplishments/records/" in self.url:
            self.url = self.url.replace("/records/", "/verify/")
        if "/my-learning" in self.url:
            self.page = 1
        self.targets["managed"]["url"] = self.url
        return {}

    async def evaluate(self, expression):
        if expression.startswith(learning.PAGE_JS):
            request = json.loads(expression[len(learning.PAGE_JS) + 1:-1])
            if request["action"] == "inspect":
                return {"url":self.url, "ready":"complete", "page":self.page,
                        "in_progress_selected":True,"has_next":self.page < len(self.pages),
                        "course_link_count":0 if self.transient_empty else len(self.pages[self.page - 1]),
                        "explicit_empty":not any(self.pages),
                        "cards":[] if self.transient_empty else self.pages[self.page - 1],
                        "course_rows":[{"id":c["id"],"status":c.get("source", "")}
                                       for c in self.pages[self.page - 1]],
                        "signature":"page-" + str(self.page)}
            if request["action"] == "next_page":
                if request["signature"] != "page-" + str(self.page):
                    return {"error":"stale"}
                previous = self.page
                self.page += 1
                self.commands.append(("click_next", previous))
                return {"clicked":True,"previous_page":previous}
            if request["action"] == "click_course":
                if request["signature"] != "page-" + str(self.page):
                    return {"error":"stale"}
                selected = next((c for c in self.pages[self.page - 1] if c["id"] == request["id"]), None)
                if not selected:
                    return {"error":"gone"}
                self.commands.append(("click_course", selected["id"]))
                for target_id, target_url in self.spawned_targets_on_click:
                    self.targets[target_id] = {"type":"page", "targetId":target_id, "url":target_url}
                if not self.stay_on_learning_after_click:
                    self.url = selected["title_url"]
                    self.targets["managed"]["url"] = self.url
                return {"clicked":True,"id":selected["id"]}
        if expression.startswith(learning.CERTIFICATE_JS):
            request = json.loads(expression[len(learning.CERTIFICATE_JS) + 1:-1])
            if request["action"] == "inspect":
                return {"url":self.url,"ready":"complete","completion_title":self.completion_title,
                        "links":[self.certificate] if self.certificate else []}
            self.commands.append(("click_certificate", request["href"]))
            for target_id, target_url in self.spawned_targets_on_certificate_click:
                self.targets[target_id] = {"type":"page", "targetId":target_id, "url":target_url}
            if not self.stay_after_certificate_click:
                self.url = request["href"]
                self.targets["managed"]["url"] = self.url
            return {"clicked":True,"href":self.url}
        if expression.startswith(learning.MY_LEARNING_LINK_JS):
            request = json.loads(expression[len(learning.MY_LEARNING_LINK_JS) + 1:-1])
            if request["action"] == "inspect":
                return {"url":self.url,"links":1 if "/account/accomplishments/verify/" in self.url else 0}
            self.commands.append(("click_my_learning", self.url))
            self.url = "https://www.coursera.org/my-learning"
            return {"clicked":True,"url":self.url}
        if expression.startswith(learning.CERTIFICATE_PAGE_JS):
            request = json.loads(expression[len(learning.CERTIFICATE_PAGE_JS) + 1:-1])
            token = self.url.rstrip("/").split("/")[-1]
            if "/account/accomplishments/verify/" not in self.url or token != request["token"]:
                return {"error":"wrong certificate page","url":self.url}
            headings = [self.certificate_page_title] if self.certificate_page_title else []
            product_links = (["/learn/" + (self.certificate_page_course_id or request["course_id"])]
                             if self.certificate_page_title else [])
            return {"url":self.url,"ready":"complete","token":token,
                    "headings":headings,"product_links":product_links,
                    "completed_by":self.certificate_page_completed_by,
                    "signature":json.dumps([self.url,headings,product_links,self.certificate_page_completed_by])}
        raise AssertionError("Unknown page script")


class LaggedPageTab(FakeTab):
    """Page number updates before React replaces the previous page's cards."""

    def __init__(self, pages, stale_inspections):
        super().__init__(pages)
        self.stale_inspections = stale_inspections
        self.remaining_stale = 0
        self.second_page_inspections = 0

    async def evaluate(self, expression):
        result = await super().evaluate(expression)
        if not expression.startswith(learning.PAGE_JS):
            return result
        request = json.loads(expression[len(learning.PAGE_JS) + 1:-1])
        if request["action"] == "next_page" and result.get("clicked"):
            self.remaining_stale = self.stale_inspections
        elif request["action"] == "inspect" and self.page == 2:
            self.second_page_inspections += 1
            if self.remaining_stale is None or self.remaining_stale > 0:
                if self.remaining_stale is not None:
                    self.remaining_stale -= 1
                old_cards = self.pages[0]
                result.update(cards=old_cards,
                              course_rows=[{"id": c["id"], "status": c.get("source", "")}
                                           for c in old_cards],
                              course_link_count=len(old_cards),
                              signature="page-2-with-page-1-cards")
        return result


class GroupedDeferredTab(FakeTab):
    """A later course in a specialization is visible without a start action."""

    async def evaluate(self, expression):
        result = await super().evaluate(expression)
        if expression.startswith(learning.PAGE_JS):
            request = json.loads(expression[len(learning.PAGE_JS) + 1:-1])
            if request["action"] == "inspect" and self.page == 1:
                result["course_rows"].append({"id": "future-course",
                                              "status": "Course 2 of 3 · Not started"})
                result["course_link_count"] += 1
                result["explicit_empty"] = False
        return result


class UrlTests(unittest.TestCase):
    def test_certificate_url_is_strict(self):
        valid = "https://www.coursera.org/account/accomplishments/verify/EXAMPLECERT12345"
        self.assertTrue(learning.valid_certificate_url(valid))
        self.assertTrue(learning.valid_certificate_url(
            "https://www.coursera.org/account/accomplishments/records/EXAMPLECERT12345"))
        for bad in ["http://www.coursera.org/account/accomplishments/verify/EXAMPLECERT12345",
                    "https://coursera.org.evil.test/account/accomplishments/verify/EXAMPLECERT12345",
                    "https://www.coursera.org/account/accomplishments/verify/EXAMPLECERT12345?next=evil",
                    "https://www.coursera.org/learn/philosophy",
                    "https://www.coursera.org/account/accomplishments/verify/x"]:
            with self.subTest(bad=bad):
                self.assertFalse(learning.valid_certificate_url(bad))

    def test_title_link_becomes_canonical_course_only(self):
        self.assertEqual(learning.course_from_lesson_url(
            "https://coursera.org/learn/advanced-malware-and-network-anomaly-detection/home/welcome"),
            {"id":"advanced-malware-and-network-anomaly-detection",
             "url":"https://www.coursera.org/learn/advanced-malware-and-network-anomaly-detection"})
        self.assertIsNone(learning.course_from_lesson_url("https://example.org/learn/test/home/welcome"))
        self.assertIsNone(learning.course_from_lesson_url("https://www.coursera.org/specializations/ai"))


class NavigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_reads_only_current_course_certificate_overlay(self):
        tab = FakeTab([[]], "https://www.coursera.org/learn/introduction-to-ai-for-cybersecurity/assignment-submission/quiz/attempt")
        tab.certificate = "https://www.coursera.org/account/accomplishments/records/EXAMPLECERT12345"
        tab.completion_title = "Introduction to AI for Cybersecurity"
        self.assertEqual(await learning.observed_certificate_url(tab, "introduction-to-ai-for-cybersecurity"),
                         tab.certificate)
        self.assertIsNone(await learning.observed_certificate_url(tab, "another-course"))
        tab.completion_title = "A different certificate"
        self.assertIsNone(await learning.observed_certificate_url(tab, "introduction-to-ai-for-cybersecurity"))
        tab.completion_title = "A Course with Another Slug"
        tab.url = "https://www.coursera.org/learn/different-slug/home"
        self.assertEqual(await learning.observed_certificate_url(tab, "different-slug", "A Course with Another Slug"),
                         tab.certificate)

    async def test_click_certificate_then_my_learning(self):
        url = "https://www.coursera.org/account/accomplishments/verify/EXAMPLECERT12345"
        tab = FakeTab([[card("next-course", "Next course")]])
        tab.certificate = url
        with patch.object(learning, "POLL_SECONDS", 0):
            self.assertEqual(await learning.open_certificate_page(tab), url)
            await learning.open_my_learning(tab)
        self.assertEqual(tab.commands[0], ("click_certificate", url))
        self.assertEqual(tab.commands[1], ("click_my_learning", url))
        self.assertEqual(tab.commands[2], ("Page.navigate", {"url":learning.MY_LEARNING_URL}))

    async def test_saved_certificate_navigates_only_to_validated_link(self):
        url = "https://www.coursera.org/account/accomplishments/verify/EXAMPLECERT12345"
        tab = FakeTab([[]])
        with patch.object(learning, "POLL_SECONDS", 0):
            self.assertEqual(await learning.open_certificate_page(tab, url), url)
        self.assertEqual(tab.commands, [("Page.navigate", {"url":url})])
        with self.assertRaises(ValueError):
            await learning.open_certificate_page(tab, "https://example.org/verify/fake")

    async def test_certificate_click_falls_back_to_observed_url_in_same_tab(self):
        url = "https://www.coursera.org/account/accomplishments/records/EXAMPLECERT12345"
        tab = FakeTab([[]])
        tab.certificate = url
        tab.stay_after_certificate_click = True
        with patch.object(learning, "POLL_SECONDS", 0):
            self.assertEqual(await learning.open_certificate_page(tab), url)
        self.assertEqual(tab.commands, [
            ("click_certificate", url), ("Page.navigate", {"url":url})])

    async def test_certificate_click_closes_only_its_new_matching_tab(self):
        url = "https://www.coursera.org/account/accomplishments/records/EXAMPLECERT12345"
        tab = FakeTab([[]])
        tab.certificate = url
        tab.stay_after_certificate_click = True
        tab.targets["existing-same"] = {"type":"page", "targetId":"existing-same", "url":url}
        tab.targets["existing-other"] = {"type":"page", "targetId":"existing-other",
                                         "url":"https://www.coursera.org/account/accomplishments/verify/OTHER123"}
        tab.spawned_targets_on_certificate_click = [
            ("new-same", url.replace("/records/", "/verify/")),
            ("new-other", "https://www.coursera.org/account/accomplishments/verify/OTHER123")]
        with patch.object(learning, "POLL_SECONDS", 0):
            self.assertEqual(await learning.open_certificate_page(tab), url)
        self.assertEqual([x for x in tab.commands if x[0] == "Target.closeTarget"],
                         [("Target.closeTarget", {"targetId":"new-same"})])
        self.assertEqual(set(tab.targets), {"managed", "existing-same", "existing-other", "new-other"})
        self.assertEqual(tab.url, url)

    async def test_scans_pages_and_clicks_first_nonexcluded_course(self):
        pages = [[card("done", "Completed"), card("removed", "Removed")],
                 [card("next-course", "Next course"), card("later", "Later")]]
        tab = FakeTab(pages)
        with patch.object(learning, "POLL_SECONDS", 0):
            selected = await learning.discover_next_course(tab, {"done", "removed"})
        self.assertEqual(selected["id"], "next-course")
        self.assertEqual([name for name, _ in tab.commands],
                         ["Page.navigate", "click_next", "Page.navigate", "click_next", "click_course"])

    async def test_full_scan_selects_started_course_without_opening_any_course(self):
        started = card("unfinished", "Unfinished", action="resume")
        started["source"] = "Course · 43% complete"
        tab = FakeTab([[card("new-course", "New course")], [started]])
        with patch.object(learning, "POLL_SECONDS", 0):
            selected = await learning.scan_in_progress(tab, set())
        self.assertEqual(selected["id"], "unfinished")
        self.assertEqual(selected["selected_page"], 2)
        self.assertEqual(selected["kind"], "started")
        self.assertEqual(tab.url, learning.MY_LEARNING_URL)
        self.assertEqual([name for name, _ in tab.commands], ["Page.navigate", "click_next"])

        with patch.object(learning, "POLL_SECONDS", 0):
            opened = await learning.open_scanned_course(tab, selected)
        self.assertEqual(opened["id"], "unfinished")
        self.assertEqual([name for name, _ in tab.commands],
                         ["Page.navigate", "click_next", "Page.navigate", "click_next", "click_course"])

    async def test_read_only_scan_rejects_unknown_later_page_before_course_open(self):
        unknown = card("unknown-course", "Unknown", action="unsupported")
        unknown["source"] = "Course · 40% complete"
        tab = FakeTab([[card("new-course", "New course")], [unknown]])
        with patch.object(learning, "POLL_SECONDS", 0):
            with self.assertRaisesRegex(RuntimeError, "unfinished courses without a supported action"):
                await learning.scan_in_progress(tab, set())
        self.assertEqual([name for name, _ in tab.commands], ["Page.navigate", "click_next"])

    async def test_open_scanned_course_rejects_changed_card_before_click(self):
        started = card("unfinished", "Unfinished", action="resume")
        started["source"] = "Course · 43% complete"
        tab = FakeTab([[started]])
        with patch.object(learning, "POLL_SECONDS", 0):
            selected = await learning.scan_in_progress(tab, set())
        started["source"] = "Course · 44% complete"
        with patch.object(learning, "POLL_SECONDS", 0):
            with self.assertRaisesRegex(RuntimeError, "changed before the selected course"):
                await learning.open_scanned_course(tab, selected)
        self.assertFalse(any(name == "click_course" for name, _ in tab.commands))

    async def test_later_page_started_course_precedes_new_course(self):
        new_course = card("new-course", "New course")
        started = card("unfinished", "Unfinished", action="resume")
        started["source"] = "Course · 43% complete"
        tab = FakeTab([[new_course], [started]])
        with patch.object(learning, "POLL_SECONDS", 0):
            selected = await learning.discover_next_course(tab, set())
        self.assertEqual(selected["id"], "unfinished")
        self.assertEqual([value for name, value in tab.commands if name == "click_course"], ["unfinished"])
        self.assertEqual([name for name, _ in tab.commands],
                         ["Page.navigate", "click_next", "Page.navigate", "click_next", "click_course"])

    async def test_first_page_started_course_precedes_later_new_course(self):
        started = card("unfinished", "Unfinished", action="continue_learning")
        started["source"] = "Course 2 of 3 · Started"
        tab = FakeTab([[started], [card("new-course", "New course")]])
        with patch.object(learning, "POLL_SECONDS", 0):
            selected = await learning.discover_next_course(tab, set())
        self.assertEqual(selected["id"], "unfinished")
        self.assertEqual([name for name, _ in tab.commands],
                         ["Page.navigate", "click_next", "Page.navigate", "click_course"])

    async def test_resume_zero_percent_precedes_new_course(self):
        resumed = card("unfinished", "Unfinished", action="resume")
        tab = FakeTab([[card("new-course", "New course")], [resumed]])
        with patch.object(learning, "POLL_SECONDS", 0):
            selected = await learning.discover_next_course(tab, set())
        self.assertEqual(selected["id"], "unfinished")

    async def test_resume_action_precedes_started_row_with_get_started(self):
        started_with_start_button = card("older", "Older", action="get_started")
        started_with_start_button["source"] = "Course · 40% complete"
        resumed = card("resume-course", "Resume course", action="resume")
        tab = FakeTab([[started_with_start_button], [resumed]])
        with patch.object(learning, "POLL_SECONDS", 0):
            selected = await learning.discover_next_course(tab, set())
        self.assertEqual(selected["id"], "resume-course")
        self.assertEqual([value for name, value in tab.commands if name == "click_course"],
                         ["resume-course"])

    async def test_later_duplicate_resume_replaces_earlier_get_started(self):
        earlier = card("same-course", "Same course", action="get_started")
        earlier["source"] = "Course · 40% complete"
        later = card("same-course", "Same course", action="resume")
        later["source"] = "Course · 40% complete"
        tab = FakeTab([[earlier, card("other-course", "Other course")], [later]])
        with patch.object(learning, "POLL_SECONDS", 0):
            selected = await learning.scan_in_progress(tab, set())
            opened = await learning.open_scanned_course(tab, selected)
        self.assertEqual(selected["action"], "resume")
        self.assertEqual(selected["selected_page"], 2)
        self.assertEqual(opened["action"], "resume")

    async def test_later_unhandled_course_blocks_earlier_new_course(self):
        unsupported = card("unfinished", "Unfinished", action="unsupported")
        unsupported["source"] = "Course · 40% complete"
        tab = FakeTab([[card("new-course", "New course")], [unsupported]])
        with patch.object(learning, "POLL_SECONDS", 0):
            with self.assertRaisesRegex(RuntimeError, "unfinished courses without a supported action"):
                await learning.discover_next_course(tab, set())
        self.assertFalse(any(name == "click_course" for name, _ in tab.commands))

    async def test_grouped_future_course_does_not_block_actionable_started_course(self):
        started = card("unfinished", "Unfinished", action="resume")
        started["source"] = "Course · 43% complete"
        tab = GroupedDeferredTab([[], [started]])
        with patch.object(learning, "POLL_SECONDS", 0):
            selected = await learning.scan_in_progress(tab, set())
        self.assertEqual(selected["id"], "unfinished")
        self.assertEqual(selected["kind"], "started")
        self.assertEqual(selected["selected_page"], 2)
        self.assertFalse(any(name == "click_course" for name, _ in tab.commands))

    async def test_only_grouped_future_course_stops_instead_of_finishing_queue(self):
        tab = GroupedDeferredTab([[]])
        with patch.object(learning, "POLL_SECONDS", 0):
            with self.assertRaises(RuntimeError) as raised:
                await learning.scan_in_progress(tab, set())
        self.assertRegex(str(raised.exception), "deferred|unavailable|unfinished|action")
        self.assertFalse(any(name == "click_course" for name, _ in tab.commands))

    async def test_unknown_status_blocks_new_course(self):
        unknown = card("unknown", "Unknown")
        unknown["source"] = "Course · Waiting for feedback"
        tab = FakeTab([[card("new-course", "New course")], [unknown]])
        with patch.object(learning, "POLL_SECONDS", 0):
            with self.assertRaisesRegex(RuntimeError, "known status"):
                await learning.discover_next_course(tab, set())
        self.assertFalse(any(name == "click_course" for name, _ in tab.commands))

    async def test_course_link_without_recognized_status_blocks_new_course(self):
        tab = FakeTab([[card("new-course", "New course")]])
        original = tab.evaluate
        async def with_unknown_row(expression):
            value = await original(expression)
            if expression.startswith(learning.PAGE_JS) and isinstance(value, dict) and "course_rows" in value:
                value["course_rows"].append({"id":"unrecognized", "status":""})
            return value
        tab.evaluate = with_unknown_row
        with patch.object(learning, "POLL_SECONDS", 0):
            with self.assertRaisesRegex(RuntimeError, "known status"):
                await learning.discover_next_course(tab, set())
        self.assertFalse(any(name == "click_course" for name, _ in tab.commands))

    async def test_conflicting_row_and_card_progress_blocks_selection(self):
        started = card("unfinished", "Unfinished", action="resume")
        started["source"] = "Course · 43% complete"
        tab = FakeTab([[started]])
        original = tab.evaluate
        async def with_conflicting_row(expression):
            value = await original(expression)
            if expression.startswith(learning.PAGE_JS) and isinstance(value, dict) and "course_rows" in value:
                value["course_rows"][0]["status"] = "Course · Complete"
            return value
        tab.evaluate = with_conflicting_row
        with patch.object(learning, "POLL_SECONDS", 0):
            with self.assertRaisesRegex(RuntimeError, "conflicting progress"):
                await learning.discover_next_course(tab, set())
        self.assertFalse(any(name == "click_course" for name, _ in tab.commands))

    async def test_completed_card_is_not_reopened_when_start_action_remains(self):
        completed = card("complete", "Complete")
        completed["source"] = "Course 1 of 3 · Complete"
        tab = FakeTab([[completed], [card("new-course", "New course")]])
        with patch.object(learning, "POLL_SECONDS", 0):
            selected = await learning.discover_next_course(tab, set())
        self.assertEqual(selected["id"], "new-course")
        self.assertEqual([value for name, value in tab.commands if name == "click_course"], ["new-course"])

    async def test_skips_completed_cards_and_exhausts_all_pages(self):
        tab = FakeTab([[card("done", "Done")], [card("removed", "Removed")]])
        with patch.object(learning, "POLL_SECONDS", 0):
            self.assertIsNone(await learning.discover_next_course(tab, {"done", "removed"}))
        self.assertEqual([name for name, _ in tab.commands], ["Page.navigate", "click_next"])

    async def test_title_link_fallback_after_button_opens_other_target(self):
        tab = FakeTab([[card("new-course", "New course")]])
        tab.stay_on_learning_after_click = True
        with patch.object(learning, "POLL_SECONDS", 0):
            selected = await learning.discover_next_course(tab, set())
        self.assertEqual(selected["id"], "new-course")
        self.assertEqual(tab.commands[-1],
                         ("Page.navigate", {"url":"https://www.coursera.org/learn/new-course/home/welcome"}))

    async def test_closes_only_new_tab_for_selected_course_after_managed_navigation(self):
        selected = card("new-course", "New course")
        tab = FakeTab([[selected]])
        tab.stay_on_learning_after_click = True
        # These pre-existing tabs, even one on the same course, must remain.
        tab.targets["existing-selected"] = {"type":"page", "targetId":"existing-selected",
                                            "url":selected["title_url"]}
        tab.targets["existing-other"] = {"type":"page", "targetId":"existing-other",
                                         "url":"https://www.coursera.org/learn/other/home/welcome"}
        tab.spawned_targets_on_click = [
            ("new-selected", selected["title_url"]),
            ("new-other", "https://www.coursera.org/learn/other/home/welcome")]
        with patch.object(learning, "POLL_SECONDS", 0):
            result = await learning.discover_next_course(tab, set())
        self.assertEqual(result["id"], "new-course")
        self.assertEqual([x for x in tab.commands if x[0] == "Target.closeTarget"],
                         [("Target.closeTarget", {"targetId":"new-selected"})])
        self.assertEqual(set(tab.targets), {"managed", "existing-selected", "existing-other", "new-other"})
        self.assertEqual(tab.url, selected["title_url"])

    async def test_page_repetition_is_not_mistaken_for_completion(self):
        tab = FakeTab([[card("done", "Done")], [card("later", "Later")]])
        async def stuck(expression):
            if expression.startswith(learning.PAGE_JS):
                request = json.loads(expression[len(learning.PAGE_JS) + 1:-1])
                if request["action"] == "next_page":
                    return {"clicked":True,"previous_page":1}
            return await FakeTab.evaluate(tab, expression)
        tab.evaluate = stuck
        with patch.object(learning, "POLL_SECONDS", 0), patch.object(learning, "PAGE_PROBES", 3):
            with self.assertRaisesRegex(RuntimeError, "stable course list"):
                await learning.discover_next_course(tab, {"done"})

    async def test_waits_for_new_cards_after_page_indicator_advances(self):
        started = card("unfinished", "Unfinished", action="resume")
        started["source"] = "Course · 43% complete"
        tab = LaggedPageTab([[card("done", "Done")], [started]], stale_inspections=4)
        with patch.object(learning, "POLL_SECONDS", 0), patch.object(learning, "PAGE_PROBES", 8):
            selected = await learning.scan_in_progress(tab, set())
            self.assertEqual(selected["id"], "unfinished")
            self.assertEqual(selected["selected_page"], 2)
            opened = await learning.open_scanned_course(tab, selected)
        self.assertEqual(opened["id"], "unfinished")
        self.assertGreaterEqual(tab.second_page_inspections, 12)
        self.assertEqual([value for name, value in tab.commands if name == "click_course"],
                         ["unfinished"])

    async def test_stale_cards_forever_stop_at_bounded_page_wait(self):
        started = card("unfinished", "Unfinished", action="resume")
        started["source"] = "Course · 43% complete"
        tab = LaggedPageTab([[card("done", "Done")], [started]], stale_inspections=None)
        with patch.object(learning, "POLL_SECONDS", 0), patch.object(learning, "PAGE_PROBES", 6):
            with self.assertRaisesRegex(RuntimeError, "stable course list"):
                await learning.scan_in_progress(tab, set())
        self.assertEqual(tab.second_page_inspections, 6)
        self.assertFalse(any(name == "click_course" for name, _ in tab.commands))

    async def test_transient_empty_page_does_not_end_scan(self):
        tab = FakeTab([[card("later", "Later")]])
        tab.transient_empty = True
        with patch.object(learning, "POLL_SECONDS", 0), patch.object(learning, "PAGE_PROBES", 3):
            with self.assertRaisesRegex(RuntimeError, "stable course list"):
                await learning.discover_next_course(tab, set())

    async def test_unknown_action_on_unfinished_course_is_attention(self):
        tab = FakeTab([[card("unfinished", "Unfinished", action="unsupported")]])
        with patch.object(learning, "POLL_SECONDS", 0):
            with self.assertRaisesRegex(RuntimeError, "unfinished courses without a supported action"):
                await learning.discover_next_course(tab, set())

    async def test_completed_child_without_get_started_does_not_block_exhaustion(self):
        completed = card("already-done", "Already done", action="unsupported")
        completed["source"] = "Course 1 of 3 · Complete"
        tab = FakeTab([[completed]])
        with patch.object(learning, "POLL_SECONDS", 0):
            self.assertIsNone(await learning.discover_next_course(tab, set()))

    async def test_saved_records_certificate_proves_matching_course(self):
        saved = "https://www.coursera.org/account/accomplishments/records/EXAMPLECERT12345"
        tab = FakeTab([[]])
        tab.records_redirect = True
        tab.certificate_page_title = "Introduction to AI for Cybersecurity"
        tab.certificate_page_completed_by = "Completed by Example Learner"
        with patch.object(learning, "POLL_SECONDS", 0):
            proof = await learning.verify_saved_certificate(
                tab, saved, "introduction-to-ai-for-cybersecurity")
        self.assertTrue(proof["verified"])
        self.assertEqual(proof["method"], "certificate")
        self.assertEqual(proof["certificate_token"], "EXAMPLECERT12345")
        self.assertEqual(proof["url"], saved.replace("/records/", "/verify/"))
        self.assertEqual(tab.commands, [("Page.navigate", {"url":saved})])

    async def test_saved_certificate_rejects_wrong_title_or_missing_completed_by(self):
        saved = "https://www.coursera.org/account/accomplishments/verify/EXAMPLECERT12345"
        tab = FakeTab([[]])
        tab.certificate_page_title = "A Different Course"
        tab.certificate_page_completed_by = "Completed by Example Learner"
        with patch.object(learning, "POLL_SECONDS", 0), patch.object(learning, "PAGE_PROBES", 3):
            with self.assertRaisesRegex(RuntimeError, "did not show verified completion"):
                await learning.verify_saved_certificate(tab, saved, "introduction-to-ai-for-cybersecurity")
        tab.certificate_page_title = "Introduction to AI for Cybersecurity"
        tab.certificate_page_completed_by = None
        with patch.object(learning, "POLL_SECONDS", 0), patch.object(learning, "PAGE_PROBES", 3):
            with self.assertRaisesRegex(RuntimeError, "did not show verified completion"):
                await learning.verify_saved_certificate(tab, saved, "introduction-to-ai-for-cybersecurity")

    async def test_saved_certificate_rejects_invalid_url_before_navigation(self):
        tab = FakeTab([[]])
        with self.assertRaises(ValueError):
            await learning.verify_saved_certificate(tab,
                "https://example.org/account/accomplishments/verify/EXAMPLECERT12345", "course")
        self.assertEqual(tab.commands, [])

    async def test_saved_certificate_rejects_course_link_to_another_course(self):
        saved = "https://www.coursera.org/account/accomplishments/verify/EXAMPLECERT12345"
        tab = FakeTab([[]])
        tab.certificate_page_title = "Introduction to AI for Cybersecurity"
        tab.certificate_page_completed_by = "Completed by Example Learner"
        tab.certificate_page_course_id = "another-course"
        with patch.object(learning, "POLL_SECONDS", 0), patch.object(learning, "PAGE_PROBES", 3):
            with self.assertRaisesRegex(RuntimeError, "did not show verified completion"):
                await learning.verify_saved_certificate(tab, saved, "introduction-to-ai-for-cybersecurity")

    async def test_saved_certificate_can_match_display_title_different_from_slug(self):
        saved = "https://www.coursera.org/account/accomplishments/verify/EXAMPLECERT12345"
        tab = FakeTab([[]])
        tab.certificate_page_title = "Explainable Machine Learning (XAI)"
        tab.certificate_page_completed_by = "Completed by Learner"
        with patch.object(learning, "POLL_SECONDS", 0):
            proof = await learning.verify_saved_certificate(
                tab, saved, "explainable-machine-learning-xai-2", "Explainable Machine Learning (XAI)")
        self.assertTrue(proof["verified"])


if __name__ == "__main__":
    unittest.main()
