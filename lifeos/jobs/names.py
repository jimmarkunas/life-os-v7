"""Employer-name comparison shared by the hiring-pipeline handoff and the sponsor register. Deterministic, no fuzzy scoring."""
from lifeos.jobs.fit.profile import norm

LEGAL = {"inc", "llc", "ltd", "limited", "corp", "corporation", "co", "plc", "gmbh", "company", "the", "incorporated", "lp", "llp", "uk", "u.k"}
GENERIC = {"group", "holdings", "holding", "international", "global", "europe", "services", "technologies", "technology", "tech", "solutions",
           "systems", "software", "labs", "digital", "bank", "worldwide", "partners", "consulting", "emea", "services"}
INTERMEDIARY = {"recruitment", "recruiting", "recruiters", "staffing", "resourcing", "headhunters", "headhunting", "talent", "agency", "hays", "reed",
                "adecco", "randstad", "manpower", "experis", "hiring", "jobgether", "personnel"}


def tokens(name):
    out = (t.strip(".") for t in norm(name).split())
    return [t for t in out if t and t not in LEGAL]


def core(name):
    """The distinctive tokens: legal and generic trailing words dropped ("Monzo Bank Limited" -> ["monzo"])."""
    out = tokens(name)
    while len(out) > 1 and out[-1] in GENERIC:
        out.pop()
    return out


def is_intermediary(name):
    return bool(INTERMEDIARY & set(tokens(name))) or any(p in norm(name) for p in (" michael page ", " robert half ", " harvey nash ", " executive search "))


def same_employer(a, b):
    """Equal distinctive tokens. A bare shared first word is not enough ("Smith" is not "Smith & Wesson")."""
    x, y = core(a), core(b)
    return bool(x) and x == y and not (x and x[0] in GENERIC)
