# Shallow-profiler sensor calibration

This project checks and corrects three sensors on the OOI Regional Cabled Array shallow profilers by comparing them with the
measurements taken by the ship on each yearly maintenance cruise (2020-2025):

| Sensor | What it measures | What we compare it with | What we produce |
|---|---|---|---|
| CTD (CTDPFA) | salinity | the ship's CTD, itself checked against bottle salinity | an offset to add, and a conductivity slope |
| Oxygen (DOFSTA) | dissolved oxygen | the ship's oxygen sensor, itself checked against Winkler bottles | a gain to multiply by |
| Nitrate (SUNA, NUTNR) | nitrate | nitrate measured in the bottles | an offset to add |

It covers three sites: **Axial Base** (`RS03AXPS`), **Slope Base** (`RS01SBPS`) and **Oregon Offshore** (`CE04OSPS`).

Every number comes from the data files listed in `config/cruises.toml`. If a file is missing, that run stops and says which file.

## Quick start

You need Python 3.12 and [Git LFS](https://git-lfs.com) (two data files are larger than 100 MB).

```
git lfs install && git lfs pull
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt     # macOS/Linux: .venv/bin/python
```

Look at the results in the browser:

```
python -m streamlit run app.py
```

Recompute everything (no OOI login needed, all downloaded data is in the repository):

```
python scripts/run_all.py
```

Or one run at a time, for example Slope Base oxygen in 2025:

```
python scripts/run_year.py --year 2025 --site RS01SBPS --variable oxygen      # --variable salinity | oxygen | nitrate
```

Run the tests with `python -m pytest tests`.

## The viewer

The app has one page per comparison. Pick a run (year and site) in the sidebar; the variant picker is at the top of each page.

- **Deployments**: one line per instrument and deployment, with the correction and a status from 1 (good) to 4 (not recommended).
- **Salinity**: the ship's CTD against the bottle salinities, then the profiler against the ship's CTD.
- **Oxygen**: the ship's oxygen sensor against the Winkler bottles, then the profiler against the corrected ship sensor.
- **Nitrate**: the profiler's nitrate against the bottle nitrate.
- **Run your files**: upload your own files and run the same calculation.
- **Provenance and export**: every input file with its checksum, and downloads of all results.

Each page starts with the answer in one sentence ("add +2.01 µmol/L") and the deployment status, then the numbers grouped by method, the flags (collapsed, with a count of warnings), then the plots. Per-upcast plots and full tables are in tabs below the main plot.

## How each correction is worked out

### Salinity

1. Check the ship's CTD against the bottle salinities (measured on an Autosal). Bottles that disagree with the rest by more than 0.01 PSU are flagged.
2. Take the first 6 complete profiler upcasts after the ship's cast (from the official profile list).
3. Average the profiler salinity in 1 m depth bins and compare it with the ship's CTD at the same depths.
4. The **offset** is the median difference: `S_corrected = S_raw - median(profiler - ship CTD)`.
5. As an alternative, a **conductivity slope** `m` is fitted (Sea-Bird note AN31): `C_corrected = m × C_raw`, then salinity is recomputed.

### Oxygen

1. Calibrate the ship's oxygen sensor with the Winkler bottles: `g_ctd = median(Winkler / ship sensor)`.
2. Compare the profiler oxygen with the calibrated ship sensor, depth by depth: `g = median(calibrated ship sensor / profiler)`.
3. **Corrected oxygen = g × raw oxygen.**

Winkler values in mL/L are converted to µmol/kg with `× 44660 / (σθ + 1000)`, using the ship's CTD at each bottle.

### Nitrate

This follows chapter 3 of the *OOI Biogeochemical Sensor Data: Best Practices & User Guide* (Palevsky, Clayton et al., 2023).

1. Use OOI's processed nitrate (`salinity_corrected_nitrate`). The nitrate sensor only samples on two profiles a day, so we download
   36 hours either side of the ship's cast.
2. Clean it: drop data OOI has marked for exclusion and remove single spikes.
3. Pick the full profile closest in time to the cast.
4. Match each bottle to the sensor at the **same temperature** (the guide's recommendation), or at the same depth if no temperature matches.
5. Leave out any bottle that disagrees with the rest by more than 10 µmol/L (a bad sample).
6. The **offset** is the median difference: `NO3_corrected = NO3_sensor + median(bottle - sensor)`.
   The sensor's stated accuracy is ±2 µmol/L, so a smaller offset means no correction is really needed.

If the profiler was parked during the whole cruise window, there is no full profile; the parked reading is compared with the bottles at its
depth as a single point, and the page says so.

### What is not done yet

- Each correction is one value per cruise. Drift during a deployment is not corrected (records say `T2: missing`).
- Oxygen sensor response time is not corrected.
- Nitrate interference from dissolved organic matter is not corrected.
- OOI's automatic quality flags (QARTOD) are not used; OOI's manual annotations are.

## How the status is decided

Every result goes through a set of checks (for example: did the correction reduce the error? how many bottles were used? how far apart in
time were the ship and the profiler?). Each check passes, is marginal, or fails against a target and a limit set in `[deployments]` in
the config. The worst check sets the status:

1. good
2. minor issues
3. use with caution
4. not recommended

The full list is in `outputs/deployments.yaml`. The targets are provisional.

## Where things are

| Folder or file | What it holds |
|---|---|
| `config/cruises.toml` | which files each run uses, and all settings and thresholds |
| `data/raw/ooinet/` | profiler data downloaded from OOINet, kept exactly as downloaded |
| `outputs/<run>/` | results for one year and site (`oxygen/` and `nitrate/` subfolders for those sensors) |
| `outputs/deployments.yaml` | summary of every instrument and deployment |
| `rca_sal/` | the calculation code |
| `scripts/` | command-line tools (below) |
| `app.py`, `app_pages/` | the viewer |
| `tests/` | automated tests |

A run is named by its year for Axial Base (`2025`) and by year and site for the others (`2025-RS01SBPS`).

Each result folder has a `calibration_record.json` that lists every input file with its SHA-256 checksum and source (a GitHub link at a
fixed commit, or a path in this repository), the settings used, and the code version.

## Where the data come from

- **Profiler data**: OOINet. Downloading new data needs an OOI account, read from the environment variables `OOI_API_USERNAME` and
  `OOI_API_TOKEN`. Requests are sent one at a time.
- **Ship data** (CTD casts, bottle salinity, Winkler oxygen, nutrients): [OOI-CabledArray/discreteSummaries](https://github.com/OOI-CabledArray/discreteSummaries).
- **Profile start and end times**: [OOI-CabledArray/profileIndices](https://github.com/OOI-CabledArray/profileIndices).
- **Instrument deployment times and positions**: [oceanobservatories/asset-management](https://github.com/oceanobservatories/asset-management).

GitHub files are read at the fixed commits named in the config and cached in `data/cache/github/`.

## Other tools

| Command | What it does |
|---|---|
| `python scripts/fetch_profiler.py --year 2024 --site RS01SBPS --variable nitrate` | download profiler data around a cruise cast |
| `python scripts/fetch_profiler.py --resume` | save downloads that OOINet finished later |
| `python scripts/deployments.py` | rebuild `outputs/deployments.yaml` |
| `python scripts/instrument_inventory.py` | list every CTD and oxygen instrument that has bottle samples nearby |
| `python scripts/fetch_seafloor.py`, `seafloor_calibrate.py` | first look at the seafloor CTDs (Axial Base and Slope Base) |

## Known quirks in the data

- The ship's CTD files were processed with `datcnv` only, and contain the downcast only.
- Some years have only a few bottles (two or three for oxygen).
- In some years the profiler was parked during the cruise, so nitrate could only be compared at one depth.
- The 2024 and 2025 summaries have salinity and nitrate values without quality flags; they are used and marked "unflagged".
- The 2025 bottle sheet has one clearly wrong nitrate value (Niskin 9, cast CTD-007), which is left out automatically.
- OOI lists the Oregon Offshore profiler range as 20-200 m, but the data reach 4-5 m.

## Reference

Palevsky, H. I., Clayton, S., et al. (2023). *OOI Biogeochemical Sensor Data: Best Practices & User Guide*, version 1.1.1.
https://doi.org/10.25607/OBP-1865.2
