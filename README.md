# qfil

Native Python QFIL/QSahara/Firehose tooling.

## Setup

Install `uv`, then clone and sync:

```bash
git clone https://github.com/akhil838/qfil.git
cd qfil
uv sync
```

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
