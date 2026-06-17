<h1 align="center">Bluetooth "Wall of Sheep"</h1>

![Python](https://img.shields.io/badge/python-3.10+-blue)
![Issues](https://img.shields.io/github/issues/skittleson/bluetooth-wos)


> > Bluetooth "Wall of Sheep" is a lightweight Python application that scans for nearby Bluetooth devices and displays them in a live-updating, interactive board. It's ideal for demonstrating Bluetooth visibility and presence tracking in real-time.


## ✨ Demo

![Interactive app](app.jpg)


## ✅ Features

### 🔍 Core Features
- Live discovery of nearby Bluetooth devices
- Interactive “Wall of Sheep” display with metadata
- Company identification via public device listing
- Real-time CSV export of scanned device data

### 📏 Device Intelligence
- Estimates distance using RSSI and TX Power
- Highlights persistent devices ("stick around")
- Removes transient devices to handle address randomization
- Detects and hides device MAC addresses for privacy
- Decodes common service data passively (no GATT connection): Temperature,
  Humidity, Battery Level, TX Power, Heart Rate, and Eddystone (TLM/UID/URL)

### 🔧 Configurable Behavior
- Adjustable timeout for inactive devices
- Toggle address visibility


## 🚀 Quick Start

### ⚙️ Requirements
- Python 3.10+
- Linux or macOS (Bluetooth support)
- [`uv`](https://docs.astral.sh/uv/)

### 📦 Install globally

Install as a global command with `uv`:

```bash
uv tool install .
```

Then run it from anywhere:

```bash
bluetooth-wos
```

To pick up local changes, reinstall:

```bash
uv tool install --reinstall .
```

### 🧪 Run from source

```bash
uv venv
uv pip sync requirements.txt
uv run bluetooth_wos.py
```

## Development

`python -m pylint $(git ls-files '*.py')`

## 🛣️ Roadmap
 
 - [ ] Resolve services by name
 - [x] Estimates distance from transmitter and receiver of a device given ONLY RSSI if know distance values are present.
 - [ ] Configurable columns
 - [ ] Fingerprint devices that keep changing MAC addresses
 - [ ] Show adv data
 - [ ] Interactive way to go into service data
 - [x] Resolve common service->characteristics such as temp/humidity
 - [ ] attempt to keep same indexes of current devices
 - [x] Load spinner on first load. It's boring to see nothing in a table
 - [ ] no coloring option

## 🤝 Contributing

Contributions, issues and feature requests are welcome.<br />
Feel free to check [issues page](https://github.com/skittleson/bluetooth-wos/issues) if you want to contribute.<br />

## Author

👤 **Spencer Kittleson**

- Github: [@skittleson](https://github.com/skittleson)
- LinkedIn: [@skittleson](https://www.linkedin.com/in/skittleson)
- Blog: [DoCodeThatMatters](https://docodethatmatters.com)
- X: [@skittleson](https://twitter.com/skittleson)
- StackOverflow: [spencer](https://stackoverflow.com/users/2414540/spencer)

## Show your support

⭐️ this repository if this project helped you! It motivates me a lot! 👋

Buy me a coffee ☕: <a href="https://www.buymeacoffee.com/skittles">skittles</a><br />

## Built with ♥

- python
- rich
- bleak
- ruamel.yaml


## 📑 References

 - https://bitbucket.org/bluetooth-SIG/public/src/main/assigned_numbers/uuids/service_uuids.yaml
