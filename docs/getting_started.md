# Getting started

## Installation

```bash
git clone https://github.com/zerotonin/ThermoFooty.git
cd ThermoFooty
pip install -e ".[all]"
```

Python ≥ 3.11 required (meteostat 2.x dropped 3.10 support).

## Data root

ThermoFooty keeps the entire data tree off-repo on a fast NVMe.
On Bart's workstation that's `/media/geuba03p/DATADRIVE1/ThermoFooty/`,
exposed through the gitignored `data/` symlink. On any other machine,
set `THERMOFOOTY_DATA_ROOT` before running the code:

```bash
export THERMOFOOTY_DATA_ROOT=/path/to/your/data/root
ln -sf "$THERMOFOOTY_DATA_ROOT" data
```

`thermofooty.config.assert_data_root_ready()` verifies the root
exists and is writeable; CLI scripts call this at startup so a
missing mount fails loudly rather than silently degrading.

## Optional API keys

For the ERA5 weather fallback tier you need a free
[Copernicus CDS API key](https://cds.climate.copernicus.eu/api-how-to)
dropped at `~/.cdsapirc` (gitignored).

The Tier 1 (meteostat) and Tier 4 (NOAA 20CRv3) sources require no
credentials.

## Verify the install

```bash
python -c "import thermofooty; print(thermofooty.__version__)"
pytest                   # runs the fast scaffold smoke tests
pytest -m network        # opt-in: hits live upstreams (long)
```

## Next steps

The runnable pipeline lands in Phases 2–5 of the dev plan
(`~/PyProjects/ThermoFooty_DEV_PLAN.md`). See {doc}`pipeline` for
the staged build-out.
