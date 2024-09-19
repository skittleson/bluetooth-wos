import math


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
