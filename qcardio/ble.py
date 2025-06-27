# qcardio/ble.py

import asyncio
import subprocess
import re
from pathlib import Path
import yaml
from bleak import BleakScanner, BleakClient
from bleak.exc import BleakError

BASE_UUID_FMT = "0000{short}-0000-1000-8000-00805f9b34fb"

def _list_adapters() -> list[str]:
    try:
        out = subprocess.check_output(["hciconfig"], text=True)
        return re.findall(r"^(hci\d+):", out, flags=re.MULTILINE)
    except Exception:
        return []


def _load_standard_uuids() -> dict[str, str]:
    """
    Loads all .yaml files in resources/uuids,
    extracts data['uuids'], and builds a map full_uuid -> name.
    Handles entry['uuid'] as both int and str.
    """
    uuids: dict[str, str] = {}
    base_dir = Path(__file__).parent
    yaml_dir = base_dir / "resources" / "uuids"
    if not yaml_dir.is_dir():
        return uuids

    for yf in yaml_dir.glob("*.yaml"):
        data = yaml.safe_load(yf.read_text())
        for entry in data.get("uuids", []):
            raw = entry.get("uuid", "")
            # normalize to 4-digit hex string
            if isinstance(raw, int):
                short = f"{raw:04x}"
            else:
                s = str(raw)
                if s.lower().startswith("0x"):
                    # convert "0x2A00" to integer then to hex
                    short = f"{int(s, 16):04x}"
                else:
                    short = s.lower().zfill(4)
            full = BASE_UUID_FMT.format(short=short)
            name = entry.get("name", "").strip()
            if full and name:
                uuids[full] = name
    return uuids

# Load the map once
STANDARD_UUIDS = _load_standard_uuids()

def discover_device(address: str, adapter: str | None = None, timeout: float = 5.0):
    """
    Scans for a BLE device and prints services/characteristics,
    adding the standard description if the UUID is in the map.
    """
    async def _discover():
        nonlocal adapter

        # Adapter fallback
        valid = _list_adapters()
        if adapter and adapter not in valid:
            first = valid[0] if valid else None
            print(f"[WARN] Adapter '{adapter}' not found. Using '{first}'.")
            adapter = first

        # Find device
        try:
            device = await BleakScanner.find_device_by_address(
                address, timeout=timeout, adapter=adapter
            )
        except BleakError:
            device = await BleakScanner.find_device_by_address(
                address, timeout=timeout
            )

        if not device:
            print(f"[FAIL] Device {address} not found")
            return

        # Connect & print
        conn_args = {"adapter": adapter} if adapter else {}
        try:
            async with BleakClient(device, **conn_args) as client:
                used = adapter or "default"
                print(f"[OK] Connected to {address} ({used})\n")
                for svc in client.services:
                    su = svc.uuid.lower()
                    sname = STANDARD_UUIDS.get(su)
                    svc_label = su + (f" ({sname})" if sname else "")
                    print(f"Service {svc_label}:")
                    for char in svc.characteristics:
                        cu = char.uuid.lower()
                        cname = STANDARD_UUIDS.get(cu)
                        clabel = cu
                        props = ",".join(char.properties)
                        desc = f" ({cname})" if cname else ""
                        spacer = " " + "." * (30 - len(props) - 2) + " "
                        print(f"  └─ {clabel}  [{props}]{spacer}{desc}")
                print()
        except BleakError as e:
            print(f"[FAIL] BLE connection error: {e}")

    asyncio.run(_discover())

# qcardio/ble.py  (add below discover_device)

def read_characteristic(address: str,
                        adapter: str | None,
                        uuid: str,
                        timeout: float = 5.0) -> bytes:
    """
    Performs a BLE read of a single characteristic and returns the raw bytes.
    Uses the same adapter fallback mechanism as discover_device.
    """
    import asyncio
    from bleak import BleakScanner, BleakClient
    from bleak.exc import BleakError

    async def _read():
        nonlocal adapter
        # adapter fallback if invalid
        valid = _list_adapters()
        if adapter and adapter not in valid:
            adapter = valid[0] if valid else None

        # find device
        try:
            device = await BleakScanner.find_device_by_address(
                address, timeout=timeout, adapter=adapter
            )
        except BleakError:
            device = await BleakScanner.find_device_by_address(
                address, timeout=timeout
            )
        if not device:
            raise BleakError(f"Device {address} not found")

        # connect and read
        conn_args = {"adapter": adapter} if adapter else {}
        async with BleakClient(device, **conn_args) as client:
            return await client.read_gatt_char(uuid)

    return asyncio.run(_read())
