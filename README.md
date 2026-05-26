# qfil

Native Python QFIL/QSahara/Firehose tooling.

## Setup

Install `uv`, then clone and sync:

```bash
git clone https://github.com/akhil838/qfil.git
cd qfil
uv sync
```

## USB Dependencies

This package uses USB/libusb as the primary transport for Qualcomm EDL devices.

### Linux

Install libusb and make sure your user can access the Qualcomm EDL USB device:

```bash
sudo apt install libusb-1.0-0
```

Most systems also need a udev rule for `05c6:9008`, then replug the device.

### macOS

Install libusb:

```bash
brew install libusb
```

No Qualcomm QDLoader COM driver stack is required on macOS.

### Windows

Install the Qualcomm HS-USB QDLoader driver. PyUSB can work when libusb can
access the `05c6:9008` interface through the installed driver binding.

If Windows exposes the device only as `COMx`, USB/libusb access may not work.
That path needs a serial/COM transport backend, which is separate from the
current USB transport.

## Usage

Run the module or console tools through uv:

```bash
uv run qfil --help
uv run fh_loader --help
uv run qsaharaserver --help
```

Python import:

```python
import qfil
```

## Notes

USB flashing requires local USB permissions and a supported Qualcomm EDL device.
