import threading

from bareloop.settings import WORKDIR
import  json
from datetime import datetime
from uuid import uuid4
from typing import Any

TRACE_DIR = WORKDIR / '.bareloop' / '.trace'
trace_lock = threading.Lock()

class TraceWriter:

    def __init__(self):
        TRACE_DIR.mkdir(parents=True, exist_ok=True)

        self.trace_id = uuid4().hex
        self.sequence = 0
        self.lock = threading.Lock()

        created_at = datetime.now().astimezone()
        filename = f"{created_at:%Y%m%d_%H%M%S}_{self.trace_id}.jsonl"
        self.path = TRACE_DIR / filename


    def write(self, event_type: str, **data: Any) -> None:
        """追加一条记录，data 由调用方自由决定。"""

        try:
            with self.lock:
                self.sequence += 1

                record = {
                    "trace_id": self.trace_id,
                    "sequence": self.sequence,
                    "time": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                    "type": event_type,
                    "data": data,
                }

                with self.path.open("a", encoding="utf-8") as file:
                    file.write(
                        json.dumps(record, ensure_ascii=False, default=str) + "\n"
                    )
        except Exception as error:
            print(f"日志写入失败: {error}")



