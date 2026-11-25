"""LIFE-918 AC6: the daily reconcile's promotion call site (off unless V2_PROMOTION is on); lifeos.run hands it to the Jira stage as `after`.

Kept here because the Jira package may not import this one. Off: nothing. On: promotion.run_if_due (Fri Jan 1 2027 8:00 AM CT, not already official); a pass runs the LIFE-976 setter once, a fail keeps legacy and alerts once."""

from lifeos.accountability import common, official_setter, promotion as check, switches

PROMOTION_ALERT_KEY = "v2_promotion_failed"


def promotion(environ, now, live, alert, client_factory=None, readers=None):
    """The AC6 call site: V2_PROMOTION off is a no-op; before the boundary or when already official nothing happens; on a pass the LIFE-976 setter runs exactly once (dry run unless live); a fail keeps legacy official and
    sends one alert. The inputs of AC11/AC12 come from `readers`; the shipped default is the empty frozen membership of both boards, so it fails closed naming JFM and MEJ until the Q1 2027 list is approved and a reader is built."""
    state = switches.snapshot(environ)
    if not state["V2_PROMOTION"]:
        return {"status": "not_enabled"}

    def missing():
        return {"quarter": "2027-Q1", "boards": {name: {} for name in check.BOARDS}}                    # no approved frozen list for either board yet: both fail closed and the alert names them

    def send(item):
        alert(item["subject"], item["details"], PROMOTION_ALERT_KEY, True)
    client = (client_factory or common.notion_writer)(environ)
    setter = official_setter.official_setter(client, environ.get("V7_RUNTIME_CONFIG_SOURCE_ID", ""), live)
    return check.run_if_due(now, {"V2_PROMOTION": True, "V2_OFFICIAL": state["V2_OFFICIAL"]}, readers or missing, setter, send)
