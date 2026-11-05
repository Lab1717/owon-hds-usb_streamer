OWON USB Streamer

A Python-based USB interface for OWON HDS-series digital multimeters. Monitor measurements in real time directly from your computer through a simple graphical interface.

Features
Real-time measurement readings.
Support for DC/AC voltage and current, resistance, capacitance, diode and continuity modes.
Change AC/DC voltage measurement ranges directly from the GUI.
Switch between AUTO and manual range selection.
Live command and response log.
Export and copy measurement logs.
Automatic multimeter model identification.
USB Connection

The application communicates directly with the multimeter through USB using PyUSB.

It sends SCPI-style commands to configure measurement modes, retrieve readings and control supported range settings.

The device is currently identified using its USB Vendor ID (VID) and Product ID (PID).

Range Control

AC and DC voltage ranges can currently be changed directly from the graphical interface using the RANGE and AUTO buttons.

Range control for resistance and capacitance is not implemented yet. I'm still investigating how to control these functions through the USB protocol.

Requirements
Python 3.8 or newer
PyUSB
Tkinter (usually included with Python)
A compatible OWON HDS-series multimeter
USB connection to the multimeter
A working libusb backend
Installation
1. Clone the repository
bash
git clone https://github.com/Lab1717/owon-hds-usb_streame.git
cd owon-hds-usb_streame

2. Install dependencies
bash
pip install pyusb


Tkinter is normally included with Python. On Linux, it may need to be installed separately:

bash
sudo apt install python3-tk


You also need a working libusb backend for PyUSB.

3. Run the application
bash
python usb_streamer.py


Make sure your multimeter is connected via USB before starting the application.

Depending on your operating system, you may also need to configure USB permissions.

Development Status

🚧 Work in Progress

This project is still under development.

Basic measurement reading and AC/DC voltage range control are working, but some functions are still being investigated and improved.

In particular, resistance and capacitance range control through USB is not yet supported.

Feedback, testing and contributions are welcome!

Developed as an independent project for experimenting with USB communication and remote control of OWON digital multimeters.
