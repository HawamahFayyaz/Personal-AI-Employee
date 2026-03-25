# Skill: BaseWatcher

## What it does

`BaseWatcher` is an abstract base class for all vault watchers. A watcher monitors
an external or internal source (inbox folder, email, API, etc.) on a fixed interval
and drops a task file into the vault's `Needs_Action/` folder whenever something
requires attention.

## File location

```
Watchers/base_watcher.py
```

## Class interface

```python
class BaseWatcher(ABC):
    def __init__(self, vault_path: str, check_interval: int = 60): ...

    @abstractmethod
    def check_for_updates(self): ...       # return data, or None / empty

    @abstractmethod
    def create_action_file(self, data): ... # write file to Needs_Action/

    def run(self): ...                      # blocking loop — call this to start
```

### Constructor parameters

| Parameter        | Type  | Default | Description                                    |
|------------------|-------|---------|------------------------------------------------|
| `vault_path`     | `str` | —       | Absolute or relative path to the vault root.   |
| `check_interval` | `int` | `60`    | Seconds to sleep between each check cycle.     |

### Attributes set by `__init__`

| Attribute        | Value                           |
|------------------|---------------------------------|
| `self.vault_path`   | `Path(vault_path)`           |
| `self.needs_action` | `vault_path / 'Needs_Action'`|
| `self.check_interval` | as supplied               |
| `self.logger`       | `logging.getLogger(<ClassName>)` |

`Needs_Action/` is created automatically if it does not exist.

## How to implement a concrete watcher

```python
from Watchers.base_watcher import BaseWatcher

class InboxWatcher(BaseWatcher):
    def check_for_updates(self):
        files = list((self.vault_path / 'Inbox').glob('*.md'))
        return files if files else None

    def create_action_file(self, data):
        for f in data:
            action = self.needs_action / f.name
            action.write_text(f.read_text())
            f.unlink()
            self.logger.info("Queued %s for action", f.name)
```

Start the watcher:

```python
watcher = InboxWatcher(vault_path='/path/to/vault', check_interval=30)
watcher.run()   # blocks forever; run in a thread or subprocess
```

## Behaviour notes

- `run()` loops indefinitely; wrap it in a `threading.Thread` or a separate
  process if you need to run multiple watchers concurrently.
- Any exception raised inside a loop iteration is caught, logged at `ERROR`
  level with a full traceback, and the loop continues — the watcher never dies
  silently.
- Logging uses the concrete class name as the logger name, so each watcher type
  gets its own log channel.

## Dependencies

Standard library only — `abc`, `logging`, `pathlib`, `time`. No third-party
packages required.
