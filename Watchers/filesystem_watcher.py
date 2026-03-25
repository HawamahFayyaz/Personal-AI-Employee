import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from watchdog.events import FileCreatedEvent, FileSystemEventHandler
from watchdog.observers.polling import PollingObserver as Observer

from Watchers.base_watcher import BaseWatcher


class _InboxEventHandler(FileSystemEventHandler):
    """Watchdog event handler that reacts to new files in the Inbox."""

    def __init__(self, watcher: "FilesystemWatcher"):
        super().__init__()
        self._watcher = watcher

    def on_created(self, event: FileCreatedEvent):
        if event.is_directory:
            return
        try:
            self._watcher.create_action_file(Path(event.src_path))
        except Exception:
            self._watcher.logger.exception(
                "Error processing new file: %s", event.src_path
            )


class FilesystemWatcher(BaseWatcher):
    """
    Watches the vault's Inbox/ folder using watchdog.

    When a file is dropped into Inbox/:
      1. Copies it to Needs_Action/ with a FILE_ prefix.
      2. Creates a companion .md metadata file in Needs_Action/.
    """

    def __init__(self, vault_path: str, check_interval: int = 60):
        super().__init__(vault_path, check_interval)
        self.inbox = self.vault_path / "Inbox"
        self.inbox.mkdir(parents=True, exist_ok=True)

        self._handler = _InboxEventHandler(self)
        self._observer = Observer()
        self._observer.schedule(self._handler, str(self.inbox), recursive=False)

    # ------------------------------------------------------------------
    # BaseWatcher abstract-method implementations
    # ------------------------------------------------------------------

    def check_for_updates(self):
        """
        Not used for event-driven logic; exists to satisfy the ABC contract.
        Returns None — watchdog callbacks handle everything.
        """
        return None

    def create_action_file(self, source: Path):
        """Copy *source* into Needs_Action/ and write a metadata sidecar."""
        dest_name = f"FILE_{source.name}"
        dest_file = self.needs_action / dest_name
        meta_file = self.needs_action / f"{dest_name}.md"

        # Copy the file (retry once if it's still being written)
        try:
            shutil.copy2(source, dest_file)
        except (PermissionError, OSError):
            self.logger.warning(
                "File locked, retrying in 1s: %s", source.name
            )
            time.sleep(1)
            shutil.copy2(source, dest_file)

        size = dest_file.stat().st_size
        dropped_at = datetime.now(timezone.utc).isoformat()

        meta_content = (
            "---\n"
            "type: file_drop\n"
            f"original_name: {source.name}\n"
            f"size: {size}\n"
            f"dropped_at: {dropped_at}\n"
            "status: pending\n"
            "priority: medium\n"
            "---\n"
            f"New file dropped for processing: {source.name}\n"
            "\n"
            "## Suggested Actions\n"
            "- [ ] Review file contents\n"
            "- [ ] Process or delegate\n"
            "- [ ] Move to Done when complete\n"
        )
        meta_file.write_text(meta_content, encoding="utf-8")

        self.logger.info(
            "Queued '%s' → Needs_Action/%s  (%d bytes)", source.name, dest_name, size
        )

    # ------------------------------------------------------------------
    # Overridden run() — uses watchdog Observer instead of a poll loop
    # ------------------------------------------------------------------

    def run(self):
        print(
            f"[FilesystemWatcher] Monitoring started.\n"
            f"  Vault     : {self.vault_path}\n"
            f"  Watching  : {self.inbox}\n"
            f"  Output    : {self.needs_action}\n"
            f"Drop a file into Inbox/ to trigger an action.\n"
        )
        self.logger.info(
            "FilesystemWatcher started — watching %s", self.inbox
        )

        self._observer.start()
        try:
            while True:
                time.sleep(self.check_interval)
        except KeyboardInterrupt:
            self.logger.info("Shutdown requested — stopping observer.")
        finally:
            self._observer.stop()
            self._observer.join()
            self.logger.info("FilesystemWatcher stopped.")
