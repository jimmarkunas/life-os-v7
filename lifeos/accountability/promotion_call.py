"""The daily reconcile's promotion call site (off unless V2_PROMOTION is on); lifeos.run hands it to the Jira stage as `after`.

It lives here because the Jira package may not import the promotion package."""

from lifeos.accountability import common, official_setter, promotion as check, switches

PROMOTION_ALERT_KEY = "v2_promotion_failed"


def promotion(environ, now, live, alert, client_factory=None, readers=None):
    """Off: no-op. Before the boundary or already official: nothing. A pass runs the setter once (dry run unless
    live); a fail keeps legacy and alerts once. The default `readers` has an empty frozen membership, so it fails
    closed naming JFM and MEJ."""
    state = switches.snapshot(environ)
    if not state["V2_PROMOTION"]:
        return {"status": "not_enabled"}

    def missing():
        return {"quarter": "2027-Q1", "boards": {name: {} for name in check.BOARDS}}

    def send(item):
        alert(item["subject"], item["details"], PROMOTION_ALERT_KEY, True)

    client = (client_factory or common.notion_writer)(environ)
    setter = official_setter.official_setter(client, environ.get("V7_RUNTIME_CONFIG_SOURCE_ID", ""), live)
    return check.run_if_due(
        now, {"V2_PROMOTION": True, "V2_OFFICIAL": state["V2_OFFICIAL"]}, readers or missing, setter, send
    )
