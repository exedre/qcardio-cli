# qcardio/commands/arm.py

import os
import enum
import asyncio
import datetime
import sys
import logging
from bleak import BleakClient
from bleak.exc import BleakError
from .base import PluginBase

logger = logging.getLogger("qcardio.arm")

# UUID constants
BP_SERVICE_UUID = "00001810-0000-1000-8000-00805f9b34fb"
BP_MEAS_UUID    = "00002a35-0000-1000-8000-00805f9b34fb"
BP_FEATURE_UUID = "00002a49-0000-1000-8000-00805f9b34fb"
CONTROL_UUID    = "583cb5b3-875d-40ed-9098-c39eb0c1983d"
BATT_UUID       = "00002a19-0000-1000-8000-00805f9b34fb"

# Device Info Service
DI_MANUF_UUID = "00002a29-0000-1000-8000-00805f9b34fb"
DI_MODEL_UUID = "00002a24-0000-1000-8000-00805f9b34fb"
DI_SERIAL_UUID = "00002a25-0000-1000-8000-00805f9b34fb"
DI_FWREV_UUID = "00002a26-0000-1000-8000-00805f9b34fb"
DI_SWREV_UUID = "00002a28-0000-1000-8000-00805f9b34fb"
DI_HWREV_UUID = "00002a27-0000-1000-8000-00805f9b34fb"
DI_SYSTEMID_UUID = "00002a23-0000-1000-8000-00805f9b34fb"
DI_PNPID_UUID = "00002a50-0000-1000-8000-00805f9b34fb"

class QardioConnectionError(RuntimeError):
    """BLE connection error."""

class QardioMeasurementAborted(RuntimeError):
    """Measurement aborted by device."""

class Phase(enum.Enum):
    INFLATING  = 0x01
    MEASURING  = 0x02
    DEFLATING  = 0x03
    COMPLETED  = 0x04
    ABORTED    = 0x05
    ERROR      = 0x06

def parse_sfloat(raw: bytes) -> float:
    """Decode IEEE-11073 SFLOAT (16-bit) into a float."""
    val = raw[0] | (raw[1] << 8)
    mantissa = val & 0x0FFF
    if mantissa >= 0x0800:
        mantissa -= 0x1000
    exponent = (val >> 12) & 0x000F
    if exponent >= 0x0008:
        exponent -= 0x0010
    return mantissa * (10 ** exponent)

def parse_bp_notification(data: bytearray) -> dict:
    """Parse raw Blood Pressure Measurement notification."""
    flags = data[0]
    offset = 1
    unit = "kPa" if (flags & 0x01) else "mmHg"
    systolic  = parse_sfloat(data[offset:offset+2]);  offset += 2
    diastolic = parse_sfloat(data[offset:offset+2]);  offset += 2
    map_val   = parse_sfloat(data[offset:offset+2]);  offset += 2
    pulse = None
    if flags & 0x04:
        pulse = parse_sfloat(data[offset:offset+2]); offset += 2
    status = None
    if flags & 0x10:
        status = int.from_bytes(data[offset:offset+2], 'little')
        offset += 2
    return {
        "flags": flags,
        "unit": unit,
        "systolic": systolic,
        "diastolic": diastolic,
        "map": map_val,
        "pulse": pulse,
        "status": status
    }

def decode_conditions(status: int) -> list[str]:
    """Decode measurement status bits into human‐readable list."""
    mapping = {
        0: "body_movement",
        1: "cuff_too_loose",
        2: "irregular_pulse",
        3: "pulse_rate_out_of_range",
    }
    return [name for bit, name in mapping.items() if status & (1 << bit)]

async def _measure_async(address: str, timeout: int, progress=None) -> dict:
    """Perform async measurement and return final data dict."""
    event = asyncio.Event()
    result = {}
    aborted = {"flag": False, "reason": ""}

    def _decode_vendor(raw: bytes):
        if len(raw)==2 and raw[0]==0xF2:
            return Phase(raw[1])
        return None

    def handle_measurement(_, data):
        m = parse_bp_notification(data)
        # implicit abort
        if len(data)>=5 and data[0]==0x04 and data[1]==0xFF:
            status = int.from_bytes(data[3:5], "little")
            aborted["flag"]   = True
            aborted["reason"] = "arm movement"
            if progress: progress({"type":"phase","phase":Phase.ABORTED})
            event.set()
            raise QardioMeasurementAborted("arm movement")
        if progress: progress({"type":"bp", **m})
        if m["flags"] & 0x10:
            result.update(m)
            event.set()

    def handle_control(_, data):
        phase = _decode_vendor(data)
        if not phase: return
        if progress: progress({"type":"phase","phase":phase})
        if phase in {Phase.COMPLETED}: event.set()
        if phase in {Phase.ABORTED, Phase.ERROR}:
            event.set()
            raise QardioMeasurementAborted(str(phase))

    try:
        async with BleakClient(address) as c:
            await c.start_notify(BP_MEAS_UUID, handle_measurement)
            await c.start_notify(CONTROL_UUID, handle_control)
            await c.write_gatt_char(CONTROL_UUID, bytes([0xF1,0x01]), response=True)
            await asyncio.wait_for(event.wait(), timeout=timeout)
    except (BleakError, asyncio.TimeoutError, OSError) as e:
        raise QardioConnectionError("cannot connect") from e

    if aborted["flag"]:
        raise QardioMeasurementAborted(aborted["reason"])
    return result

async def _read_char_async(address: str, uuid: str) -> bytes:
    """Generic async read of a single GATT characteristic."""
    try:
        async with BleakClient(address) as c:
            return await c.read_gatt_char(uuid)
    except BleakError as e:
        raise QardioConnectionError(f"read failed: {e}") from e

class Plugin(PluginBase):
    """QardioArm plugin: exposes measure, battery, info, features."""

    def __init__(self, cfg, state):
        super().__init__(cfg, state)
        self.address = cfg.address
        self.timeout = int(cfg.poll_interval or 60)

    def measure(self, progress=None) -> dict | None:
        """Perform blood pressure measurement."""
        try:
            m = asyncio.run(_measure_async(self.address, self.timeout, progress))
        except (QardioConnectionError, QardioMeasurementAborted) as e:
            print(f"[FAIL] {e}")
            return None

        battery = asyncio.run(_read_char_async(self.address, BATT_UUID))[0]
        conditions = decode_conditions(m.get("status") or 0)
        row = {
            "timestamp": datetime.datetime.now().isoformat(sep=" ", timespec="seconds"),
            **m,
            "battery": battery,
            "conditions": "|".join(conditions)
        }
        return row

    def get_battery(self) -> int:
        """Read battery percentage."""
        return asyncio.run(_read_char_async(self.address, BATT_UUID))[0]

    def get_device_info(self) -> dict:
        """Read Device Information Service fields."""
        info = {}
        for uuid in (
            DI_MANUF_UUID, DI_MODEL_UUID, DI_SERIAL_UUID,
            DI_FWREV_UUID, DI_SWREV_UUID, DI_HWREV_UUID,
            DI_SYSTEMID_UUID, DI_PNPID_UUID
        ):
            raw = asyncio.run(_read_char_async(self.address, uuid))
            try:
                info[uuid] = raw.decode().strip()
            except:
                info[uuid] = raw.hex()
        return info

    def get_features(self) -> dict:
        """Read Blood Pressure Feature bitmask & supported list."""
        raw = asyncio.run(_read_char_async(self.address, BP_FEATURE_UUID))
        mask = int.from_bytes(raw, 'little')
        features_map = {
            0:"Body Movement Detection",1:"Cuff Fit Detection",
            2:"Irregular Pulse Detection",3:"Pulse Rate Range Detection",
            4:"Measurement Position Detection"
        }
        supported = [name for bit,name in features_map.items() if mask&(1<<bit)]
        return {"bitmask": mask, "supported": supported}
