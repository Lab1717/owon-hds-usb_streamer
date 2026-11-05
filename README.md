
# OWON HDS USB DATA Streamer

A Python-based USB interface for **OWON HDS-series digital multimeters**. Monitor measurements in real time directly from your computer through a simple graphical interface.

### Compatibility

**Tested with:** OWON HDS242.

The application is designed for OWON HDS-series multimeters and may also work with other OWON models that support compatible USB communication commands. However, compatibility with other models has not yet been verified.

If you test it with another OWON multimeter, feedback is welcome!
---

## ✨ Features

- Real-time measurement readings.
- Support for DC/AC voltage and current, resistance, capacitance, diode and continuity modes.
- Change AC/DC voltage measurement ranges directly from the GUI.
- Switch between AUTO and manual range selection.
- Live command and response log.
- Export and copy measurement logs.
- Automatic multimeter model identification.

## 🔌 USB Connection

The application communicates directly with the multimeter through USB using **PyUSB**.

It sends SCPI-style commands to configure measurement modes, retrieve readings and control supported range settings.

The device is identified using its USB Vendor ID (VID) and Product ID (PID).

## 🎚️ Range Control

AC and DC voltage ranges can currently be changed directly from the graphical interface using the **RANGE** and **AUTO** buttons.

Range control for resistance and capacitance is not implemented yet. I'm still investigating how to control these functions through the USB protocol.

## 📋 Requirements

- Python 3.8 or newer
- PyUSB
- Tkinter (usually included with Python)
- Compatible OWON HDS-series multimeter
- USB connection
- Working libusb backend

## ⚙️ Installation

### 1. Clone the repository

```bash
git clone https://github.com/Lab1717/owon-hds-usb_streamer.git
cd owon-hds-usb_streamer
```

### 2. Install dependencies

```bash
pip install pyusb
```

On Linux, install Tkinter if necessary:

```bash
sudo apt install python3-tk
```

### 3. USB Permissions (Linux)

Create a udev rule:

```bash
sudo nano /etc/udev/rules.d/99-owon.rules
```

Add this line:

```text
SUBSYSTEM=="usb", ATTR{idVendor}=="5345", ATTR{idProduct}=="1234", MODE="0666"
```

Reload the udev rules:

```bash
sudo udevadm control --reload-rules
sudo udevadm trigger
```

Unplug and reconnect the multimeter.

You should now be able to run the application without root privileges.

**Note:** The VID and PID must match the values configured in the Python application.

### 4. Run the application

```bash
python owon_streamer.py
```

Make sure your multimeter is connected via USB before starting the application.

## 🚧 Development Status

**Work in Progress**

This project is still under development.

Basic measurement reading and AC/DC voltage range control are working, but some functions are still being investigated and improved.

In particular, resistance and capacitance range control through USB is not yet supported.

Feedback, testing and contributions are welcome!

---

*Developed as an independent project for experimenting with USB communication and remote control of OWON digital multimeters.*
