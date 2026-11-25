"""LIFE-918 AC6 call site: the daily reconcile stage's promotion check (off unless V2_PROMOTION is on). Kept in this package because the Jira package may not import it; lifeos.run hands it to the stage as `after`.

V2_PROMOTION off: nothing. On: promotion.run_if_due (the Fri Jan 1 2027 8:00 AM CT boundary, already-official, evaluate AC11 and AC12); on a pass the LIFE-976 setter runs exactly once (a dry run when the run is not live),
on a fail legacy stays official and one alert is sent, once. Codes and counts only."""
from lifeos.accountability import common, official_setter, promotion as check, switches

PROMOTION_ALERT_KEY = "v2_promotion_failed"


def promotion(environ, now, live, alert, client_factory=None, readers=None):
    """The AC6 call site: V2_PROMOTION off is a no-op; before the boundary or when already official nothing happens; on a pass the LIFE-976 setter runs exactly once (dry run unless live); a fail keeps legacy official and
    sends one alert. The inputs of AC11/AC12 come from `readers`; the shipped default is the empty frozen membership, so it fails closed with MEMBERSHIP_MISSING until the Q1 2027 list is approved and a reader is built."""
    state = switches.snapshot(environ)
    if not state["V2_PROMOTION"]:
        return {"status": "not_enabled"}

    def missing():
        raise check.PromotionError(check.MEMBERSHIP_MISSING)

    def send(item):
        alert(item["subject"], item["details"], PROMOTION_ALERT_KEY, True)
    client = (client_factory or common.notion_writer)(environ)
    setter = official_setter.official_setter(client, environ.get("V7_RUNTIME_CONFIG_SOURCE_ID", ""), live)
    return check.run_if_due(now, {"V2_PROMOTION": True, "V2_OFFICIAL": state["V2_OFFICIAL"]}, readers or missing, setter, send)
