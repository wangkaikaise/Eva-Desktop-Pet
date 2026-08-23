# Third-party notices

Eva Desktop Pet for Windows uses the following third-party components. Their
licenses remain with their respective copyright holders.

- **Python** — Python Software Foundation License.
- **PySide6 / Qt for Python** — LGPL-3.0-only or GPL-3.0-only, with commercial
  licensing available from The Qt Company. The distributed application keeps
  Qt libraries dynamically linked.
- **psutil** — BSD-3-Clause.
- **PyInstaller** — GPL-2.0-or-later with the PyInstaller bootloader exception.
- **LibreHardwareMonitor 0.9.6** — MPL-2.0. Official source and license:
  <https://github.com/LibreHardwareMonitor/LibreHardwareMonitor>.
- **PawnIO 2.2.0** — GPL-2.0-or-later with the upstream special exception for
  independent software communicating only through the device I/O control
  interface. Official source and license: <https://github.com/namazso/PawnIO>.

The build downloads LibreHardwareMonitor and PawnIO only from their official
GitHub releases and verifies pinned SHA-256 checksums before packaging.
