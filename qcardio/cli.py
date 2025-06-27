
#!/usr/bin/env python3
import argparse
import cmd
import importlib
import os
import threading
import time
import sys
import asyncio
import itertools
import shlex

from bleak import BleakScanner
from bleak.exc import BleakError

from qcardio.services.config import ConfigService
from qcardio.services.state import StateService
from qcardio.services.history import HistoryService
from qcardio.ble import discover_device, read_characteristic, STANDARD_UUIDS


class QardioShell(cmd.Cmd):
    intro = "Welcome to Qardio CLI plugin-based. Type 'help'."
    prompt = "qardio> "
    _spinner = itertools.cycle("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏")

    def __init__(self, device, address, adapter):
        super().__init__()
        self.cfg = ConfigService(device, address, adapter)
        self.state = StateService(self.cfg)
        self.hist = HistoryService()
        self.device = self._load_plugin(device)

        # verify that the BLE device is available
        self._verify_device()
        # start periodic keep-alive polling
        # self._start_keep_alive()

        # ensure "_" slot is initialized
        self.state.data["_"] = {}
        self.state.save()

        
    def _load_plugin(self, name):
        """Dynamically load the plugin for the given device."""
        mod = importlib.import_module(f"qcardio.commands.{name}")
        self._Phase = getattr(mod, "Phase", None)
        return mod.Plugin(self.cfg, self.state)

    def _progress_print(self, info: dict):
        """
        Callback to display measurement progress.
        Expects info with 'type': 'bp' or 'phase'.
        """
        # record every progress callback in state["_"]["progress"]
        if info.get("type") == "bp":
            sys.stdout.write(
                "\r%s Measuring: %.0f/%.0f %s "
                % (
                    next(self._spinner),
                    info.get("systolic", 0),
                    info.get("diastolic", 0),
                    info.get("unit", ""),
                )
            )
        elif info.get("type") == "phase" and self._Phase:
            phase = info.get("phase")
            msgs = {
                self._Phase.INFLATING: "Inflating...",
                self._Phase.MEASURING: "Measuring...",
                self._Phase.DEFLATING: "Deflating...",
                self._Phase.COMPLETED: "Completed",
                self._Phase.ABORTED: "Aborted",
                self._Phase.ERROR: "Error",
            }
            msg = msgs.get(phase, str(phase))
            sys.stdout.write(f"\r{next(self._spinner)} {msg}")
        sys.stdout.flush()
        
    def _verify_device(self):
        """
        Ensure the BLE device is found before entering the shell.
        Exits if the device is not reachable.
        """
        try:
            dev = asyncio.run(
                BleakScanner.find_device_by_address(
                    self.cfg.address, timeout=5.0, adapter=self.cfg.adapter
                )
            )
        except BleakError:
            dev = None

        if not dev:
            print(f"[FAIL] Device {self.cfg.address} not reachable. Exiting.")
            sys.exit(1)

    def _start_keep_alive(self):
        """
        Start a background thread that periodically reads
        the battery level to keep the BLE connection alive.
        """
        interval = self.cfg.poll_interval
        thread = threading.Thread(
            target=self._keep_alive_loop,
            args=(interval,),
            daemon=True
        )
        thread.start()

    def _keep_alive_loop(self, interval: int):
        """Loop that performs periodic keep-alive reads."""
        while True:
            try:
                _ = self._get_battery()
            except Exception as e:
                print(f"[WARN] Keep-alive error: {e}")
            time.sleep(interval)

    def do_discover(self, arg):
        """
        discover     : scan BLE for current device and
                       print services and characteristics.
        """
        address = self.cfg.address
        adapter = self.cfg.adapter
        if not address:
            print("[FAIL] Please specify a BLE address via --address or config.")
            return
        discover_device(address, adapter)

    def do_info(self, arg):
        """
        info         : read and print Device Information characteristics:
                       Manufacturer, Model, FW/HW rev, System ID, SW rev,
                       PnP ID, Serial Number.
        """
        uuids = [
            "00002a29-0000-1000-8000-00805f9b34fb",  # Manufacturer Name String
            "00002a24-0000-1000-8000-00805f9b34fb",  # Model Number String
            "00002a26-0000-1000-8000-00805f9b34fb",  # Firmware Revision String
            "00002a27-0000-1000-8000-00805f9b34fb",  # Hardware Revision String
            "00002a23-0000-1000-8000-00805f9b34fb",  # System ID
            "00002a28-0000-1000-8000-00805f9b34fb",  # Software Revision String
            "00002a50-0000-1000-8000-00805f9b34fb",  # PnP ID
            "00002a25-0000-1000-8000-00805f9b34fb",  # Serial Number String
        ]
        address = self.cfg.address
        adapter = self.cfg.adapter
        if not address:
            print("[FAIL] Please specify a BLE address via --address or config.")
            return

        for cu in uuids:
            name = STANDARD_UUIDS.get(cu.lower(), "")
            prefix = f"└─ {cu}  [read]"
            try:
                raw = read_characteristic(address, adapter, cu)
                try:
                    val = raw.decode().strip()
                except Exception:
                    val = raw.hex()
                print(f"{prefix}  {val:<24}   ({name})")
            except Exception as e:
                print(f"{prefix}  [FAIL] Error: {e}   ({name})")

    def do_read(self, arg):
        "read <uuid>   : read the specified characteristic UUID"
        print(self.device.read(arg.strip()))

    def do_write(self, arg):
        "write <uuid> <val> : write a value to the specified characteristic UUID"
        u, v = arg.split(maxsplit=1)
        print(self.device.write(u, v))

    def do_measure(self, arg):
        """
        measure [<file>] : perform a measurement and optionally save results to <file>.
        """
        outfile = arg.strip() or None
        # reset "_" slot for this new measurement
        self.state.data["_"] = {"progress": []}
        self.state.save() 
        
        try:
            result = self.device.measure(progress=self._progress_print)
        except Exception as e:
            # record any exception that slipped through
            self.state.data["_"]["error"] = str(e)
            self.state.save()
            print(f"[FAIL] {e}")
            return
        
        if result is None:
            # aborted according to plugin logic
            self.state.data["_"]["error"] = "measurement aborted"
            self.state.save()
            return

        print("Measurement result:")
        for k, v in result.items():
            print(f"  {k}: {v}")

        # record final result
        self.state.data["_"]["measurement"] = result
        self.state.save()

        # optionally append to file
        if outfile:
            try:
                with open(outfile, "a") as f:
                    f.write(f"{result}\n")
                print("[OK] Saved to", outfile)
            except Exception as e:
                print(f"[FAIL] Failed to write to {outfile}: {e}")

    def _get_battery(self):
        """
        Internal helper to read the battery level.
        Returns percentage as integer.
        """
        address = self.cfg.address
        adapter = self.cfg.adapter
        if not address:
            print("[FAIL] BLE address not specified (--address or config)")
            return None

        try:
            raw = read_characteristic(
                address, adapter,
                "00002a19-0000-1000-8000-00805f9b34fb"
            )
            return raw[0] if raw else None
        except Exception as e:
            print(f"[FAIL] Error reading battery level: {e}")
            return None

    def do_battery(self, arg):
        "battery       : read the battery level percentage"
        level = self._get_battery()
        if level is not None:
            print(f"Battery level: {level}%")
        else:
            print("[FAIL] No data received")

    def do_print(self, arg):
        """
        print <name>  : print the dataset stored under <name> (or '_' if omitted)
        """
        name = arg.strip() or "_"
        ds = self.state.data

        if name not in ds:
            print(f"[FAIL] Dataset '{name}' not found")
            return

        data = ds[name]
        # Pretty‐print based on type
        if isinstance(data, list):
            for idx, item in enumerate(data, 1):
                print(f"{name}[{idx}]: {item}")
        elif isinstance(data, dict):
            for key, val in data.items():
                print(f"{key}: {val}")
        else:
            # fallback for other types
            print(f"{name}: {data}")
            
    def do_dataset(self, arg):
        """
        dataset <op> [...args] [--if <field>=<regexp>]

        Available operations:
          ls                   : list all named datasets
          bless <name>         : assign current '_' dataset to <name>
          rm <name>            : remove named dataset
          cp <src> <dest>      : copy dataset src to dest (with optional filter)
          mv <old> <new>       : rename dataset old to new
        """
        tokens = shlex.split(arg)
        filter_field = None
        filter_re = None
        # parse optional filter
        if "--if" in tokens:
            idx = tokens.index("--if")
            if idx + 1 < len(tokens):
                cond = tokens[idx + 1]
                tokens = tokens[:idx] + tokens[idx + 2:]
                if "=" in cond:
                    field, pattern = cond.split("=", 1)
                    filter_field = field
                    filter_re = re.compile(pattern)
        if not tokens:
            print("[FAIL] Missing operation")
            return

        op = tokens[0]
        ds = self.state.data  # shorthand

        if op == "ls":
            names = sorted(ds.keys())
            for name in names:
                print(name)

        elif op == "bless":
            if len(tokens) != 2:
                print("[FAIL] Usage: dataset bless <name>")
                return
            name = tokens[1]
            ds[name] = copy.deepcopy(ds.get("_"))
            print(f"[OK] Blessed '_' as '{name}'")
            self.state.save()

        elif op == "rm":
            if len(tokens) != 2:
                print("[FAIL] Usage: dataset rm <name>")
                return
            name = tokens[1]
            if name in ds:
                del ds[name]
                print(f"[OK] Removed dataset '{name}'")
                self.state.save()
            else:
                print(f"[FAIL] Dataset '{name}' not found")

        elif op == "cp":
            if len(tokens) != 3:
                print("[FAIL] Usage: dataset cp <src> <dest> [--if <field>=<regexp>]")
                return
            src, dest = tokens[1], tokens[2]
            if src not in ds:
                print(f"[FAIL] Source dataset '{src}' not found")
                return
            data = ds[src]
            # apply filter if src is a list and filter provided
            if isinstance(data, list) and filter_field and filter_re:
                filtered = [
                    item for item in data
                    if filter_field in item and filter_re.search(str(item[filter_field]))
                ]
                ds[dest] = filtered
            else:
                ds[dest] = copy.deepcopy(data)
            print(f"[OK] Copied dataset '{src}' to '{dest}'")
            self.state.save()

        elif op == "mv":
            if len(tokens) != 3:
                print("[FAIL] Usage: dataset mv <old> <new>")
                return
            old, new = tokens[1], tokens[2]
            if old not in ds:
                print(f"[FAIL] Dataset '{old}' not found")
                return
            ds[new] = ds.pop(old)
            print(f"[OK] Renamed dataset '{old}' to '{new}'")
            self.state.save()

        else:
            print(f"[FAIL] Unknown operation '{op}'")
            
    def do_exit(self, arg):
        "exit          : exit the CLI"
        return True

    def do_EOF(self, arg):
        "Ctrl-D        : exit the CLI"
        print()
        return True

    def postloop(self):
         """
         Called once after the command loop terminates.
         Save history and state before exiting.
         """
         # save state (measurements, etc.)
         try:
             self.state.save()
         except Exception as e:
             print(f"[WARN] Failed to save state: {e}")
         # save command history
         try:
             self.hist.save()
         except Exception as e:
             print(f"[WARN] Failed to save history: {e}")

def main():
    parser = argparse.ArgumentParser(prog="qardio")
    parser.add_argument("device", help="DEVICE plugin (arm, core, …)")
    parser.add_argument("--address", help="BLE address")
    parser.add_argument("--adapter", help="BLE adapter")
    args = parser.parse_args()
    shell = QardioShell(args.device, args.address, args.adapter)
    shell.cmdloop()


if __name__ == "__main__":
    main()
