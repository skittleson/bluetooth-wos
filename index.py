"""Entry into bluetooth wos"""

import os
import sys
import logging
from datetime import datetime, timedelta
import asyncio
import csv
import requests
from rich.progress import track
from rich.text import Text
from rich.live import Live
from rich.table import Table
from rich.console import Console
from ruamel.yaml import YAML
from ruamel.yaml.reader import Reader
import numpy as np
from scipy import stats
from bleak import BleakScanner, AdvertisementData, BLEDevice, BleakClient
from core import (
    device_distance_calculation,
    bytes_to_hex_string,
    bytes_to_int,
    bytes_to_string,
)

logging.basicConfig(filename="bluetooth-discovery.log", level=logging.DEBUG)


class BleScannerInteractive:
    """Interactive Bluetooth Scanner"""

    def __init__(self, redacted_address=False) -> None:
        self._logger = logging.getLogger(__name__)
        self._redacted_address = redacted_address
        self.ensure_bluetooth_public_information_is_saved()
        self._console = Console()
        self._company_dict = {}
        self._services_dict = {}
        self._devices_dict = {}
        self._devices_columns = {
            "index": "Index",
            "address": "Address",
            "name": "Name",
            "rssi": "RSSI",
            "tx_power": "TX Power",
            "services": "Services",
            "company": "Company",
            "distance": "Distance",
            "last_seen": "Last Seen",
            "first_seen": "First Seen",
        }
        self.__create_table()
        self._discovery_timeout = 10
        self._private_resolvable_random_address_timeout = 120
        self._signal_propagation_constant = 8

    def get_key_index(self, key: str, generic_dict: dict) -> int:
        """Get a key by index"""

        keys = list(generic_dict.keys())
        try:
            return keys.index(key)
        except ValueError:
            return -1  # Return -1 if key is not found

    def ensure_bluetooth_public_information_is_saved(self):
        """Download public bluetooth information"""

        def save(filename: str, url: str):
            if os.path.isfile(filename) is True:
                return

            self._logger.info("updating/creation %s from %s", filename, url)
            response = requests.get(url, timeout=5000)
            response.raise_for_status()
            with open(filename, "w", encoding="utf-8") as file:
                file.write(response.text.strip())
            self._logger.info("saved %s", filename)

        # TODO this should be saved in a config file
        save(
            "service_uuids.yaml",
            "https://bitbucket.org/bluetooth-SIG/public/raw/025ac280519f8ad3967f79ee45bd921a76003113/assigned_numbers/uuids/service_uuids.yaml",
        )
        save(
            "company_identifiers.yaml",
            "https://bitbucket.org/bluetooth-SIG/public/raw/025ac280519f8ad3967f79ee45bd921a76003113/assigned_numbers/company_identifiers/company_identifiers.yaml",
        )

    def __create_table(self) -> None:
        self._table = Table()
        for _, device_value in self._devices_columns.items():
            self._table.add_column(device_value)

    async def _query_device(self, device_index: int):
        mac_address = self._devices_dict[device_index]
        client = BleakClient(mac_address, timeout=60)
        try:
            self._console.log(f"Connecting to {mac_address}")
            await client.connect()
            self._console.log(f"Connected to {mac_address}")

            # Iterate through services and characteristics
            for service in client.services:
                self._console.log(
                    f"Service: {service.uuid} {service.description} {service.handle} {self.__get_entity_name(service.handle, 'service')}"
                )

                for characteristic in service.characteristics:
                    self._console.log(
                        f"  Characteristic: {characteristic.uuid}")
                    self._console.log(
                        f"    Properties: {characteristic.properties}")
                    for descriptor in characteristic.descriptors:
                        # descriptor_value = await client.read_gatt_char(descriptor.uuid)
                        self._console.log(
                            f"    Descriptor: {descriptor.uuid} Handle: {descriptor.handle}"
                        )
        # pylint: disable=W0703
        except Exception as exception:
            self._console.log(exception)
            self._logger.warning(exception)
        finally:
            await client.disconnect()

    def uuid_to_gatt_handle(self, uuid: str) -> int:
        """Get uuid to int"""

        handle_hex = uuid[4:8]
        handle_int = int(handle_hex, 16)
        return handle_int

    def __callback(self, device: BLEDevice, advertisement_data: AdvertisementData):
        service_count = (
            len(advertisement_data.service_data.items())
            if advertisement_data.service_data
            else 0
        )

        company = None
        if advertisement_data.manufacturer_data:
            for company_id, _ in advertisement_data.manufacturer_data.items():
                if company_id and company_id != 0:
                    company = self.__get_entity_name(
                        company_id, 'company') or str(company_id)
                    break

        # ensure that rssi and tx power are negative (since that is how it should work!)
        rssi = -abs(advertisement_data.rssi or device.rssi or 0)
        tx_power = -abs(advertisement_data.tx_power or 0)
        distance = device_distance_calculation(
            tx_power, rssi, self._signal_propagation_constant
        )
        last_seen = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # If device has already been seen, use the original first seen value from existing table.
        first_seen = last_seen
        if self._devices_dict.get(device.address, None) is not None:
            first_seen = self._devices_dict[device.address][
                self.get_key_index("first_seen", self._devices_columns)
            ]

        # Maintain an ongoing current status list
        self._devices_dict[device.address] = [
            str(0),
            str(device.address),
            str(device.name or "Unknown"),
            str(rssi),
            str(tx_power),
            str(service_count),
            str(company or ""),
            str(f"{distance:.2f}"),
            last_seen,
            first_seen,
        ]

        # all items in array become parameters
        # self._table.add_row(*self._devices_dict[device.address])

        # if advertisement_data.manufacturer_data:
        #     self._console.log("Manufacturer Data:")
        #     for company_id, data in advertisement_data.manufacturer_data.items():
        #         self._console.log(f"  Manufacturer ID: {company_id}")
        #     self._console.log(
        #         f"  Hex Data: {BleScannerInteractive.bytes_to_hex_string(data)}")
        #     self._console.log(
        #         f"  Integer Data: {BleScannerInteractive.bytes_to_int(data)}")
        #     self._console.log(
        #         f"  String Data: {BleScannerInteractive.bytes_to_string(data)}")
        # if advertisement_data.service_uuids:
        #     service_name = None
        #     for service_uuid in advertisement_data.service_uuids:
        #         service_name = self.__get_service_name(self.uuid_to_gatt_handle(service_uuid))
        #         self._console.log(f'{service_name} {service_uuid}')
        #     if service_name == 'Battery':
        #         for service_uuid, data in advertisement_data.service_data.items():
        #             print(
        #                 f"  Hex Data: {bytes_to_hex_string(data)}")
        #             print(
        #                 f"  Integer Data: {bytes_to_int(data)}")
        #             print(
        #                 f"  String Data: {bytes_to_string(data)}")

        # if advertisement_data.service_data:
        #     print("Service Data:")
        #     for service_uuid, data in advertisement_data.service_data.items():
        #         print(f"  Service UUID: {service_uuid} {data}")
        #         print(
        #             f"  Hex Data: {bytes_to_hex_string(data)}")
        #         print(
        #             f"  Integer Data: {bytes_to_int(data)}")
        #         print(
        #             f"  String Data: {bytes_to_string(data)}")

    @staticmethod
    def strip_invalid(s):
        """strip invalid yaml markup"""

        res = ""
        for x in s:
            if Reader.NON_PRINTABLE.match(x):
                continue
            res += x
        return res

    def __get_entity_name(self, value: int, entity_type: str) -> str | None:
        """
        Get a name for a given value and entity type (service or company).

        Args:
            value: The integer value to look up
            entity_type: Type of entity to search for ("service" or "company")

        Returns:
            The name of the entity if found, None otherwise
        """

        entity_attributes = {
            "service": {
                "dict_attr": "_services_dict",
                "file_name": "service_uuids.yaml",
                "root_key": "uuids",
                "value_key": "uuid",
            },
            "company": {
                "dict_attr": "_company_dict",
                "file_name": "company_identifiers.yaml",
                "root_key": "company_identifiers",
                "value_key": "value",
            }
        }

        # Check if the requested entity type is supported
        if entity_type not in entity_attributes:
            raise ValueError(f"Unknown entity type: {entity_type}")

        attrs = entity_attributes[entity_type]
        dict_attr = attrs["dict_attr"]

        # Load data if the dictionary is empty
        if len(getattr(self, dict_attr, {})) == 0:
            with open(attrs["file_name"], "r", encoding="utf-8") as file:
                yaml = YAML(typ="safe")
                setattr(
                    self,
                    dict_attr,
                    yaml.load(BleScannerInteractive.strip_invalid(file.read()))
                )

        # Search for the entity
        entities_dict = getattr(self, dict_attr)
        for entity in entities_dict[attrs["root_key"]]:
            if entity[attrs["value_key"]] == value:
                return entity["name"]

        return None

    def __write_current_device_list_csv(self):
        try:
            with open("devices.csv", mode="w", newline="", encoding="utf8") as file:
                writer = csv.writer(file)
                writer.writerow(self._devices_columns.keys())
                for _, values in self._devices_dict.items():
                    writer.writerow([*values])
        except Exception as e:
            self._logger.warning(e)

    async def __loading(self):
        for _ in track(range(self._discovery_timeout + 1), description="Scanning..."):
            await asyncio.sleep(1)

    async def __discover_with_data(self):

        # Combat "private resolvable random addresses" from filling up the screen.
        time_threshold = datetime.now() - timedelta(
            seconds=self._private_resolvable_random_address_timeout
        )
        keys_to_remove = []
        for device, value in self._devices_dict.items():
            last_seen = datetime.strptime(
                value[self.get_key_index("last_seen", self._devices_columns)],
                "%Y-%m-%d %H:%M:%S",
            )
            if last_seen < time_threshold:
                keys_to_remove.append(device)

        # Remove devices after expire
        for device in keys_to_remove:
            del self._devices_dict[device]
            self._logger.info("removed %s", device)

        self._logger.info("bluetooth discovered devices started")
        devices_data = await BleakScanner.discover(
            timeout=self._discovery_timeout, return_adv=True
        )
        self._logger.info("bluetooth discovered devices ended")
        for device_key in devices_data:
            device, adv = devices_data[device_key]
            self.__callback(device, adv)

        self.calculate_missing_distances_regression()

        # update all the missing ones in the table
        for index, (device_key, device_data) in enumerate(self._devices_dict.items()):
            rendered_device_data = []
            device_data[0] = str(index)
            for index_device_data, text_device_data in enumerate(device_data):
                text = str(text_device_data)

                # redact addresses
                if (
                    self._redacted_address is True
                    and index_device_data
                    == self.get_key_index("address", self._devices_columns)
                ):
                    text = text[:3] + "." * (len(text) - 3)
                rendered_device_data.append(Text(text=text))

            # tx power or services want to be found. highlight them
            style = ""
            if (abs(int(device_data[self.get_key_index("tx_power", self._devices_columns)])) > 0
                or abs(
                    int(
                        device_data[
                            self.get_key_index(
                                "services", self._devices_columns)
                        ]
                    )
            )
                > 0
            ):
                style = "green"
            self._table.add_row(*rendered_device_data, style=style)

    def calculate_missing_distances_regression(self):
        
        
        # Step 1: Gather data points from devices with complete information
        rssi_values = []
        distance_values = []
        
        for device in self._devices_dict.values():
             if abs(int(device[self.get_key_index("rssi", self._devices_columns)])) > 0 and \
                    abs(int(device[self.get_key_index("tx_power", self._devices_columns)])) > 0 and \
                    float(device[self.get_key_index("distance", self._devices_columns)]) > 0:
                rssi_values.append(abs(int(device[self.get_key_index("rssi", self._devices_columns)])))
                distance_values.append(float(device[self.get_key_index("distance", self._devices_columns)]))
        
        if len(rssi_values) < 3:  # Need sufficient data points
            return
        
        # Step 2: Build a log-based model (since RSSI vs distance typically follows logarithmic relationship)
        # Log-distance path loss model: distance = 10^((Tx_power - RSSI)/(10*n))
        # where n is the path loss exponent
        
        # Since we don't have TX power, we'll use a simple regression on log of distance
        log_distances = np.log10(distance_values)
        slope, intercept, r_value, p_value, std_err = stats.linregress(rssi_values, log_distances)
        
        # Step 3: Apply the model to devices without distance
        for device_id, device in self._devices_dict.items():
            if abs(int(device[self.get_key_index("tx_power", self._devices_columns)])) == 0:
                rssi = abs(int(device[self.get_key_index("rssi", self._devices_columns)]))
                log_distance = slope * rssi + intercept
                device[self.get_key_index("distance", self._devices_columns)] = "{:.2f}".format(10 ** log_distance)

    def calculate_missing_distances(self):
        """Calculates the missing distance value given information about other devices"""

        # Step 1: Gather data points from devices with complete information
        known_data = []
        for device in self._devices_dict.values():
            if abs(int(device[self.get_key_index("rssi", self._devices_columns)])) > 0 and \
                    abs(int(device[self.get_key_index("tx_power", self._devices_columns)])) > 0 and \
                    float(device[self.get_key_index("distance", self._devices_columns)]) > 0:
                known_data.append({
                    'rssi': abs(int(device[self.get_key_index("rssi", self._devices_columns)])),
                    'distance': float(device[self.get_key_index("distance", self._devices_columns)])
                })

        if not known_data:
            return  # Not enough information to make estimates

        # Step 2: Create a simple model relating RSSI to distance - Simple averaging by RSSI value
        rssi_to_distance = {}
        rssi_counts = {}
        for point in known_data:
            rssi = point['rssi']
            distance = point['distance']

            if rssi not in rssi_to_distance:
                rssi_to_distance[rssi] = 0
                rssi_counts[rssi] = 0

            rssi_to_distance[rssi] += distance
            rssi_counts[rssi] += 1

        # Calculate average distance for each RSSI value
        for rssi in rssi_to_distance:
            rssi_to_distance[rssi] /= rssi_counts[rssi]

        # Step 3: Estimate distances for devices missing TX Power then update them
        for device_id, device in self._devices_dict.items():
            if abs(int(device[self.get_key_index("tx_power", self._devices_columns)])) == 0:
                rssi = abs(
                    int(device[self.get_key_index("rssi", self._devices_columns)]))

                # Find the closest known RSSI if exact match not available
                if rssi in rssi_to_distance:
                    device[self.get_key_index("distance", self._devices_columns)] = "{:.2f}".format(
                        rssi_to_distance[rssi])
                else:
                    # Find closest RSSI value
                    closest_rssi = min(rssi_to_distance.keys(),
                                       key=lambda x: abs(x - rssi))
                    device[self.get_key_index("distance", self._devices_columns)] = "{:.2f}".format(
                        rssi_to_distance[closest_rssi])

    async def run_async(self):
        """async method to start app"""

        # Dummy loading screening while devices are being discovered
        await asyncio.gather(self.__discover_with_data(), self.__loading())
        self._console.clear()

        # Keep a live table going
        with Live(self._table, console=self._console) as live:
            live.update(self._table)
            while True:
                self.__create_table()
                await self.__discover_with_data()
                live.update(self._table)
                self.__write_current_device_list_csv()

    def run(self):
        """sync method to start app"""

        try:
            asyncio.run(self.run_async())
        except KeyboardInterrupt:
            self._console.print("Bye bye")
        sys.exit(0)

        # while True:
        #     device_index = int(self._console.input(
        #         "Pick an index to connect:"))
        #     await self._query_device(device_index)


if __name__ == "__main__":
    scanner = BleScannerInteractive(redacted_address=True)
    scanner.run()
