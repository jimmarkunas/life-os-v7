"""LIFE-1003 (REQ-85 T2): the budget guard for the isolated Honcho experiment. It talks to the meter and to Honcho only through two injected objects, so every failure can be played back deterministically.

The meter owns hard enforcement (its scope cap and documented-maximum reservations); the guard never substitutes a model for it. Every reading is the meter's own /health document and must show the expected scope: present, the expected id, the approved cap, state open, nothing blocked. Anything else stops the guard (and it disarms). Spend and the brake are the scope's own spend. The workspace starts DISARMED (no deriver or dream spending without an explicit switch) and the guard ARMS one step at a time, only when the queue is idle and settled and the step fits the modeled ceiling and one more reservation still fits the scope. Every failure (meter timeout, a missing or wrong scope, a refused configuration change, an expired token, a drain that never ends, no progress) stops the guard from arming anything further. A run is never guaranteed to complete: a meter refusal ends it, and it is never retried around the meter. The live trip is `um_budget.TRIP`."""

import importlib.util
import pathlib
import sys


def _load(name):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, pathlib.Path(__file__).with_name(name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


budget = _load("um_budget")

MAX_METER_ERRORS = 3
STALL_S = 180  # no spend and no finished unit for this long while armed: the meter may be refusing
SETTLE_S = 1.0
DRAIN_S = 600
WAIT_STEP_S = 1.0
RUN_S = 90 * 60  # the token lasts two hours; the run is over well before that


class Stop(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class MeterError(Exception):
    pass


class HonchoError(Exception):
    pass


class Unauthorized(HonchoError):
    pass


class Guard:
    def __init__(
        self, meter, honcho, clock, scope_id=None, ceiling=budget.CEILING, trip=budget.TRIP, tranche=budget.TRANCHE
    ):
        self.meter, self.hon, self.clock, self.ceiling, self.trip, self.tranche = (
            meter,
            honcho,
            clock,
            ceiling,
            trip,
            tranche,
        )
        self.scope_id, self.cap, self.scope_view = scope_id, budget.SCOPE_CAP, None
        self.events, self.last, self.units_seen, self.exposure = [], None, 0, 0.0
        self.units_seen_armed, self.spent_at_units = False, 0.0
        self.started = clock.now()

    # ---- readings
    def _log(self, what, **kw):
        self.events.append({"t": round(self.clock.now() - self.started, 1), "e": what, **kw})

    def _scope_spend(self, doc):
        """-> the scope's own spend from the meter's health document, or a Stop for anything but a healthy, expected, open scope."""
        if not isinstance(doc, dict) or doc.get("ok") is not True:
            raise Stop("meter_malformed") from None
        if doc.get("blocked"):
            raise Stop("meter_blocked") from None  # an expired scope reads as blocked
        scope = doc.get("scope")
        if not isinstance(scope, dict):
            raise Stop("scope_missing") from None
        if not self.scope_id or scope.get("id") != self.scope_id:
            raise Stop("scope_mismatch") from None
        try:
            cap, spent = float(scope["cap_usd"]), float(scope["spent_usd"])
        except (KeyError, TypeError, ValueError):
            raise Stop("meter_malformed") from None
        if spent < 0 or abs(cap - self.cap) > 1e-9:
            raise Stop("scope_cap_wrong" if spent >= 0 else "meter_malformed") from None
        if scope.get("state") != "open":
            raise Stop("scope_not_open") from None
        self.scope_view = {"id": scope["id"], "cap_usd": cap, "spent_usd": spent, "state": scope["state"]}
        return spent

    def _read(self):
        try:
            doc = self.meter.reading()
        except Exception as error:  # noqa: BLE001  any failure of the meter is a failed reading
            raise MeterError(type(error).__name__) from error
        value = self._scope_spend(doc)
        if self.last is not None and value < self.last - 1e-9:
            raise MeterError("ledger_went_backwards")
        self.last = value
        return value

    def _deadline(self):
        if self.clock.now() - self.started > RUN_S:
            raise Stop("deadline") from None

    def _settled(self):
        """The queue is idle and two readings a second apart agree: nothing is in flight and the ledger is final. -> spend since the start."""
        waited = 0.0
        while True:
            self._deadline()
            try:
                idle = self.hon.queue_idle()
            except Unauthorized:
                raise Stop("token_expired") from None
            except HonchoError:
                raise Stop("honcho_unreadable") from None
            if idle:
                try:
                    first = self._read()
                    self.clock.sleep(SETTLE_S)
                    second = self._read()
                except MeterError as error:
                    raise Stop("meter_" + str(error)) from None
                if first == second and self.hon.queue_idle():
                    break
            self.clock.sleep(WAIT_STEP_S)
            waited += WAIT_STEP_S
            if waited > DRAIN_S:
                raise Stop("drain_timeout") from None
        spent = second
        done = self.hon.completed_units()
        if done > self.units_seen and spent <= self.spent_at_units + budget.CHECK_DELTA and self.units_seen_armed:
            raise Stop("meter_stale")  # work finished but the ledger did not move: the reading cannot be trusted
        self.units_seen, self.spent_at_units = done, spent
        return spent

    def brake(self):
        """Disarm. Best effort: a refused change is recorded, never raised, because the armed exposure is already inside the bound."""
        try:
            self.hon.set_flags(False, False)
            self._log("disarmed")
        except Exception as error:  # noqa: BLE001
            self._log("disarm_failed", error=type(error).__name__)

    def _watch(self, until_idle):
        """While a step runs: read the meter every second; at the trip, disarm and stop. Meter errors are counted; the step is still bounded by its own exposure."""
        errors, waited, snap, since = 0, 0.0, None, self.clock.now()
        while not until_idle():
            self._deadline()
            try:
                spent = self._read()
                errors = 0
                if spent >= self.trip:
                    raise Stop("tripped") from None
                progress = (round(spent, 6), self.hon.completed_units())
                if progress != snap:
                    snap, since = progress, self.clock.now()
                elif self.clock.now() - since > STALL_S:
                    raise Stop("no_progress") from None
            except MeterError:
                errors += 1
                if errors >= MAX_METER_ERRORS:
                    self._log("meter_unreadable_while_armed")
            except Unauthorized:
                self.brake()
                raise Stop("token_expired") from None
            except HonchoError:
                self.brake()
                raise Stop("honcho_unreadable") from None
            except Stop:
                self.brake()  # a wrong scope, a block, the trip or a stall: disarm first, then stop
                raise
            self.clock.sleep(WAIT_STEP_S)
            waited += WAIT_STEP_S
            if waited > DRAIN_S:
                self.brake()
                raise Stop("drain_timeout") from None

    def _inputs_ok(self, batch=()):
        """Refuse BEFORE anything is armed when a message or the workspace's text is larger than the limits the bound assumes."""
        if any(len(m.get("content", "")) > budget.MESSAGE_CHARS_MAX for m in batch):
            raise Stop("message_too_long") from None
        try:
            volume = self.hon.volume_chars()
        except Unauthorized:
            raise Stop("token_expired") from None
        except HonchoError:
            raise Stop("honcho_unreadable") from None
        if volume + sum(len(m.get("content", "")) for m in batch) > budget.VOLUME_CHARS_MAX:
            raise Stop("volume_exceeded") from None

    # ---- the run
    def seed(self, messages):
        sent = 0
        spent = self._settled()
        self._log("start", spent=round(spent, 5))
        for i in range(0, len(messages), self.tranche):
            batch = messages[i : i + self.tranche]
            spent = self._settled()
            exposure = budget.tranche_worst(len(batch))
            self._inputs_ok(batch)
            if not budget.can_arm(spent, exposure, budget.dream_worst(), self.ceiling):
                raise Stop("budget_before_tranche_%d" % (i // self.tranche + 1)) from None
            if not budget.admissible(spent, self.cap):
                raise Stop(
                    "admission_limit"
                ) from None  # one more reservation would not fit the scope: the meter would refuse
            try:
                self.hon.post_messages(batch, armed=True)
            except Unauthorized:
                raise Stop("token_expired") from None
            except HonchoError:
                raise Stop("post_failed") from None
            self.units_seen_armed, sent = True, sent + len(batch)
            self.exposure += exposure
            self._log("armed_tranche", n=len(batch), spent=round(spent, 5), exposure=round(exposure, 4))
            self._watch(lambda: self._idle_safely())
            after = self._settled()
            if after - spent > exposure + budget.CHECK_DELTA:
                raise Stop("exposure_exceeded")  # the model's per-call assumption was wrong: no more arming
        return sent

    def _idle_safely(self):
        try:
            return self.hon.queue_idle()
        except Unauthorized:
            raise Stop("token_expired") from None
        except HonchoError:
            raise Stop("honcho_unreadable") from None

    def dream(self):
        spent = self._settled()
        try:
            count = self.hon.explicit_count()
        except Unauthorized:
            raise Stop("token_expired") from None
        except HonchoError:
            raise Stop("honcho_unreadable") from None
        if count > budget.EXPLICIT_MAX:
            raise Stop("too_many_conclusions_for_isolation") from None
        exposure = budget.dream_worst()
        self._inputs_ok()
        if not budget.can_arm(spent, exposure, 0.0, self.ceiling):
            raise Stop("budget_before_dream") from None
        if not budget.admissible(spent, self.cap):
            raise Stop("admission_limit") from None
        try:
            self.hon.set_flags(True, True)
        except Unauthorized:
            self.brake()
            raise Stop("token_expired") from None
        except HonchoError:
            self.brake()
            raise Stop("arm_failed") from None
        self.exposure += exposure
        self._log("armed_dream", spent=round(spent, 5), exposure=round(exposure, 4))
        try:
            self.hon.schedule_dream()
        except HonchoError:
            self.brake()
            raise Stop("schedule_failed") from None
        self.units_seen_armed = False
        try:
            self._watch(lambda: self.hon.dream_finished())
        finally:
            self.brake()
        if self._settled() - spent > exposure + budget.CHECK_DELTA:
            raise Stop("exposure_exceeded") from None
        return True

    def run(self, messages, seeded=False):
        """-> the report. Never raises: a Stop is the reason the run ended early. `seeded=True`: the conclusions were created directly (no deriver spending), so only the dream is armed."""
        reason = ""
        try:
            try:
                self._read()  # the scope must be the expected, open one before anything else is touched
            except MeterError as error:
                raise Stop("meter_" + str(error)) from None
            self.hon.set_flags(False, False)
            if tuple(self.hon.get_flags()) != (False, False):
                raise Stop("flags_unverified") from None
            if not seeded:
                self.seed(messages)
            self.dream()
        except Stop as stop:
            reason = stop.reason
        except Exception as error:  # noqa: BLE001  any surprise is a stop, never a continued spend
            reason = "unexpected_" + type(error).__name__
        finally:
            self.brake()
        try:
            final = self._read()
        except (MeterError, Stop):
            final = None
        return {
            "stopped": reason or None,
            "spent_usd": None if final is None else round(final, 6),
            "armed_exposure_usd": round(self.exposure, 4),
            "scope": self.scope_view,
            "cleanup": self._cleanup(),
            "events": self.events,
        }

    def _cleanup(self):
        """What a reader needs to drain and close: the switches as last read and whether the queue is idle (None when Honcho could not be read)."""
        out = {}
        for key, call in (("flags", lambda: list(self.hon.get_flags())), ("queue_idle", self.hon.queue_idle)):
            try:
                out[key] = call()
            except Exception as error:  # noqa: BLE001
                out[key] = None
                out[key + "_error"] = type(error).__name__
        return out
