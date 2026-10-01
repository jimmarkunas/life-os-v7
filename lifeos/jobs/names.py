"""Employer-name comparison for Jobs: the hiring-pipeline handoff and the sponsor register. The normalizer and the token rules live in
`lifeos.platform.names` (shared with Interview OS); this module keeps only what is Jobs' own: recruiter/intermediary detection."""
from lifeos.platform.names import GENERIC, LEGAL, core, norm, same_employer, tokens          # noqa: F401 - re-exported for existing callers

INTERMEDIARY = {"recruitment", "recruiting", "recruiters", "staffing", "resourcing", "headhunters", "headhunting", "talent", "agency", "hays", "reed",
                "adecco", "randstad", "manpower", "experis", "hiring", "jobgether", "personnel"}


def is_intermediary(name):
    return bool(INTERMEDIARY & set(tokens(name))) or any(p in norm(name) for p in (" michael page ", " robert half ", " harvey nash ", " executive search "))
