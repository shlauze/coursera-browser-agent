"""Offline checks for certificate recovery from My Learning → Completed."""
import importlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
learning = importlib.import_module("my_learning")

COURSE_ID = "advanced-malware-and-network-anomaly-detection"
TITLE = "Advanced Malware and Network Anomaly Detection"
RECORDS = "https://www.coursera.org/account/accomplishments/records/SAMPLECERT67890"
VERIFY = RECORDS.replace("/records/", "/verify/")


def candidate(href=RECORDS, *, depth=4, ids=None, titles=None):
    return {"href":href, "depth":depth, "course_ids":[COURSE_ID] if ids is None else ids,
            "title_matches":[TITLE] if titles is None else titles}


class TemporaryCompletedTab:
    def __init__(self, pages, *, certificate_title=TITLE, certificate_course_id=COURSE_ID):
        self.pages = pages
        self.page = 1
        self.url = learning.COMPLETED_LEARNING_URL
        self.certificate_title = certificate_title
        self.certificate_course_id = certificate_course_id
        self.commands = []
        self.closed = False

    async def call(self, method, params=None):
        self.commands.append((method, dict(params or {})))
        if method == "Page.navigate":
            self.url = params["url"].replace("/records/", "/verify/")
        return {}

    async def evaluate(self, expression):
        if expression.startswith(learning.COMPLETED_PAGE_JS):
            request = json.loads(expression[len(learning.COMPLETED_PAGE_JS) + 1:-1])
            if request["action"] == "next_page":
                if request["signature"] != "completed-" + str(self.page):
                    return {"error":"stale"}
                old = self.page
                self.page += 1
                self.commands.append(("next_page", old))
                return {"clicked":True, "previous_page":old}
            found = self.pages[self.page - 1]
            return {"url":self.url, "ready":"complete", "page":self.page,
                    "completed_selected":True, "explicit_empty":not any(self.pages),
                    "has_next":self.page < len(self.pages),
                    "certificate_count":len(found), "course_link_count":1 if found else 0,
                    "candidates":found, "signature":"completed-" + str(self.page)}
        if expression.startswith(learning.CERTIFICATE_PAGE_JS):
            request = json.loads(expression[len(learning.CERTIFICATE_PAGE_JS) + 1:-1])
            token = self.url.rstrip("/").split("/")[-1]
            if "/verify/" not in self.url or token != request["token"]:
                return {"url":self.url, "error":"wrong certificate page"}
            links = ["/learn/" + self.certificate_course_id]
            return {"url":self.url, "ready":"complete", "token":token,
                    "headings":[self.certificate_title], "product_links":links,
                    "completed_by":"Completed by Example Learner",
                    "signature":json.dumps([self.url, self.certificate_title, links])}
        raise AssertionError("Unexpected page script")

    async def close(self):
        self.closed = True


class OwnerTab:
    def __init__(self):
        self.commands = []

    async def call(self, method, params=None):
        self.commands.append((method, dict(params or {})))
        if method == "Target.closeTarget":
            return {"success":True}
        raise AssertionError("The managed course tab must not navigate")


class CompletedCertificateTests(unittest.IsolatedAsyncioTestCase):
    async def recover(self, temporary):
        owner = OwnerTab()
        with patch.object(learning, "_open_temporary_completed_tab",
                          AsyncMock(return_value=(temporary, "temporary-target"))), \
             patch.object(learning, "POLL_SECONDS", 0):
            url = await learning.find_completed_certificate(owner, COURSE_ID, TITLE)
        self.assertTrue(temporary.closed)
        self.assertEqual(owner.commands, [("Target.closeTarget", {"targetId":"temporary-target"})])
        return url

    async def test_duplicate_card_links_are_deduplicated_then_verified(self):
        temporary = TemporaryCompletedTab([[candidate(), candidate()]])
        self.assertEqual(await self.recover(temporary), VERIFY)
        self.assertEqual(temporary.commands, [("Page.navigate", {"url":RECORDS})])

    async def test_paginates_until_the_matching_card(self):
        temporary = TemporaryCompletedTab([[candidate(ids=["other-course"], titles=[])],
                                           [candidate()]])
        self.assertEqual(await self.recover(temporary), VERIFY)
        self.assertIn(("next_page", 1), temporary.commands)

    async def test_neighboring_course_certificate_is_not_accepted(self):
        temporary = TemporaryCompletedTab([[candidate(ids=["other-course"], titles=[TITLE])]])
        self.assertIsNone(await self.recover(temporary))
        self.assertFalse(any(command == "Page.navigate" for command, _ in temporary.commands))

    async def test_matching_title_without_course_link_still_requires_page_verification(self):
        temporary = TemporaryCompletedTab([[candidate(ids=[], titles=[TITLE])]])
        self.assertEqual(await self.recover(temporary), VERIFY)

    async def test_wrong_certificate_page_is_rejected_and_temporary_tab_closed(self):
        temporary = TemporaryCompletedTab([[candidate()]], certificate_title="Different Course")
        owner = OwnerTab()
        with patch.object(learning, "_open_temporary_completed_tab",
                          AsyncMock(return_value=(temporary, "temporary-target"))), \
             patch.object(learning, "POLL_SECONDS", 0), patch.object(learning, "PAGE_PROBES", 3):
            with self.assertRaisesRegex(RuntimeError, "did not show verified completion"):
                await learning.find_completed_certificate(owner, COURSE_ID, TITLE)
        self.assertTrue(temporary.closed)
        self.assertEqual(owner.commands, [("Target.closeTarget", {"targetId":"temporary-target"})])

    async def test_two_distinct_certificate_tokens_for_one_card_are_ambiguous(self):
        temporary = TemporaryCompletedTab([[candidate(), candidate(
            "https://www.coursera.org/account/accomplishments/records/DIFFERENT123")]])
        owner = OwnerTab()
        with patch.object(learning, "_open_temporary_completed_tab",
                          AsyncMock(return_value=(temporary, "temporary-target"))), \
             patch.object(learning, "POLL_SECONDS", 0):
            with self.assertRaisesRegex(RuntimeError, "ambiguous certificates"):
                await learning.find_completed_certificate(owner, COURSE_ID, TITLE)
        self.assertTrue(temporary.closed)

    async def test_invalid_course_id_never_opens_temporary_tab(self):
        owner = OwnerTab()
        with patch.object(learning, "_open_temporary_completed_tab", AsyncMock()) as opened:
            with self.assertRaises(ValueError):
                await learning.find_completed_certificate(owner, "../wrong", TITLE)
        opened.assert_not_awaited()

    async def test_temporary_target_is_connected_by_its_exact_id(self):
        engine = importlib.import_module("coursera_start")
        class CreatingOwner:
            def __init__(self):
                self.commands = []

            async def call(self, method, params):
                self.commands.append((method, params))
                if method == "Target.createTarget":
                    return {"targetId":"exact-new-target"}
                if method == "Target.closeTarget":
                    return {"success":True}
                raise AssertionError(method)

        class ConnectedTab:
            def __init__(self, address):
                self.address = address
                self.connected = False

            async def connect(self):
                self.connected = True

            async def close(self):
                pass

        owner = CreatingOwner()
        tabs = [{"type":"page", "id":"older-target", "webSocketDebuggerUrl":"ws://127.0.0.1:9223/devtools/page/old"},
                {"type":"page", "id":"exact-new-target", "webSocketDebuggerUrl":"ws://127.0.0.1:9223/devtools/page/new"}]
        with patch.object(engine, "local_json", return_value=tabs), \
             patch.object(engine, "ChromeTab", ConnectedTab):
            connected, target_id = await learning._open_temporary_completed_tab(owner)
        self.assertEqual(target_id, "exact-new-target")
        self.assertTrue(connected.connected)
        self.assertEqual(connected.address, tabs[1]["webSocketDebuggerUrl"])
        self.assertEqual(owner.commands,
                         [("Target.createTarget", {"url":learning.COMPLETED_LEARNING_URL})])

    async def test_failed_temporary_connection_closes_only_its_target(self):
        engine = importlib.import_module("coursera_start")
        class CreatingOwner:
            def __init__(self):
                self.commands = []

            async def call(self, method, params):
                self.commands.append((method, params))
                return {"targetId":"exact-new-target"} if method == "Target.createTarget" else {"success":True}

        class FailedTab:
            def __init__(self, address):
                self.closed = False

            async def connect(self):
                raise RuntimeError("connect failed")

            async def close(self):
                self.closed = True

        owner = CreatingOwner()
        tabs = [{"type":"page", "id":"exact-new-target", "webSocketDebuggerUrl":"ws://127.0.0.1:9223/devtools/page/new"}]
        with patch.object(engine, "local_json", return_value=tabs), \
             patch.object(engine, "ChromeTab", FailedTab):
            with self.assertRaisesRegex(RuntimeError, "connect failed"):
                await learning._open_temporary_completed_tab(owner)
        self.assertEqual(owner.commands[-1],
                         ("Target.closeTarget", {"targetId":"exact-new-target"}))


if __name__ == "__main__":
    unittest.main()
