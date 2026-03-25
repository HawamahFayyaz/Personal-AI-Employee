# Skill: FilesystemWatcher

## What it does

`FilesystemWatcher` monitors the vault's `Inbox/` folder in real time using the
**watchdog** library. The moment a file lands in `Inbox/`, it is automatically:

1. **Copied** to `Needs_Action/` with a `FILE_` prefix.
2. **Documented** — a companion `.md` metadata file is created alongside it
   containing YAML frontmatter and a checklist of suggested actions.

This turns any file drop into a structured, trackable task without any manual
intervention.

## File location

```
Watchers/filesystem_watcher.py
```

## Class interface

```python
class FilesystemWatcher(BaseWatcher):
    def __init__(self, vault_path: str, check_interval: int = 60): ...
    def run(self): ...  # blocking — starts watchdog Observer
```

Inherits from `BaseWatcher` (`Watchers/base_watcher.py`).
`check_for_updates()` is a no-op stub (watchdog callbacks drive everything).
`create_action_file(source: Path)` is called per-file by the internal event handler.

### Constructor parameters

| Parameter        | Type  | Default | Description                                      |
|------------------|-------|---------|--------------------------------------------------|
| `vault_path`     | `str` | —       | Absolute or relative path to the vault root.     |
| `check_interval` | `int` | `60`    | Seconds between watchdog liveness heartbeats.    |

### Folders managed automatically

| Folder                  | Purpose                                          |
|-------------------------|--------------------------------------------------|
| `<vault>/Inbox/`        | Drop zone — watched for new files.               |
| `<vault>/Needs_Action/` | Output — receives copied file + metadata sidecar.|

Both directories are created at startup if they do not exist.

## Output format

For each dropped file `example.pdf` two entries appear in `Needs_Action/`:

```
Needs_Action/
  FILE_example.pdf          ← verbatim copy
  FILE_example.pdf.md       ← metadata sidecar
```

### Metadata sidecar (`FILE_example.pdf.md`)

```markdown
---
type: file_drop
original_name: example.pdf
size: 204800
dropped_at: 2025-10-01T14:32:00+00:00
status: pending
priority: medium
---
New file dropped for processing: example.pdf

## Suggested Actions
- [ ] Review file contents
- [ ] Process or delegate
- [ ] Move to Done when complete
```

## Usage

```python
import logging
from Watchers.filesystem_watcher import FilesystemWatcher

logging.basicConfig(level=logging.INFO)

watcher = FilesystemWatcher(vault_path='/path/to/vault', check_interval=30)
watcher.run()   # blocks; prints startup banner then watches forever
```

Run in a background thread alongside other watchers:

```python
import threading

t = threading.Thread(target=watcher.run, daemon=True)
t.start()
```

Stop cleanly with `Ctrl-C` — the Observer is joined before exit.

## Error handling

- If a file is locked when first copied, the watcher sleeps 1 second and
  retries once before raising.
- All other exceptions during file processing are caught and logged at `ERROR`
  level with a full traceback; the watcher continues running.
- Directory-creation events are ignored automatically.

## Dependencies

| Package    | Used for                            |
|------------|-------------------------------------|
| `watchdog` | Real-time filesystem event listener |
| `shutil`   | Copying files (stdlib)              |
| `pathlib`  | Path manipulation (stdlib)          |

Install watchdog if not present:

```bash
pip install watchdog
```
