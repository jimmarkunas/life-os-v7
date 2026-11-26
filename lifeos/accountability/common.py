"""Shared run of one V3 accountability stage: for Jim then Matt, read Jira, write the callout and summary page, read
both back. A person whose Jira read fails gets a DEGRADED stamp (body kept); the other is still written, then the
stage fails.
"""
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from lifeos.accountability import region
from lifeos.accountability.read import PEOPLE, site_url
from lifeos.accountability.render import degraded_stamp
from lifeos.platform.jira import Jira, JiraError
from lifeos.platform.notion_client import Client

CHI = ZoneInfo("America/Chicago")


def notion_writer(environ):
    return Client({"NOTION_API_TOKEN": (environ.get("NOTION_JIRA_TOKEN") or "").strip(), "NOTION_JOB_LEDGER_DATA_SOURCE_ID": "unused"})


def run_region(kind, build, environ=None, jira=None, notion=None, now=None, live=False):
    environ = os.environ if environ is None else environ
    now = now or datetime.now(CHI)
    notion = notion or notion_writer(environ)
    jira = jira or Jira.from_env(environ)
    site = site_url(environ)
    counts, degraded = {}, []
    for person, project in PEOPLE:
        block_id = region.address(notion, f"{person.lower()}.{kind}")
        client = region.scoped(notion, block_id)
        try:
            counts[person.lower()] = build(client, block_id, person, project, jira, now, live, site, notion)
        except JiraError:
            counts[person.lower()] = {"degraded": True, "stamp_written": region.degrade(client, block_id, lambda old: degraded_stamp(old, now), live)}
            degraded.append(person.lower())
    if degraded:
        raise region.AccountabilityError("ACCOUNTABILITY_DEGRADED:" + ",".join(degraded))
    return counts
