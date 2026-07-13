# USB Blocker

A cross-platform USB mass-storage device control application written in Python.  
Blocks **all** USB storage devices by default and enforces a persistent **allow-list** keyed by vendor ID + product ID + serial number.


## Architecture

```
usb-blocker/
├── core/           Shared logic: device model, config, allowlist, IPC, logger
├── backends/       OS-specific backends (Linux / Windows / macOS)
├── service/        Privileged background daemon + OS-native wrappers
├── tray/           System-tray GUI (pystray + Pillow)
├── config/         Install files (systemd unit, launchd plist, Windows installer)
└── tests/          Unit tests (pytest)
```

Two runtime components:

| Component | Runs as | Purpose |
|---|---|---|
| **Service / daemon** | root / SYSTEM | Monitors USB events, enforces allow-list, hosts IPC socket |
| **Tray app** | regular user | Shows status, lets admin manage allow-list via IPC |


## Requirements

- Python 3.10+
- See `requirements.txt` for pinned dependencies

```bash
pip install -r requirements.txt
# or per-platform:
pip install -e ".[linux]"   # Linux
pip install -e ".[windows]" # Windows
pip install -e ".[macos]"   # macOS
```



## Installation

### Linux (systemd)

```bash
# 1. Install to /opt/usb-blocker
sudo git clone <repo> /opt/usb-blocker
cd /opt/usb-blocker
sudo pip install -e ".[linux]"

# 2. Create required directories
sudo mkdir -p /etc/usb-blocker /run/usb-blocker /var/log/usb-blocker

# 3. Install the systemd unit
sudo cp config/usb_blocker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now usb_blocker.service

# 4. Verify
sudo systemctl status usb_blocker.service
journalctl -u usb_blocker -f

# 5. Run the tray app (as your regular user)
python tray/app.py
```

**Uninstall:**
```bash
sudo systemctl disable --now usb_blocker.service
sudo rm /etc/systemd/system/usb_blocker.service
sudo systemctl daemon-reload
```



### Windows (Windows Service via pywin32)

Run all commands in an **Administrator** Command Prompt or PowerShell.

```powershell
# 1. Install dependencies
pip install -r requirements.txt

# 2. Install and start the service
python config\install_windows.py install

# 3. Verify
sc query UsbBlockerService

# 4. Run the tray app (as your user account)
python tray\app.py
```

**Uninstall:**
```powershell
python config\install_windows.py remove
```

Manual service control:
```powershell
net start UsbBlockerService
net stop  UsbBlockerService
# or via the Services MMC snap-in (services.msc)
```



### macOS (launchd)

```bash
# 1. Install to /opt/usb-blocker
sudo git clone <repo> /opt/usb-blocker
cd /opt/usb-blocker
sudo pip3 install -e ".[macos]"

# 2. Create required directories
sudo mkdir -p /Library/Application\ Support/usb-blocker \
              /var/run/usb-blocker \
              /Library/Logs/usb-blocker

# 3. Install the launchd plist
sudo cp config/com.usblocker.plist /Library/LaunchDaemons/
sudo launchctl load -w /Library/LaunchDaemons/com.usblocker.plist

# 4. Verify
sudo launchctl list | grep usblocker
cat /Library/Logs/usb-blocker/launchd.out.log

# 5. Run the tray app
python3 tray/app.py
```

**Uninstall:**
```bash
sudo launchctl unload -w /Library/LaunchDaemons/com.usblocker.plist
sudo rm /Library/LaunchDaemons/com.usblocker.plist
```

---

## Configuration

Default config is built into `core/config.py`.  To customise, create:

- Linux/macOS: `/etc/usb-blocker/config.yaml`
- Windows: `%ProgramData%\UsbBlocker\config.yaml`

Copy `config/default_config.yaml` as a starting point.

### Key settings

| Key | Default | Description |
|---|---|---|
| `blocking_enabled` | `true` | Global kill-switch (false = allow all) |
| `ipc.socket_path` | `/run/usb-blocker/service.sock` | Unix socket for IPC |
| `log.path` | `/var/log/usb-blocker/usb-blocker.log` | Log file path |
| `log.max_bytes` | `10485760` (10 MB) | Max size per log file |
| `log.backup_count` | `5` | Rotated copies to keep |
| `allowlist_db` | `/etc/usb-blocker/allowlist.db` | SQLite allow-list path |



## Allow-list management

The allow-list is managed by the privileged service.  Use the tray app:

1. Plug in the USB device — it will be **blocked immediately**.
2. Open the tray menu → **Connected Devices** → select the device → **Add to allow-list**.
3. The service adds the entry, unblocks the device, and it becomes accessible.
4. On subsequent connections the device is recognised and allowed automatically.

To remove a device: tray menu → **Allow-list** → select entry → **Remove from allow-list**.



## Logging

Events are logged in two places:

1. **Rotating log file** (configured by `log.path`) — human-readable with structured JSON event lines.
2. **SQLite event_log table** (in `allowlist_db`) — queryable history.

Each event record contains: timestamp, event type (CONNECTED / ALLOWED / BLOCKED / DISCONNECTED), device identity, and action taken.



## Running the Tests

```bash
pip install pytest pytest-mock PyYAML
cd /path/to/usb-blocker
pytest tests/ -v
```

Tests are fully self-contained (no real hardware or OS-level USB access needed). Platform-specific imports are mocked or skipped.



## Blocking Implementation Details

| OS | Mechanism |
|---|---|
| **Linux** | Layer 1: udev rule (`/etc/udev/rules.d/99-usb-blocker.rules`) — prevents auto-mount before the service callback fires. Layer 2: `udisksctl unmount --force` + `udisksctl power-off` via the service on the `add` event. |
| **Windows** | `Win32_PnPEntity.Disable()` via WMI. Fallback: sets `HKLM\SYSTEM\CurrentControlSet\Services\UsbStor\Start = 4` (disables UsbStor driver). |
| **macOS** | `diskutil unmount force <device>` triggered immediately on IOKit notification. |

**Fail-safe**: if a device cannot be fully identified, it is **blocked by default** and a warning is logged.



## Security Notes

- The service runs as **root / SYSTEM**. Only this process writes to the allow-list DB and udev rules.
- The tray app runs as a normal user and communicates via the IPC socket — it **cannot** escalate privileges directly.
- The IPC socket (`/run/usb-blocker/service.sock`) has permissions `0660`; add your tray-app user to a group that has read access if needed (or adjust socket permissions in config).
- To disable protection temporarily, use the tray toggle (this calls `SET_BLOCKING false` over IPC; the service enforces it).
