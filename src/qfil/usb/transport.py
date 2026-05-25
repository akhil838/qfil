"""Small PyUSB bulk transport for Qualcomm 9008 devices."""

from __future__ import annotations

from dataclasses import dataclass
import time

import usb.core
import usb.util


class UsbTransportError(RuntimeError):
    pass


DEFAULT_USB_IDS = ((0x05C6, 0x9008), (0x05C6, 0x900E))


@dataclass
class QualcommUsbTransport:
    vid: int = 0x05C6
    pid: int = 0x9008
    timeout_ms: int = 3000
    write_chunk_size: int = 1024 * 1024

    def __post_init__(self) -> None:
        self.device = None
        self.ep_in = None
        self.ep_out = None

    @classmethod
    def auto(cls, timeout_ms: int = 3000) -> "QualcommUsbTransport":
        for vid, pid in DEFAULT_USB_IDS:
            if usb.core.find(idVendor=vid, idProduct=pid) is not None:
                return cls(vid=vid, pid=pid, timeout_ms=timeout_ms)
        raise UsbTransportError("No Qualcomm 9008 USB device found.")

    def open(self) -> "QualcommUsbTransport":
        self.device = usb.core.find(idVendor=self.vid, idProduct=self.pid)
        if self.device is None:
            raise UsbTransportError(
                f"USB device {self.vid:04x}:{self.pid:04x} not found."
            )
        self.device.set_configuration()
        cfg = self.device.get_active_configuration()
        intf = cfg[(0, 0)]
        if self.device.is_kernel_driver_active(intf.bInterfaceNumber):
            try:
                self.device.detach_kernel_driver(intf.bInterfaceNumber)
            except (NotImplementedError, usb.core.USBError):
                pass
        self.ep_in = usb.util.find_descriptor(
            intf,
            custom_match=lambda endpoint: (
                usb.util.endpoint_type(endpoint.bmAttributes)
                == usb.util.ENDPOINT_TYPE_BULK
                and usb.util.endpoint_direction(endpoint.bEndpointAddress)
                == usb.util.ENDPOINT_IN
            ),
        )
        self.ep_out = usb.util.find_descriptor(
            intf,
            custom_match=lambda endpoint: (
                usb.util.endpoint_type(endpoint.bmAttributes)
                == usb.util.ENDPOINT_TYPE_BULK
                and usb.util.endpoint_direction(endpoint.bEndpointAddress)
                == usb.util.ENDPOINT_OUT
            ),
        )
        if self.ep_in is None or self.ep_out is None:
            raise UsbTransportError("Could not find Qualcomm bulk IN/OUT endpoints.")
        return self

    def close(self) -> None:
        if self.device is not None:
            usb.util.dispose_resources(self.device)
        self.device = None
        self.ep_in = None
        self.ep_out = None

    def __enter__(self) -> "QualcommUsbTransport":
        return self.open()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def write(self, data: bytes) -> None:
        if self.ep_out is None:
            raise UsbTransportError("USB transport is not open.")
        if not data:
            try:
                self.ep_out.write(b"", timeout=self.timeout_ms)
            except usb.core.USBError:
                pass
            return
        view = memoryview(data)
        for offset in range(0, len(view), self.write_chunk_size):
            self.ep_out.write(
                view[offset : offset + self.write_chunk_size], timeout=self.timeout_ms
            )

    def read(self, size: int = 1024 * 1024, timeout_ms: int | None = None) -> bytes:
        if self.ep_in is None:
            raise UsbTransportError("USB transport is not open.")
        timeout = self.timeout_ms if timeout_ms is None else timeout_ms
        try:
            return bytes(self.ep_in.read(size, timeout=timeout))
        except usb.core.USBTimeoutError:
            return b""

    def read_until(self, marker: bytes, timeout_s: float = 10.0) -> bytes:
        deadline = time.monotonic() + timeout_s
        data = bytearray()
        while time.monotonic() < deadline:
            chunk = self.read(timeout_ms=500)
            if chunk:
                data += chunk
                if marker in data:
                    return bytes(data)
        return bytes(data)
