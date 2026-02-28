import math
from typing import Callable, Optional


# ---------------------------------------------------------------------------
# Standard Bluetooth SIG GATT sensor characteristics
# ---------------------------------------------------------------------------

# Maps full 128-bit UUID → {"label": str, "decode": bytes -> str}
# Covers any device that implements the standard Environmental Sensing profile.
#   0x2A6E  Temperature         sint16, x0.01 °C
#   0x2A6F  Humidity            uint16, x0.01 %RH
#   0x2A19  Battery Level       uint8,  0–100 %
#   0x2A1C  Temperature Measurement (Health Thermometer profile, flags + sint32)
SENSOR_CHARACTERISTICS: dict[str, dict[str, str | Callable[[bytes], str]]] = {
    "00002a6e-0000-1000-8000-00805f9b34fb": {
        "label": "Temperature",
        "decode": lambda b: f"{int.from_bytes(b[:2], 'little', signed=True) / 100:.2f} °C",
    },
    "00002a6f-0000-1000-8000-00805f9b34fb": {
        "label": "Humidity",
        "decode": lambda b: f"{int.from_bytes(b[:2], 'little', signed=False) / 100:.2f} %RH",
    },
    "00002a19-0000-1000-8000-00805f9b34fb": {
        "label": "Battery Level",
        "decode": lambda b: f"{b[0]}%",
    },
    "00002a1c-0000-1000-8000-00805f9b34fb": {
        "label": "Temperature Measurement",
        # Flags byte: bit0 = Fahrenheit, bits 1-2 = timestamp/type present
        # Value is sint32 at bytes [1:5], unit depends on flags bit0
        "decode": lambda b: (
            f"{int.from_bytes(b[1:5], 'little', signed=True) / 100:.2f} "
            f"{'°F' if b[0] & 0x01 else '°C'}"
        ) if len(b) >= 5 else f"(raw: {b.hex()})",
    },
}


# ---------------------------------------------------------------------------
# Advertisement sensor decoders
# ---------------------------------------------------------------------------

# ATC MiThermometer — Environmental Sensing Service UUID used by both
# ATC1441 original firmware and PVVX custom firmware
_ATC_ESS_UUID = "0000181a-0000-1000-8000-00805f9b34fb"


def decode_advertisement_sensors(service_data: dict) -> Optional[dict]:
    """Decode sensor readings embedded in a BLE advertisement payload.

    Currently supports the ATC MiThermometer PVVX custom firmware format
    (https://github.com/pvvx/ATC_MiThermometer), which broadcasts sensor
    readings passively in service_data under UUID 0x181A.

    PVVX payload layout (19 bytes, little-endian):
        [0:6]   MAC address (ignored)
        [6:8]   int16   temperature  x0.01 °C
        [8:10]  uint16  humidity     x0.01 %RH
        [10:12] uint16  battery_mv   millivolts
        [12]    uint8   battery_level 0–100 %
        [13]    uint8   counter      measurement count
        [14]    uint8   flags

    Args:
        service_data: The AdvertisementData.service_data dict from bleak,
                      mapping UUID strings to bytes payloads.

    Returns:
        A dict with decoded sensor values, or None if no known format matched.
    """
    if not service_data:
        return None

    # ATC PVVX / ATC1441 — both use UUID 0x181A; distinguish by length
    payload: Optional[bytes] = None
    for uuid, data in service_data.items():
        if uuid.lower() == _ATC_ESS_UUID:
            payload = data
            break

    if payload is not None and len(payload) >= 15:
        # PVVX format (≥15 bytes): MAC[6] + temp[2] + hum[2] + mv[2] + lvl + cnt + flags
        temperature = int.from_bytes(payload[6:8], byteorder="little", signed=True) / 100.0
        humidity = int.from_bytes(payload[8:10], byteorder="little", signed=False) / 100.0
        battery_mv = int.from_bytes(payload[10:12], byteorder="little", signed=False)
        battery_level = payload[12]
        counter = payload[13]
        flags = payload[14]
        return {
            "source": "ATC PVVX advertisement",
            "temperature": temperature,
            "humidity": humidity,
            "battery_mv": battery_mv,
            "battery_level": battery_level,
            "counter": counter,
            "flags": flags,
        }

    return None


_EDDYSTONE_UUID = "0000feaa-0000-1000-8000-00805f9b34fb"

# Eddystone frame type byte (first byte of service_data payload)
_EDDYSTONE_FRAME_TYPES = {
    0x00: "UID",
    0x10: "URL",
    0x20: "TLM",
    0x30: "EID",
}


def detect_beacon_type(
    manufacturer_data: dict,
    service_data: dict,
) -> str | None:
    """Detect common BLE beacon types from advertisement data.

    Returns a short label string if a known beacon format is detected,
    or None if the device does not appear to be a beacon.

    Supported formats:
      - iBeacon  (Apple manufacturer_data 0x004C, subtype 0x02 0x15)
      - Eddystone-UID / URL / TLM / EID  (service UUID 0xFEAA)
      - AltBeacon  (manufacturer_data with 0xBEAC magic at bytes 0-1)
    """
    # --- iBeacon ---
    if manufacturer_data:
        apple_data = manufacturer_data.get(0x004C)
        if apple_data and len(apple_data) >= 2 and apple_data[0] == 0x02 and apple_data[1] == 0x15:
            return "iBeacon"

        # --- AltBeacon ---
        for _company_id, payload in manufacturer_data.items():
            if len(payload) >= 2 and payload[0] == 0xBE and payload[1] == 0xAC:
                return "AltBeacon"

    # --- Eddystone ---
    if service_data:
        for uuid, payload in service_data.items():
            if uuid.lower() == _EDDYSTONE_UUID and payload:
                frame_type = payload[0] & 0xF0
                subtype = _EDDYSTONE_FRAME_TYPES.get(frame_type, "")
                return f"Eddystone{'-' + subtype if subtype else ''}"

    return None


def decode_unknown_char(data: bytes | bytearray) -> str:
    """Attempt several common decodings of raw characteristic bytes.

    Returns a multi-part string showing:
      - hex dump
      - UTF-8 string (if decodable)
      - uint8/16/32 little-endian integers (where length allows)
    """
    if not data:
        return "(empty)"
    parts: list[str] = [f"hex: {data.hex(' ')}"]
    # UTF-8
    try:
        text = data.decode("utf-8")
        if text.isprintable():
            parts.append(f'str: "{text}"')
    except (UnicodeDecodeError, ValueError):
        pass
    # integers (little-endian)
    if len(data) >= 1:
        parts.append(f"u8: {data[0]}")
    if len(data) >= 2:
        parts.append(f"u16le: {int.from_bytes(data[:2], 'little')}")
    if len(data) >= 4:
        parts.append(f"u32le: {int.from_bytes(data[:4], 'little')}")
    return "  |  ".join(parts)


@staticmethod
def bytes_to_hex_string(data):
    """Convert byte data to a string of hexadecimal values."""
    return ' '.join(f'{byte:02x}' for byte in data)


@staticmethod
def bytes_to_int(data):
    """Convert byte data to an integer."""
    return int.from_bytes(data, byteorder='little')


@staticmethod
def bytes_to_string(data):
    """Attempt to decode byte data as a UTF-8 string."""
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        return "Non-UTF8 data"


@staticmethod
def device_distance_calculation(tx_power: int, rssi: int, signal_propagation_constant: int) -> float:
    """
    Calculates the estimated distance between a device and a signal source based on signal strength.

    This method estimates the distance using the received signal strength indicator (RSSI), the 
    transmitted signal strength (Tx Power), and the signal propagation constant. The formula is based 
    on the logarithmic path loss model used in wireless communication to estimate distance.

    Parameters:
    -----------
    tx_power : int
        The transmit power of the device in dBm (decibels relative to one milliwatt). This value is typically 
        provided by the transmitting device and represents the signal strength at a distance of 1 meter.
    rssi : int
        The received signal strength indicator, in dBm, measured by the receiving device. This is typically 
        a negative value, where a lower (more negative) RSSI indicates a weaker signal.
    signal_propagation_constant : int
        The signal propagation constant (or path loss exponent) that represents the rate at which signal strength 
        diminishes with distance. Common values are:
            - 2: Open space or free-space environment (e.g., outdoor line-of-sight)
            - 3: Indoor environment with light obstacles
            - 4+: Heavily obstructed environment (e.g., walls, buildings)

    Returns:
    --------
    float
        The estimated distance between the transmitter and receiver, in meters.

    Example:
    --------
    >>> Device.device_distance_calculation(tx_power=-50, rssi=-70, signal_propagation_constant=2)
    3.1622776601683795

    Formula:
    ------------
    distance = 10^((tx_power - rssi) / (10 * signal_propagation_constant))

    Notes:
    ------
    - The accuracy of this calculation depends on the environment and assumptions made about signal propagation.
    - Environmental factors such as obstacles, interference, and reflections can impact the actual distance.
    - See https://stackoverflow.com/a/24245724
    """

    tx_power = -abs(tx_power)
    rssi = -abs(rssi)
    distance: float = 0.0
    if abs(rssi) > 0 and abs(tx_power) > 0:
        distance = math.pow(
            10.0, (tx_power - rssi) / (10 * signal_propagation_constant))
    return distance


@staticmethod
def device_distance_by_rssi_only(rssi, p0=-50, n=2):
    """
    Estimate the distance based on RSSI using a logarithmic path loss model.

    Args:
        rssi (float): The RSSI value in dBm.
        p0 (float): The RSSI value at 1 meter (reference distance). Default is -50 dBm.
        n (float): The path loss exponent. Default is 2 (free space).

    Returns:
        float: Estimated distance in meters.

    Example:
        >>> rssi_value = -70
        >>> distance = calculate_distance(rssi_value)
        >>> print(f"Estimated Distance: {distance:.2f} meters")
        Estimated Distance: 31.62 meters
    """
    distance = 10 ** ((p0 - rssi) / (10 * n))
    return distance
