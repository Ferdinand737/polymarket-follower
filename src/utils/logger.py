
from enum import Enum
from datetime import datetime
from pathlib import Path
from datetime import datetime


class LogType(Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class LogColors:
    INFO = "\033[92m"
    WARNING = "\033[93m"
    ERROR = "\033[91m"  
    RESET = "\033[0m"



class Logger:

    def __init__(self, clear: bool = False):
        log_dir = Path(__file__).parent.parent.parent / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = log_dir / "polymarket_follower.log"
        # Never clear logs automatically - this can happen when a duplicate
        # process starts and wipes out evidence of the first process's work
        # if clear:
        #     if self.log_file.exists():
        #         self.log_file.unlink()
        #     self.log_file.touch()

    def log(self, msg: str, log_type: LogType = LogType.INFO):
        date_and_time = datetime.now().strftime("%d-%B-%Y-%H:%M:%S").lower()

        with open(self.log_file, "a") as f:
            f.write(f"[{date_and_time}] [{log_type.value}]: {msg}\n")

        color = getattr(LogColors, log_type.name, LogColors.RESET)
        print(f"[{date_and_time}] {color}[{log_type.value}]{LogColors.RESET}: {msg}")