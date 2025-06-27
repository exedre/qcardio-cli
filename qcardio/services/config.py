import os
import configparser

class ConfigService:
    def __init__(self, device: str, address: str | None, adapter: str | None):
        cfg = configparser.ConfigParser()

        # 1) look for ./qcardio.conf
        local_path = os.path.join(os.getcwd(), "qcardio.conf")
        # 2) otherwise, ~/.config/qcardio/qcardio.conf
        user_path = os.path.expanduser("~/.config/qcardio/qcardio.conf")

        if os.path.exists(local_path):
            cfg.read(local_path)
            print(f"[INFO] Using local configuration: {local_path}")
        elif os.path.exists(user_path):
            cfg.read(user_path)
            print(f"[INFO] Using user configuration: {user_path}")
        # if neither exists, cfg remains empty

        self.device = device
        # CLI argument takes precedence; otherwise use config file
        self.address = address or cfg.get(device, "address", fallback=None)
        self.adapter = adapter or cfg.get(device, "adapter", fallback=None)
        # polling interval in seconds
        self.poll_interval = int(
            cfg.get(device, "poll_interval", fallback="60")
        )
