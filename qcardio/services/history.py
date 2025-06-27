import os, readline
class HistoryService:
    def __init__(self):
        self.path = os.path.expanduser("~/.config/qcardio/history")
        try: readline.read_history_file(self.path)
        except: pass

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        readline.write_history_file(self.path)
