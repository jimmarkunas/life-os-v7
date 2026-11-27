"""The daily reconcile's promotion call site (off unless V2_PROMOTION is on); lifeos.run hands it to the Jira stage as `after`.

It lives here because the Jira package may not import the promotion package."""

from lifeos.accountability import common, official_setter, promotion as check, promotion_inputs, switches

PROMOTION_ALERT_KEY = "v2_promotion_failed"


def promotion(environ, now, live, alert, client=None, stability=None, client_factory=None, readers=None):
    """Off: no-op. Before the Jan 6 boundary or already official: nothing. Else the real inputs are read from the
    membership file and Jira (`client`); a first pass is recorded, the second pass on the next date runs the setter
    once (dry run unless live); a fail resets the window, keeps legacy and alerts once. Without a database window
    nothing can be promoted."""
    state = switches.snapshot(environ)
    if not state["V2_PROMOTION"]:
        return {"status": "not_enabled"}

    def send(item):
        alert(item["subject"], item["details"], PROMOTION_ALERT_KEY, True)

    writer = (client_factory or common.notion_writer)(environ)
    setter = official_setter.official_setter(writer, environ.get("V7_RUNTIME_CONFIG_SOURCE_ID", ""), live)
    return check.run_if_due(
        now,
        {"V2_PROMOTION": True, "V2_OFFICIAL": state["V2_OFFICIAL"]},
        readers or promotion_inputs.readers(client, environ, now.date()),
        setter,
        send,
        stability,
    )
