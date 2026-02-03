
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


class Whomst(Enum):
    DISCORD_BOT = "DISCORD_BOT"
    POLYMARKET_FOLLOWER = "POLYMARKET_FOLLOWER"


class Logger:
    def __init__(self, whomst: Whomst):
        self.whomst = whomst.value

    def log(self, msg: str, log_type: LogType = LogType.INFO):
        date_and_time = datetime.now().strftime("%d-%B-%Y-%H:%M:%S").lower()

        log_dir = Path(__file__).parent.parent.parent / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)

        with open(log_dir / f"{self.whomst.lower()}.log", "a") as f:
            f.write(f"[{date_and_time}] [{log_type.value}] {self.whomst}: {msg}\n")

        color = getattr(LogColors, log_type.name, LogColors.RESET)
        print(f"[{date_and_time}] {color}[{log_type.value}]{LogColors.RESET} {self.whomst}: {msg}")