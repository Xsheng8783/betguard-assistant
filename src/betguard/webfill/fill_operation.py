"""At-most-once dispatch guard for the existing assisted-fill button."""
import hashlib
import json
import threading
import uuid
from pathlib import Path

_active = threading.Lock()


class FillOperation:
    def __init__(self, data: dict, directory: Path):
        identity = [data.get("manual_candidate_id"), data.get("queue_path"), data.get("item_index")]
        if data.get("refill"):
            # A retry is never implicit. The existing Refill button creates a
            # fresh operation ID; retransmission keeps that same ID.
            identity.append(str(uuid.UUID(str(data.get("operation_id", "")))))
        self.key = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
        self.directory = directory
        self.acquired = False

    def __enter__(self):
        if not _active.acquire(blocking=False):
            raise ValueError("另一筆正在填入，請先核對結果；不會排隊重試。")
        self.acquired = True
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with (self.directory / (self.key + ".json")).open("x", encoding="utf-8") as stream:
                json.dump({"state": "dispatched_or_unknown", "auto_retry": False}, stream)
        except FileExistsError:
            self.__exit__(None, None, None)
            raise ValueError("這次填入已執行或結果未明，未重複填入。請核對表單後才使用重新填入。")
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *args):
        if self.acquired:
            self.acquired = False
            _active.release()
