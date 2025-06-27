import json, os
class StateService:
    def __init__(self, cfg):
        self.path = os.path.expanduser("~/.config/qcardio/state.json")
        self.state = self._load()
        self.data = {}
        
    def _load(self):
        if os.path.exists(self.path):
            return json.load(open(self.path))
        return {}

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        json.dump(self.state, open(self.path, 'w'), indent=2)
