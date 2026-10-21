"""Cooperative runner controls shared between the worker and local dashboard."""

from __future__ import annotations

import time

from .store import Store


class ControlError(RuntimeError):
    pass


class Control:
    def __init__(self, store: Store) -> None:
        self.store = store

    def get_state(self) -> str:
        return self.store.get_control_state()

    def mark_running(self) -> None:
        self.store.set_control_state("running")
        self.store.record_event("INFO", "RUNNER_STARTED", "Runner started")

    def mark_stopped(self) -> None:
        self.store.set_control_state("stopped")
        self.store.record_event("INFO", "RUNNER_STOPPED", "Runner stopped")

    def request_pause(self) -> None:
        state = self.get_state()
        if state != "running":
            raise ControlError(f"Cannot pause while runner state is {state}")
        self.store.set_control_state("paused")
        self.store.record_event("INFO", "PAUSE_REQUESTED", "Pause requested")

    def request_resume(self) -> None:
        state = self.get_state()
        if state != "paused":
            raise ControlError(f"Cannot resume while runner state is {state}")
        self.store.set_control_state("running")
        self.store.record_event("INFO", "RESUME_REQUESTED", "Resume requested")

    def request_stop(self) -> None:
        state = self.get_state()
        if state == "stopped":
            return
        self.store.set_control_state("stop_requested")
        self.store.record_event("INFO", "STOP_REQUESTED", "Graceful stop requested")

    def should_stop(self) -> bool:
        return self.get_state() in {"stop_requested", "stopped"}

    def wait_if_paused(self, poll_seconds: float = 0.3) -> bool:
        """Wait at a safe checkpoint. Return False when a stop was requested."""
        while True:
            state = self.get_state()
            if state == "running":
                return True
            if state in {"stop_requested", "stopped"}:
                return False
            time.sleep(max(0.05, poll_seconds))
