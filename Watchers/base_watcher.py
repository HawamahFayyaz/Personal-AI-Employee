import logging
import time
from abc import ABC, abstractmethod
from pathlib import Path


class BaseWatcher(ABC):
    def __init__(self, vault_path: str, check_interval: int = 60):
        self.vault_path = Path(vault_path)
        self.needs_action = self.vault_path / 'Needs_Action'
        self.check_interval = check_interval
        self.logger = logging.getLogger(self.__class__.__name__)

        self.needs_action.mkdir(parents=True, exist_ok=True)

    @abstractmethod
    def check_for_updates(self):
        """Check the monitored source for new items or changes."""
        ...

    @abstractmethod
    def create_action_file(self, data):
        """Write a task file into the Needs_Action folder."""
        ...

    def run(self):
        self.logger.info(
            "Starting %s (interval=%ss, vault=%s)",
            self.__class__.__name__,
            self.check_interval,
            self.vault_path,
        )
        while True:
            try:
                updates = self.check_for_updates()
                if updates:
                    self.create_action_file(updates)
            except Exception:
                self.logger.exception(
                    "Unhandled error in %s — continuing.", self.__class__.__name__
                )
            time.sleep(self.check_interval)
