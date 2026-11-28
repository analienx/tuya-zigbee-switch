"""Independent decision model; transport availability is tested separately."""
from dataclasses import dataclass


@dataclass
class Controller:
    phase: int = 0
    generation: int = 0
    master: bool = True
    dark: bool = True
    grace: int = 0
    config: tuple = (False, 152, 15, 30)
    snapshot: tuple = (False, 152, 15, 30)

    def decide(self, event, now):
        intent = None
        if event == 'disable':
            self.master = False
            if self.phase:
                intent = False
            self.phase = 0
        elif event == 'enable':
            self.master = True
        elif event == 'absence' and self.phase == 1:
            self.phase = 2
            self.grace = now + self.snapshot[3] * 100 + 2000
            intent = False
        elif event == 'motion' and self.master and self.phase != 1:
            if self.phase == 2 and now < self.grace:
                self.phase = 1
                intent = True
            elif not self.config[0] or self.dark:
                self.snapshot = self.config
                self.phase = 1
                intent = True
            else:
                self.phase = 0
        if intent is not None:
            self.generation = (self.generation + 1) % 65536
        return intent
