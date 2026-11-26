"""LIFE-1003 (REQ-85 T2): the budget guard for the isolated Honcho experiment. It talks to the meter and to Honcho only through two injected objects, so every failure can be played back deterministically.

How the ceiling is kept (the model is tools/um_budget.py): the workspace starts DISARMED (no deriver or dream spending without an explicit switch) and the guard ARMS one step at a time, only when the queue is idle and the meter reading is fresh,
settled and consistent, and only when (spend so far) + (this step's worst case) + (the dream's worst case) fits the ceiling. Seeding messages are armed one by one with a per-message switch, so a guard that dies leaves nothing armed beyond the step
already running. Every failure (meter timeout or stale reading, a refused configuration change, an expired token, a drain that never ends) stops the guard from arming anything further. The live trip is a second, earlier brake, not the bound."""
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
SETTLE_S = 1.0
DRAIN_S = 600
WAIT_STEP_S = 1.0
RUN_S = 90 * 60                                            # the token lasts two hours; the run is over well before that


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
    def __init__(self, meter, honcho, clock, ceiling=budget.CEILING, trip=budget.TRIP, tranche=budget.TRANCHE):
        self.meter, self.hon, self.clock, self.ceiling, self.trip, self.tranche = meter, honcho, clock, ceiling, trip, tranche
        self.events, self.base, self.last, self.units_seen, self.exposure = [], None, None, 0, 0.0
        self.units_seen_armed, self.spent_at_units = False, 0.0
        self.started = clock.now()

    # ---- readings
    def _log(self, what, **kw):
        self.events.append({"t": round(self.clock.now() - self.started, 1), "e": what, **kw})

    def _read(self):
        try:
            value = float(self.meter.spent())
        except Exception as error:                                                              # noqa: BLE001  any failure of the meter is a failed reading
            raise MeterError(type(error).__name__) from error
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
        if self.base is None:
            self.base = second
        spent = second - self.base
        done = self.hon.completed_units()
        if done > self.units_seen and spent <= self.spent_at_units + budget.CHECK_DELTA and self.units_seen_armed:
            raise Stop("meter_stale")                                                           # work finished but the ledger did not move: the reading cannot be trusted
        self.units_seen, self.spent_at_units = done, spent
        return spent

    def brake(self):
        """Disarm. Best effort: a refused change is recorded, never raised, because the armed exposure is already inside the bound."""
        try:
            self.hon.set_flags(False, False)
            self._log("disarmed")
        except Exception as error:                                                              # noqa: BLE001
            self._log("disarm_failed", error=type(error).__name__)

    def _watch(self, until_idle):
        """While a step runs: read the meter every second; at the trip, disarm and stop. Meter errors are counted; the step is still bounded by its own exposure."""
        errors, waited = 0, 0.0
        while not until_idle():
            self._deadline()
            try:
                spent = self._read() - (self.base or 0.0)
                errors = 0
                if spent >= self.trip:
                    self.brake()
                    raise Stop("tripped") from None
            except MeterError:
                errors += 1
                if errors >= MAX_METER_ERRORS:
                    self._log("meter_unreadable_while_armed")
            self.clock.sleep(WAIT_STEP_S)
            waited += WAIT_STEP_S
            if waited > DRAIN_S:
                self.brake()
                raise Stop("drain_timeout") from None

    # ---- the run
    def seed(self, messages):
        sent = 0
        spent = self._settled()
        self._log("start", spent=round(spent, 5))
        for i in range(0, len(messages), self.tranche):
            batch = messages[i:i + self.tranche]
            spent = self._settled()
            exposure = budget.tranche_worst(len(batch))
            if not budget.can_arm(spent, exposure, budget.dream_worst(), self.ceiling):
                raise Stop("budget_before_tranche_%d" % (i // self.tranche + 1)) from None
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
                raise Stop("exposure_exceeded")                                                 # the model's per-call assumption was wrong: no more arming
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
        if not budget.can_arm(spent, exposure, 0.0, self.ceiling):
            raise Stop("budget_before_dream") from None
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

    def run(self, messages):
        """-> the report. Never raises: a Stop is the reason the run ended early."""
        reason = ""
        try:
            self.hon.set_flags(False, False)
            if tuple(self.hon.get_flags()) != (False, False):
                raise Stop("flags_unverified") from None
            self.seed(messages)
            self.dream()
        except Stop as stop:
            reason = stop.reason
        except Exception as error:                                                              # noqa: BLE001  any surprise is a stop, never a continued spend
            reason = "unexpected_" + type(error).__name__
        finally:
            self.brake()
        try:
            final = self._read() - (self.base or 0.0)
        except MeterError:
            final = None
        return {"stopped": reason or None, "spent_usd": None if final is None else round(final, 6), "armed_exposure_usd": round(self.exposure, 4), "events": self.events}
