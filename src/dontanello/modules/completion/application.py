"""Completion rules independent of transport, storage and worker."""

from dataclasses import dataclass

from .ports import CheckboxState, Clock, CompletionSource


@dataclass
class CompletionTracker:
    source: CompletionSource
    state: CheckboxState
    clock: Clock

    def run(self) -> int:
        completed = 0
        for observation in self.source.observations():
            checked = observation.checked
            if self.state.get(observation.key) is False and checked:
                checked = self.source.is_checked(observation.item_id)
                if checked:
                    self.source.stamp(observation.item_id, self.clock.today())
                    completed += 1
            # First observation is a baseline. On write failure retain old state.
            self.state.record(observation.key, checked)
        return completed
