"""Entry into bluetooth wos"""

import asyncio
import os
import csv
from datetime import datetime, timedelta

import requests
from ruamel.yaml import YAML
from ruamel.yaml.reader import Reader
import numpy as np
from scipy import stats
from bleak import BleakScanner, BleakClient
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import (
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    ProgressBar,
    RichLog,
    Static,
)

from core import (
    decode_advertisement_sensors,
    decode_unknown_char,
    detect_beacon_type,
    device_distance_calculation,
    SENSOR_CHARACTERISTICS,
)




# ---------------------------------------------------------------------------
# Modal screen: device service/characteristic inspector
# ---------------------------------------------------------------------------


class DeviceDetailScreen(ModalScreen):
    """Modal that connects to a BLE device and shows its GATT tree.

    Behaviour
    ---------
    * If a cached GATT result (from the background bulk inspector) already
      exists for this device it is rendered immediately before connecting.
    * All notify/indicate subscriptions are kept **live** for the lifetime of
      the modal.  Each time the device pushes a new value the log is updated
      in real time with a ``[live]`` marker.
    * On dismiss all subscriptions are stopped and the connection is closed.
    """

    BINDINGS = [
        Binding("escape,q", "dismiss", "Close"),
        Binding("s", "save_log", "Save to file"),
    ]

    class Closed(Message):
        """Posted when the modal is dismissed so the app can resume scanning."""

    CSS = """
    DeviceDetailScreen {
        align: center middle;
    }
    #detail-container {
        width: 80%;
        height: 80%;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    #detail-title {
        text-style: bold;
        margin-bottom: 1;
    }
    #detail-log {
        height: 1fr;
    }
    #detail-hint {
        margin-top: 1;
        color: $text-muted;
    }
    """

    def __init__(
        self,
        address: str,
        atc_data: dict | None = None,
        gatt_cache: dict | None = None,
    ) -> None:
        super().__init__()
        self._address = address
        self._atc_data = atc_data
        self._gatt_cache = gatt_cache       # pre-fetched GATT data from bulk inspector
        self._dismiss_event = asyncio.Event()  # set when the modal is closing
        self._live_client: BleakClient | None = None  # kept for cleanup

    def compose(self) -> ComposeResult:
        with Vertical(id="detail-container"):
            yield Label(f"Device: {self._address}", id="detail-title")
            yield RichLog(id="detail-log", markup=True, highlight=True)
            yield Static(
                "Press [bold]Escape[/bold]/[bold]Q[/bold] to close  |  [bold]S[/bold] to save log",
                id="detail-hint",
            )

    def on_mount(self) -> None:
        self.run_worker(self._inspect_device(), exclusive=True)

    def on_unmount(self) -> None:
        # Signal the worker to stop and clean up live subscriptions
        self._dismiss_event.set()
        self.app.post_message(DeviceDetailScreen.Closed())

    def action_save_log(self) -> None:
        """Write the detail log contents to a plain-text file."""
        import re
        log: RichLog = self.query_one("#detail-log", RichLog)
        lines = [strip.text for strip in log.lines]
        safe_addr = re.sub(r"[^A-Za-z0-9_-]", "_", self._address)
        filename = f"ble_detail_{safe_addr}.txt"
        path = os.path.join(os.path.dirname(__file__), filename)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"Device: {self._address}\n")
            fh.write("=" * 60 + "\n")
            fh.write("\n".join(lines))
            fh.write("\n")
        hint: Static = self.query_one("#detail-hint", Static)
        hint.update(f"Saved to [bold]{filename}[/bold]")

    # ------------------------------------------------------------------
    # Cached GATT display
    # ------------------------------------------------------------------

    def _render_cached_gatt(self, log: RichLog) -> None:
        """Render previously cached GATT data into the log immediately."""
        if not self._gatt_cache:
            return
        ts = self._gatt_cache.get("timestamp", "?")
        log.write(f"[bold cyan]Cached GATT data (from {ts}):[/bold cyan]")

        read_values: dict[str, str] = self._gatt_cache.get("read_values", {})
        notify_values: dict[str, str] = self._gatt_cache.get("notify_values", {})
        services: list[dict] = self._gatt_cache.get("services", [])

        # Sensor readings (known SIG UUIDs)
        sensor_lines: list[tuple[str, str]] = []
        for uuid, value in {**read_values, **notify_values}.items():
            spec = SENSOR_CHARACTERISTICS.get(uuid.lower())
            if spec:
                sensor_lines.append((str(spec["label"]), value))
        if sensor_lines:
            log.write("[bold green]  Sensor Readings:[/bold green]")
            for label, value in sensor_lines:
                log.write(f"    {label + ':':<22} [cyan]{value}[/cyan]")

        # Unknown characteristic values
        all_uuids = list(dict.fromkeys(list(read_values.keys()) + list(notify_values.keys())))
        unknown_lines: list[tuple[str, list[str]]] = []
        for uuid in all_uuids:
            if SENSOR_CHARACTERISTICS.get(uuid.lower()):
                continue  # already shown above
            parts: list[str] = []
            if uuid in read_values:
                parts.append(f"read:   {read_values[uuid]}")
            if uuid in notify_values:
                parts.append(f"notify: {notify_values[uuid]}")
            if parts:
                unknown_lines.append((uuid, parts))
        if unknown_lines:
            log.write("[bold yellow]  Characteristic Data:[/bold yellow]")
            for uuid, parts in unknown_lines:
                log.write(f"    [dim]{uuid}[/dim]")
                for part in parts:
                    log.write(f"      {part}")

        # Service/characteristic tree
        log.write("[bold]  GATT Services:[/bold]")
        for svc in services:
            log.write(
                f"\n  [bold yellow]Service:[/bold yellow] [white]{svc['uuid']}[/white]"
                f"  [dim]{svc.get('description','')}[/dim]  handle={svc.get('handle','?')}"
            )
            for char in svc.get("characteristics", []):
                log.write(
                    f"    [blue]Characteristic:[/blue] {char['uuid']}  "
                    f"props={char.get('properties', [])}"
                )
        log.write("")

    # ------------------------------------------------------------------
    # Main inspection worker (live connection + live notifications)
    # ------------------------------------------------------------------

    async def _inspect_device(self) -> None:
        log: RichLog = self.query_one("#detail-log", RichLog)

        # --- Passive advertisement sensor data (no connection required) -------
        if self._atc_data is not None:
            source = self._atc_data.get("source", "Advertisement")
            log.write(f"[bold green]Sensor Data ({source}):[/bold green]")
            if "temperature" in self._atc_data:
                log.write(f"  Temperature:   [cyan]{self._atc_data['temperature']:.2f} °C[/cyan]")
            if "humidity" in self._atc_data:
                log.write(f"  Humidity:      [cyan]{self._atc_data['humidity']:.2f} %RH[/cyan]")
            if "battery_mv" in self._atc_data and "battery_level" in self._atc_data:
                log.write(f"  Battery:       [cyan]{self._atc_data['battery_mv']} mV  ({self._atc_data['battery_level']}%)[/cyan]")
            if "counter" in self._atc_data:
                log.write(f"  Measurement #: [dim]{self._atc_data['counter']}[/dim]")
            log.write("")

        # --- Show cached GATT snapshot if available --------------------------
        if self._gatt_cache:
            self._render_cached_gatt(log)
            log.write("[dim]Re-connecting for live data...[/dim]")

        # --- Active GATT connection -------------------------------------------
        # Wait for any in-progress scan to complete before touching the adapter
        if getattr(self.app, "_scanning", False):
            log.write("[dim]Waiting for scan to finish...[/dim]")
            while getattr(self.app, "_scanning", False):
                await asyncio.sleep(0.5)

        # Also wait for background bulk inspector to finish with this device
        while getattr(self.app, "_bulk_inspecting", False):
            log.write("[dim]Waiting for background GATT inspector...[/dim]")
            await asyncio.sleep(1.0)
            break  # only show the message once; check once is enough

        log.write(f"[cyan]Connecting to {self._address}...[/cyan]")
        client = BleakClient(self._address, timeout=30)
        self._live_client = client
        live_subscribed: list = []
        try:
            await asyncio.wait_for(client.connect(), timeout=30)
            log.write("[green]Connected. Live notifications active — close to disconnect.[/green]")

            services = client.services

            # ---- Buckets --------------------------------------------------------
            # known SIG sensor chars
            notify_chars: list = []
            read_only_chars: list = []
            # all other chars
            unknown_notify: list = []
            unknown_read: list = []

            for service in services:
                for char in service.characteristics:
                    spec = SENSOR_CHARACTERISTICS.get(char.uuid.lower())
                    props = char.properties
                    if spec:
                        if "notify" in props or "indicate" in props:
                            notify_chars.append((char, spec))
                        elif "read" in props:
                            read_only_chars.append((char, spec))
                    else:
                        if "notify" in props or "indicate" in props:
                            unknown_notify.append(char)
                        if "read" in props:
                            unknown_read.append(char)

            # ---- Read all readable chars immediately -------------------------
            sensor_readings: list[tuple[str, str]] = []

            for char, spec in read_only_chars:
                try:
                    raw = await client.read_gatt_char(char)
                    sensor_readings.append((str(spec["label"]), spec["decode"](raw)))  # type: ignore[operator]
                except Exception as exc:  # pylint: disable=broad-except
                    sensor_readings.append((str(spec["label"]), f"[red](read error: {exc})[/red]"))

            read_results: dict[str, str] = {}
            for char in unknown_read:
                try:
                    raw = await client.read_gatt_char(char)
                    read_results[char.uuid.lower()] = decode_unknown_char(raw)
                except BaseException:  # pylint: disable=broad-except
                    pass

            if sensor_readings:
                log.write("\n[bold green]Sensor Readings (GATT):[/bold green]")
                for label_text, value in sensor_readings:
                    log.write(f"  {label_text + ':':<22} [cyan]{value}[/cyan]")
                log.write("")

            if read_results:
                log.write("\n[bold yellow]Characteristic Reads:[/bold yellow]")
                for uuid, value in read_results.items():
                    log.write(f"  [dim]{uuid}[/dim]")
                    log.write(f"    read:   {value}")
                log.write("")

            # ---- Full GATT tree -------------------------------------------------
            log.write("[bold]GATT Services:[/bold]")
            for service in services:
                log.write(
                    f"\n[bold yellow]Service:[/bold yellow] [white]{service.uuid}[/white]"
                    f"  [dim]{service.description}[/dim]  handle={service.handle}"
                )
                for char in service.characteristics:
                    log.write(
                        f"  [blue]Characteristic:[/blue] {char.uuid}  "
                        f"props={char.properties}"
                    )
                    for desc in char.descriptors:
                        log.write(
                            f"    [magenta]Descriptor:[/magenta] {desc.uuid}  "
                            f"handle={desc.handle}"
                        )
            log.write("")

            # ---- Live notify subscriptions --------------------------------------
            # Subscribe to ALL notify/indicate characteristics and keep them open
            # until the modal is dismissed.  Each push appends a new line to the
            # log with a [live] marker and a timestamp.
            all_notify_chars: list = (
                [char for char, _ in notify_chars] + unknown_notify
            )

            def _make_live_handler(char_obj, spec_obj):
                uuid = char_obj.uuid.lower()

                def _handler(_sender, data: bytearray) -> None:
                    raw = bytes(data)
                    ts = datetime.now().strftime("%H:%M:%S")
                    if spec_obj:
                        try:
                            decoded = spec_obj["decode"](raw)  # type: ignore[operator]
                            label_text = str(spec_obj["label"])
                        except BaseException:  # pylint: disable=broad-except
                            decoded = decode_unknown_char(raw)
                            label_text = uuid
                    else:
                        decoded = decode_unknown_char(raw)
                        label_text = uuid
                    try:
                        log.write(
                            f"[bold green][live][/bold green] [dim]{ts}[/dim]  "
                            f"[blue]{label_text}[/blue]  [cyan]{decoded}[/cyan]"
                        )
                    except Exception:  # pylint: disable=broad-except
                        pass  # log may have been unmounted

                return _handler

            if all_notify_chars:
                log.write(
                    f"[dim]Subscribing to {len(all_notify_chars)} notify/indicate "
                    f"characteristic(s) — updates appear below in real time...[/dim]"
                )
                for char in all_notify_chars:
                    spec = SENSOR_CHARACTERISTICS.get(char.uuid.lower())
                    try:
                        await client.start_notify(char, _make_live_handler(char, spec))
                        live_subscribed.append(char)
                    except BaseException as sub_exc:  # pylint: disable=broad-except
                        log.write(
                            f"[red]Could not subscribe to {char.uuid}: {sub_exc}[/red]"
                        )

                if live_subscribed:
                    log.write(
                        f"[green]Subscribed to {len(live_subscribed)} characteristic(s). "
                        f"Waiting for pushes...[/green]"
                    )
                    # Block here until the user closes the modal
                    await self._dismiss_event.wait()
            else:
                log.write("[dim]No notify/indicate characteristics found.[/dim]")
                # Nothing to wait on — show a message and stay open until dismissed
                await self._dismiss_event.wait()

        except BaseException as exc:  # pylint: disable=broad-except
            log.write(f"[red]Error: {exc!r}[/red]")
            self.log.warning(str(exc))
        finally:
            # Stop all live subscriptions before disconnecting
            for char in live_subscribed:
                try:
                    await client.stop_notify(char)
                except BaseException:  # pylint: disable=broad-except
                    pass
            try:
                await client.disconnect()
            except BaseException:  # pylint: disable=broad-except
                pass
            try:
                log.write("\n[dim]Disconnected.[/dim]")
            except Exception:  # pylint: disable=broad-except
                pass


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------


class BleScannerApp(App):
    """Bluetooth Low Energy scanner — Textual TUI"""

    TITLE = "Bluetooth WOS"
    SUB_TITLE = "BLE Device Scanner"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "rescan", "Rescan"),
        Binding("d", "toggle_dark", "Dark mode"),
        Binding("enter,c", "connect", "Connect to device"),
        Binding("s", "toggle_redact", "Redact addresses"),
        Binding("f", "focus_filter", "Filter"),
        Binding("escape", "clear_filter", "Clear filter", show=False),
    ]

    CSS = """
    Screen {
        layout: vertical;
    }
    #table-container {
        height: 1fr;
        border-bottom: solid $primary;
    }
    #devices-table {
        height: 1fr;
    }
    #scan-bar {
        height: 5;
        padding: 0 2;
        background: $panel;
        align: left middle;
        border-bottom: solid $panel-darken-2;
    }
    #scan-label {
        width: 16;
        color: $text;
    }
    #progress {
        width: 40;
        margin-right: 2;
        height: 3;
    }
    #filter-input {
        width: 30;
        height: 3;
        margin-right: 2;
    }
    #device-count {
        width: 1fr;
        height: 3;
        content-align: right middle;
        color: $text-muted;
    }
    """

    # Ordered column definitions: (internal key, display label)
    _COLUMNS = [
        ("index",      "#"),
        ("address",    "Address"),
        ("name",       "Name"),
        ("type",       ""),
        ("rssi",       "RSSI"),
        ("tx_power",   "TX Power"),
        ("services",   "Services"),
        ("company",    "Company"),
        ("distance",   "Distance (m)"),
        ("last_seen",  "Last Seen"),
        ("first_seen", "First Seen"),
        ("duration",   "Duration"),
    ]

    def __init__(self, redacted_address: bool = False) -> None:
        super().__init__()
        self._redacted_address = redacted_address
        self._devices_dict: dict[str, list[str]] = {}
        self._atc_data: dict[str, dict] = {}
        self._service_uuids: dict[str, list[str]] = {}  # address -> advertised service UUIDs
        self._gatt_cache: dict[str, dict] = {}           # address -> full GATT inspection result
        self._company_dict: dict = {}
        self._services_dict: dict = {}
        self._discovery_timeout = 10
        self._scan_interval = 300         # seconds between scan cycles (scan takes 10s, 290s idle)
        self._private_resolvable_random_address_timeout = 120
        self._signal_propagation_constant = 8
        self._scanning = False
        self._connecting = False
        self._bulk_inspecting = False     # True while _bulk_inspect_all is running
        self._scan_tick = 0
        self._filter_text: str = ""
        self._sort_key: str | None = None    # column key currently sorted on
        self._sort_asc: bool = True          # True = ascending
        self._ensure_bluetooth_public_information_is_saved()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="table-container"):
            yield DataTable(
                id="devices-table",
                cursor_type="row",
                zebra_stripes=True,
            )
        with Horizontal(id="scan-bar"):
            yield Label("Idle", id="scan-label")
            yield ProgressBar(
                total=self._discovery_timeout,
                id="progress",
                show_eta=False,
            )
            yield Input(placeholder="Filter...", id="filter-input")
            yield Label("0 devices", id="device-count")
        yield Footer()

    def on_mount(self) -> None:
        table: DataTable = self.query_one("#devices-table", DataTable)
        for key, label in self._COLUMNS:
            table.add_column(label, key=key)

        # Initial scan, then repeat every (timeout + 1) seconds
        self.run_worker(self._scan_cycle(), exclusive=False)
        self.set_interval(self._scan_interval, self._scheduled_scan)
        self.set_interval(1, self._tick_progress)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_rescan(self) -> None:
        """Trigger an immediate rescan."""
        if not self._scanning and not self._connecting:
            self.run_worker(self._scan_cycle(), exclusive=False)

    def action_connect(self) -> None:
        """Open the device detail modal for the selected row."""
        table: DataTable = self.query_one("#devices-table", DataTable)
        if not table.rows or table.cursor_row < 0:
            return
        row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        address = str(row_key.value)

        atc_data = self._atc_data.get(address)
        gatt_cache = self._gatt_cache.get(address)
        self._connecting = True
        self.push_screen(DeviceDetailScreen(address, atc_data=atc_data, gatt_cache=gatt_cache))

    def on_device_detail_screen_closed(self, _message: DeviceDetailScreen.Closed) -> None:
        """Resume scanning after the detail modal is dismissed."""
        self._connecting = False
        if not self._scanning:
            self.run_worker(self._scan_cycle(), exclusive=False)

    def action_toggle_redact(self) -> None:
        """Toggle address redaction and refresh the table."""
        self._redacted_address = not self._redacted_address
        self._refresh_table()

    def action_focus_filter(self) -> None:
        """Focus the filter input box."""
        self.query_one("#filter-input", Input).focus()

    def action_clear_filter(self) -> None:
        """Clear the filter if the input is focused; otherwise pass through."""
        input_widget = self.query_one("#filter-input", Input)
        if self.focused is input_widget:
            input_widget.value = ""
            self.query_one("#devices-table", DataTable).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        """Live-filter the table as the user types."""
        if event.input.id == "filter-input":
            self._filter_text = event.value.strip().lower()
            self._refresh_table()

    def on_data_table_header_selected(self, event: DataTable.HeaderSelected) -> None:
        """Sort by the clicked column; clicking again reverses direction."""
        col_key = str(event.column_key.value)
        if self._sort_key == col_key:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_key = col_key
            self._sort_asc = True
        self._refresh_table()

    # ------------------------------------------------------------------
    # Scan lifecycle
    # ------------------------------------------------------------------

    def _scheduled_scan(self) -> None:
        if not self._scanning and not self._connecting:
            self.run_worker(self._scan_cycle(), exclusive=False)

    async def _scan_cycle(self) -> None:
        if self._scanning or self._connecting:
            return
        self._scanning = True
        self._scan_tick = 0

        progress: ProgressBar = self.query_one("#progress", ProgressBar)
        label: Label = self.query_one("#scan-label", Label)
        progress.update(progress=0)
        label.update("Scanning...")

        try:
            self._expire_old_devices()
            self.log.info("bluetooth scan started")
            devices_data = await BleakScanner.discover(
                timeout=self._discovery_timeout, return_adv=True
            )
            self.log.info(f"bluetooth scan ended — {len(devices_data)} devices")

            for device_key in devices_data:
                device, adv = devices_data[device_key]
                self._handle_advertisement(device, adv)

            self._calculate_missing_distances_regression()
            self._refresh_table()
            self._write_csv()

            # Kick off background GATT inspection for all discovered devices
            if not self._bulk_inspecting and not self._connecting:
                self.run_worker(self._bulk_inspect_all(), exclusive=False)
        except Exception as exc:  # pylint: disable=broad-except
            self.log.error(str(exc))
        finally:
            self._scanning = False
            progress.update(progress=self._discovery_timeout)
            label.update("Idle")

    def _tick_progress(self) -> None:
        """Advance the progress bar by one tick each second while scanning."""
        if not self._scanning:
            return
        self._scan_tick = min(self._scan_tick + 1, self._discovery_timeout)
        self.query_one("#progress", ProgressBar).update(progress=self._scan_tick)

    def _expire_old_devices(self) -> None:
        """Remove devices not seen within the resolvable-address timeout window."""
        threshold = datetime.now() - timedelta(
            seconds=self._private_resolvable_random_address_timeout
        )
        to_remove = [
            addr
            for addr, data in self._devices_dict.items()
            if datetime.strptime(
                data[self._col("last_seen")], "%Y-%m-%d %H:%M:%S"
            ) < threshold
        ]
        for addr in to_remove:
            del self._devices_dict[addr]
            self._atc_data.pop(addr, None)
            self._service_uuids.pop(addr, None)
            self._gatt_cache.pop(addr, None)
            self.log.info(f"expired device {addr}")

    def _handle_advertisement(
        self, device: BLEDevice, adv: AdvertisementData
    ) -> None:
        service_count = len(adv.service_data) if adv.service_data else 0

        company: str | None = None
        if adv.manufacturer_data:
            for company_id, _ in adv.manufacturer_data.items():
                if company_id and company_id != 0:
                    company = self._get_entity_name(company_id, "company") or str(company_id)
                    break

        rssi = -abs(adv.rssi or 0)
        tx_power = -abs(adv.tx_power or 0)
        distance = device_distance_calculation(
            tx_power, rssi, self._signal_propagation_constant
        )
        last_seen = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        existing = self._devices_dict.get(device.address)
        first_seen = existing[self._col("first_seen")] if existing else last_seen

        first_seen_dt = datetime.strptime(first_seen, "%Y-%m-%d %H:%M:%S")
        last_seen_dt = datetime.strptime(last_seen, "%Y-%m-%d %H:%M:%S")
        delta = last_seen_dt - first_seen_dt
        total_seconds = int(delta.total_seconds())
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        duration = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

        adv_sensors = decode_advertisement_sensors(adv.service_data)
        if adv_sensors is not None:
            self._atc_data[device.address] = adv_sensors

        if adv.service_uuids:
            self._service_uuids[device.address] = list(adv.service_uuids)

        beacon_type = detect_beacon_type(
            adv.manufacturer_data or {},
            adv.service_data or {},
        )

        if adv_sensors is not None:
            type_icon = "🌡"
        elif beacon_type is not None:
            type_icon = f"📡"
        else:
            type_icon = ""

        self._devices_dict[device.address] = [
            "0",
            str(device.address),
            str(device.name or "Unknown"),
            type_icon,
            str(rssi),
            str(tx_power),
            str(service_count),
            str(company or ""),
            f"{distance:.2f}",
            last_seen,
            first_seen,
            duration,
        ]

    # ------------------------------------------------------------------
    # Bulk GATT inspection (background, runs after each passive scan)
    # ------------------------------------------------------------------

    async def _bulk_inspect_all(self) -> None:
        """Connect to every discovered device in turn, fetch full GATT tree,
        read all readable characteristics, subscribe to all notify/indicate
        characteristics for 3 s each, and cache the results.

        Runs after each passive scan cycle completes.  Because the BLE adapter
        cannot scan and connect simultaneously the method waits for any active
        scan before each connection attempt.
        """
        if self._bulk_inspecting:
            return  # already running — skip
        self._bulk_inspecting = True

        label: Label = self.query_one("#scan-label", Label)
        addresses = list(self._devices_dict.keys())  # snapshot

        self.log.info(f"bulk GATT inspect starting for {len(addresses)} device(s)")

        for address in addresses:
            # Skip if the device has since been expired or is open in the modal
            if address not in self._devices_dict:
                continue
            if self._connecting:
                self.log.info(f"bulk inspect skipping {address} — modal open")
                continue

            # Wait for any active scan before touching the adapter
            while self._scanning:
                await asyncio.sleep(0.5)

            label.update(f"GATT {address[:8]}…")
            self.log.info(f"bulk inspect connecting to {address}")

            client = BleakClient(address, timeout=15)
            try:
                await asyncio.wait_for(client.connect(), timeout=15)
                services = client.services

                # ---- Collect service / characteristic tree ------------------
                service_list: list[dict] = []
                all_notify_chars: list = []
                all_read_chars: list = []

                for service in services:
                    char_list: list[dict] = []
                    for char in service.characteristics:
                        char_list.append({
                            "uuid": char.uuid,
                            "description": char.description,
                            "properties": list(char.properties),
                            "handle": char.handle,
                        })
                        props = char.properties
                        if "notify" in props or "indicate" in props:
                            all_notify_chars.append(char)
                        if "read" in props:
                            all_read_chars.append(char)
                    service_list.append({
                        "uuid": service.uuid,
                        "description": service.description,
                        "handle": service.handle,
                        "characteristics": char_list,
                    })

                # ---- Read all readable characteristics ----------------------
                read_values: dict[str, str] = {}
                for char in all_read_chars:
                    try:
                        raw = await client.read_gatt_char(char)
                        spec = SENSOR_CHARACTERISTICS.get(char.uuid.lower())
                        if spec:
                            read_values[char.uuid.lower()] = spec["decode"](raw)  # type: ignore[operator]
                        else:
                            read_values[char.uuid.lower()] = decode_unknown_char(raw)
                    except BaseException:  # pylint: disable=broad-except
                        pass

                # ---- Subscribe to all notify/indicate for 3 s ---------------
                notified_values: dict[str, bytes] = {}

                def _make_bulk_handler(uuid: str):
                    def _handler(_sender, data: bytearray) -> None:
                        notified_values[uuid] = bytes(data)
                    return _handler

                subscribed: list = []
                for char in all_notify_chars:
                    try:
                        await client.start_notify(char, _make_bulk_handler(char.uuid.lower()))
                        subscribed.append(char)
                    except BaseException:  # pylint: disable=broad-except
                        pass

                if subscribed:
                    await asyncio.sleep(3.0)
                    for char in subscribed:
                        try:
                            await client.stop_notify(char)
                        except BaseException:  # pylint: disable=broad-except
                            pass

                # Decode notified values
                notify_values: dict[str, str] = {}
                for uuid, raw in notified_values.items():
                    spec = SENSOR_CHARACTERISTICS.get(uuid)
                    if spec:
                        try:
                            notify_values[uuid] = spec["decode"](raw)  # type: ignore[operator]
                        except BaseException:  # pylint: disable=broad-except
                            notify_values[uuid] = decode_unknown_char(raw)
                    else:
                        notify_values[uuid] = decode_unknown_char(raw)

                self._gatt_cache[address] = {
                    "services": service_list,
                    "read_values": read_values,
                    "notify_values": notify_values,
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                self.log.info(
                    f"bulk inspect cached {address}: "
                    f"{len(service_list)} services, "
                    f"{len(read_values)} reads, "
                    f"{len(notify_values)} notifies"
                )

                # Update the services column with GATT-discovered count
                self._update_services_column(address, len(service_list))

            except BaseException as exc:  # pylint: disable=broad-except
                self.log.warning(f"bulk inspect failed for {address}: {exc}")
            finally:
                try:
                    await client.disconnect()
                except BaseException:  # pylint: disable=broad-except
                    pass

        self._bulk_inspecting = False
        label.update("Idle")
        self.log.info("bulk GATT inspect complete")

    def _update_services_column(self, address: str, gatt_service_count: int) -> None:
        """Overwrite the Services column value with the GATT-discovered count."""
        if address not in self._devices_dict:
            return
        self._devices_dict[address][self._col("services")] = str(gatt_service_count)
        # Live-update the table cell if the row is currently visible
        table: DataTable = self.query_one("#devices-table", DataTable)
        try:
            table.update_cell(address, "services", str(gatt_service_count))
        except Exception:  # pylint: disable=broad-except
            pass  # row may not be visible (filtered out)

    # ------------------------------------------------------------------
    # Table rendering
    # ------------------------------------------------------------------

    def _refresh_table(self) -> None:
        table: DataTable = self.query_one("#devices-table", DataTable)

        # Build the ordered, filtered list of (address, data) to display
        items = list(self._devices_dict.items())

        # --- Filter ---
        if self._filter_text:
            items = [
                (addr, data)
                for addr, data in items
                if any(self._filter_text in str(v).lower() for v in data)
            ]

        # --- Sort ---
        if self._sort_key is not None:
            sort_idx = self._col(self._sort_key)
            def _sort_value(item: tuple) -> str | float:
                raw = item[1][sort_idx]
                # Numeric columns: sort as float for correct ordering
                try:
                    return float(raw)
                except (ValueError, TypeError):
                    return str(raw).lower()
            items.sort(key=_sort_value, reverse=not self._sort_asc)

        # Update index values before rendering
        for index, (address, data) in enumerate(items):
            data[self._col("index")] = str(index)

        # Rebuild the table from scratch so row order matches the sorted list
        table.clear(columns=False)
        for address, data in items:
            table.add_row(*self._render_row(data), key=address)

        total = len(self._devices_dict)
        shown = len(items)
        count_text = (
            f"{shown}/{total} device(s)"
            if self._filter_text else
            f"{total} device(s)"
        )
        self.query_one("#device-count", Label).update(count_text)

    def _render_row(self, data: list[str]) -> list[str]:
        rendered = []
        for i, value in enumerate(data):
            text = str(value)
            if self._redacted_address and i == self._col("address"):
                text = text[:3] + "." * (len(text) - 3)
            rendered.append(text)
        return rendered

    # ------------------------------------------------------------------
    # Distance regression (log-linear RSSI → distance model)
    # ------------------------------------------------------------------

    def _calculate_missing_distances_regression(self) -> None:
        rssi_values: list[float] = []
        distance_values: list[float] = []

        for device in self._devices_dict.values():
            if (
                abs(int(device[self._col("rssi")])) > 0
                and abs(int(device[self._col("tx_power")])) > 0
                and float(device[self._col("distance")]) > 0
            ):
                rssi_values.append(abs(int(device[self._col("rssi")])))
                distance_values.append(float(device[self._col("distance")]))

        if len(rssi_values) < 3:
            return  # not enough data to fit a model

        log_distances = np.log10(distance_values)
        lr = tuple(stats.linregress(rssi_values, log_distances))
        slope: float = float(lr[0])  # type: ignore[arg-type]
        intercept: float = float(lr[1])  # type: ignore[arg-type]

        for device in self._devices_dict.values():
            if abs(int(device[self._col("tx_power")])) == 0:
                rssi = abs(int(device[self._col("rssi")]))
                device[self._col("distance")] = f"{10 ** (slope * rssi + intercept):.2f}"

    # ------------------------------------------------------------------
    # CSV export
    # ------------------------------------------------------------------

    def _write_csv(self) -> None:
        try:
            col_keys = [k for k, _ in self._COLUMNS]
            with open("devices.csv", mode="w", newline="", encoding="utf8") as f:
                writer = csv.writer(f)
                writer.writerow(col_keys)
                for values in self._devices_dict.values():
                    writer.writerow(values)
        except Exception as exc:  # pylint: disable=broad-except
            self.log.warning(str(exc))

    # ------------------------------------------------------------------
    # Bluetooth public data (company identifiers / service UUIDs)
    # ------------------------------------------------------------------

    def _ensure_bluetooth_public_information_is_saved(self) -> None:
        def save(filename: str, url: str) -> None:
            if os.path.isfile(filename):
                return
            self.log.info(f"downloading {filename} from {url}")
            response = requests.get(url, timeout=5000)
            response.raise_for_status()
            with open(filename, "w", encoding="utf-8") as f:
                f.write(response.text.strip())
            self.log.info(f"saved {filename}")

        save(
            "service_uuids.yaml",
            "https://bitbucket.org/bluetooth-SIG/public/raw/025ac280519f8ad3967f79ee45bd921a76003113"
            "/assigned_numbers/uuids/service_uuids.yaml",
        )
        save(
            "company_identifiers.yaml",
            "https://bitbucket.org/bluetooth-SIG/public/raw/025ac280519f8ad3967f79ee45bd921a76003113"
            "/assigned_numbers/company_identifiers/company_identifiers.yaml",
        )

    @staticmethod
    def _strip_invalid(s: str) -> str:
        """Strip non-printable characters that confuse the YAML parser."""
        res = ""
        for ch in s:
            if Reader.NON_PRINTABLE.match(ch):
                continue
            res += ch
        return res

    def _get_entity_name(self, value: int, entity_type: str) -> str | None:
        entity_meta = {
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
            },
        }
        if entity_type not in entity_meta:
            raise ValueError(f"Unknown entity type: {entity_type}")

        meta = entity_meta[entity_type]
        attr = meta["dict_attr"]

        if not getattr(self, attr, {}):
            with open(meta["file_name"], "r", encoding="utf-8") as f:
                yaml = YAML(typ="safe")
                setattr(self, attr, yaml.load(BleScannerApp._strip_invalid(f.read())))

        for entity in getattr(self, attr)[meta["root_key"]]:
            if entity[meta["value_key"]] == value:
                return entity["name"]
        return None

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def _col(self, key: str) -> int:
        """Return the list index for a given column key."""
        for i, (k, _) in enumerate(self._COLUMNS):
            if k == key:
                return i
        raise KeyError(f"Unknown column key: {key}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = BleScannerApp(redacted_address=True)
    app.run()
