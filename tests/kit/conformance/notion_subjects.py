"""Observers for the notion family: the recorded real response shapes, and the FakeBlocks fake. Domain-free."""
import copy

from tests.kit.conformance import runner
from tests.kit.notion import FakeBlocks

RECORDING = runner.recording("notion_blocks")
ACTION_RECORD = {"list": "list_children", "delete": "delete_block", "update": "update_block", "append": "append_children"}


def _summarize(case, response):
    out = {"missing_keys": [k for k in case["must_include"] if k not in response]}
    if case["action"] == "list":
        out["has_more"] = response.get("has_more")
    if case["action"] == "delete":
        out["archived"] = response.get("archived")
    if case["action"] == "update":
        out["new_text"] = ((response.get("paragraph") or {}).get("rich_text") or [{}])[0].get("plain_text")
    if case["action"] == "append":
        first = (response.get("results") or [{}])[0]
        out["new_block_has_object"] = first.get("object") == "block"
    return out


def observe_recording(case):
    return _summarize(case, RECORDING[case["record"]])


def observe_fake_blocks(case):
    kid = copy.deepcopy(RECORDING["list_children"]["results"][0])
    fake, action = FakeBlocks([kid]), case["action"]
    if action == "list":
        response = fake.call("GET", "/blocks/parent-fake/children")
    elif action == "delete":
        response = fake.call("DELETE", "/blocks/" + kid["id"])
    elif action == "update":
        response = fake.call("PATCH", "/blocks/" + kid["id"], {"paragraph": {"rich_text": [{"text": {"content": "new fake text"}}]}})
    else:
        response = fake.call_once("POST", "/blocks/other-fake/children", {"children": [{"type": "paragraph", "paragraph": {"rich_text": [{"text": {"content": "added fake text"}}]}}]})
    return _summarize(case, response)
