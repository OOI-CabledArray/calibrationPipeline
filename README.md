# Shallow-profiler salinity and oxygen calibration

Calibration of the CTDPFA salinity and DOFSTA oxygen of the OOI Regional Cabled Array shallow profilers (Axial Base `RS03AXPS`,
Slope Base `RS01SBPS`, Oregon Offshore `CE04OSPS`) against the shipboard CTD and discrete samples of the deployment cruises, 2020-2025.
For each run (year and site) the pipeline computes an additive salinity offset, the Sea-Bird AN31 conductivity slope and a multiplicative
oxygen gain, and writes the result with the SHA-256 of every input.

Every quantity is computed from the files named in `config/cruises.toml`. If a configured input is missing, the run stops and writes
`outputs/<run>/missing.json`.

## Contents

| path | content |
|---|---|
| `rca_sal/` | pipeline package (readers, PSS-78 / EOS-80, calibration, oxygen, provenance, deployment summary) |
| `scripts/` | `run_year.py`, `run_all.py`, `deployments.py`, `fetch_profiler.py` (OOINet requests), `site_casts.py`, `site_gaps.py`, `github_inventory.py` |
| `config/cruises.toml` | parameters, variants, inputs and thresholds for every run |
| `data/raw/ooinet/<timestamp>/` | OOINet request folders, stored as downloaded |
| `outputs/<run>/` | calibration outputs; `outputs/deployments.yaml` and `.json`: summary of all runs |
| `app.py`, `app_lib.py`, `app_pages/` | Streamlit viewer |
| `tests/` | pytest suite |

A run is keyed `<year>` for the default site (Axial Base) and `<year>-<site>` otherwise, for example `2025-RS01SBPS`. Oxygen outputs are in
`outputs/<run>/oxygen/`.

## Installation

Python 3.12.

```
python -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt        # Windows: .venv\Scripts\python
git lfs install && git lfs pull                                 # two NetCDF files above 100 MB are stored with Git LFS
```

## Reproducing the outputs

```
python scripts/run_all.py          # all runs in the config, then outputs/deployments.yaml and .json
python scripts/run_year.py --year 2025 --site RS01SBPS --variable oxygen      # one run
python -m pytest tests
python -m streamlit run app.py
```

- The OOINet files used by every run are in `data/raw/ooinet/`; OOINet credentials are not needed to reproduce.
- Shipboard files, Autosal workbooks, cast logs, discrete summaries and profile indices are read from two GitHub repositories at the
  commits fixed by `ref` in the config (`[github]`, `[github_sources.profile_indices]`). They are downloaded on first use to
  `data/cache/github/<commit>/` and verified against the git blob SHA-1 of the repository tree.
- Records contain the run time and the git commit of the code, so a regenerated record differs from the committed one in those fields.

## Data

**OOINet** (M2M asynchronous requests, `rca_sal/ooinet.py`). CTDPFA from `streamed/ctdpf_sbe43_sample`, DOFSTA from
`streamed/do_fast_sample`. The DOFSTA data returned with a CTDPFA request have no depth coordinate, so the DOFSTA file of a run is a
separate request over the time span of its CTDPFA file (`scripts/fetch_profiler.py --variable oxygen --match-profiler`). Credentials are
read from `OOI_API_USERNAME` and `OOI_API_TOKEN`. Each request folder is stored unchanged with its status, provenance and annotation files.

**GitHub.**
- [OOI-CabledArray/discreteSummaries](https://github.com/OOI-CabledArray/discreteSummaries): shipboard CTD `.cnv` and `.btl`, Autosal
  workbook, cast log, discrete summary CSV per cruise, Winkler workbook (2025).
- [OOI-CabledArray/profileIndices](https://github.com/OOI-CabledArray/profileIndices): start, peak and end time of every shallow-profiler profile.

An input written `"github:<path>"` or `"github[profile_indices]:<path>"` in the config is resolved by `rca_sal/github_data.py`.

## Method

Notation: $s_k$ binned profiler value, $r_k$ reference value, $z_k$ bin mid-point depth, $k$ the depth bin.

### CTD against Autosal (reference check)

For each Niskin $i$: $d_i = S^{ctd}_i - S^{bottle}_i$, with $S^{ctd}_i$ the CTD salinity averaged over the bottle closure (`.btl`) and
$S^{bottle}_i$ the Autosal salinity of the sample. The record gives median, mean, IQR and RMSE of $d$ for `Sal00` and `Sal11`, and the AN31
slope of the bottles. A bottle is flagged when $|d_i - \operatorname{median}(d)| > 0.01$ PSU (`bottle_outlier_psu`). Bottles whose Niskin or
discrete salinity flag marks them unusable are excluded from the statistics.

### Profiler sample selection

- **Own upcast labelling** (`rca_sal.profiler.label_upcasts`): depth is smoothed with a 15 s rolling median, $w = dz/dt$; a sample is
  ascending if $w < -0.03$ m/s; ascending samples more than 30 s apart start a new run; a run is an upcast if its depth span is at least 100 m.
- **Official profile index:** samples with $t_{start} \le t \le t_{peak}$ of a profile form that profile's upcast.
- **Primary variant:** the first 6 upcasts of the index that start at or after the CTD cast start (NMEA time in the `.cnv` header) and span at
  least 100 m, pooled. The run stops with an error if fewer exist. Runs without a profile index (uploads) apply the same rule to the labelled upcasts.

### Reference CTD

The downcast is the scans from $p \ge 2$ dbar to the pressure maximum, excluding scans more than 0.5 dbar shallower than the running
maximum (ship heave). The reference is interpolated linearly in depth at $z_k$. With `extrapolate_reference = false` (primary) bins above the
shallowest or below the deepest reference depth are excluded; with `true` the reference is held constant there (`numpy.interp`).

### Salinity offset

Profiler samples are averaged in bins $(a, b]$ of 1 m labelled by the mid-point; salinity is smoothed with a centred rolling mean of 5 bins.

$$\Delta S = \operatorname{median}_k (s_k - r_k), \qquad S_{corr} = S_{raw} - \Delta S$$

Reported with $\mathrm{IQR}_k(s_k - r_k)$ and $\mathrm{RMSE} = \sqrt{\overline{(s_k - r_k)^2}}$ before and after the correction.

### AN31 conductivity slope

$\alpha_k$ is the binned profiler conductivity and $\beta_k = C(r_k, T_k, p_k)$ the conductivity of the reference salinity at the profiler
temperature and pressure (inverse of PSS-78, `rca_sal/pss78.py`).

$$m = \frac{\sum_k \alpha_k \beta_k}{\sum_k \alpha_k^2}, \qquad C_{corr} = m\,C_{raw}, \qquad S_{corr} = S_{PSS78}(C_{corr}, T, p)$$

The corrected salinity is smoothed like the offset path and both RMSEs are computed on the same bins.

### Oxygen gain

Winkler oxygen $W_i$ at depth $z_i$ of Niskin $i$, shipboard SBE43 oxygen $O^{ctd}(z)$, binned DOFSTA oxygen $O^{dof}_k$:

$$g_{ctd} = \operatorname{median}_i \frac{W_i}{O^{ctd}(z_i)}, \qquad g_{dof} = \operatorname{median}_k \frac{g_{ctd}\,O^{ctd}(z_k)}{O^{dof}_k}, \qquad O^{dof}_{corr} = g_{dof}\,O^{dof}_{raw}$$

$g_{dof}$ is relative to the calibrated CTD, so it is the total gain from raw DOFSTA to the Winkler scale. As a check, the gain of DOFSTA
directly against the Winkler values is $\operatorname{median}_i (W_i / O^{dof}(z_i))$.

Winkler values come from the cruise discrete summary (`discrete_summary_csv`): oxygen in mL/L is converted with the CTD salinity, temperature
and pressure at the bottle closure:

$$O_2\,[\mu\mathrm{mol/kg}] = O_2\,[\mathrm{mL/L}] \cdot \frac{44660}{\sigma_\theta + 1000}, \qquad \sigma_\theta = \rho(S, \theta, 0\ \mathrm{dbar}) - 1000$$

with EOS-80 density (`rca_sal/eos80.py`). Summary flags (16 bits, bit 0 rightmost): oxygen bit 4 gives bottle flag 4, oxygen bit 3 or a Niskin
flag in bits 3-7 gives 3, otherwise 2; flags 3 and 4 are excluded except in the variants that reproduce the reference notebook. The 2025 Winkler
workbook (`bottles = "workbook"`) is kept for comparison: its umol/kg column uses salinity 0 in the density for bottles without a salinity
sample (Niskins 7, 14, 16, 18, 21 of CTD-007), about 2.5 % high for those bottles.

### Times

$t_{cast}$ is the midpoint of the `.cnv` NMEA start time and the last bottle closure. $t_{prof}$ is the mean time of the profiler samples
used. $|t_{prof} - t_{cast}|$ is reported as `time_gap_h`.

### Time dependence

The correction is a single value per cruise. `rca_sal.correction.interpolate_value` interpolates linearly in time between two cruises, but
no second value is computed; records state `T2: missing`.

### OOI annotations

Annotations delivered with each OOINet request that cover the samples used are listed in the record. Samples inside an applicable annotation
with `exclusion_flag` set are dropped from every variant. The automated QARTOD flags are not used.

## Variants

| id | role | profiler samples | reference |
|---|---|---|---|
| `index_first_upcasts_depSM` | primary | first 6 complete index upcasts after the cast | downcast, `depSM`, `sal00` |
| `index_first_upcasts_depSM_sal11` | sensitivity | as primary | `sal11` |
| `first_upcasts_depSM` | alternative | first 6 complete labelled upcasts | downcast, `depSM`, `sal00` |
| `nearest_upcast_depSM`, `_sal11` | alternative | upcast nearest the cast | downcast, `depSM` |
| `upcasts_all_prDM`, `upcasts_all_depSM` | alternative | all labelled upcasts | downcast, `prDM` / `depSM` |
| `mask_fullday_prDM` | alternative | $\Delta z < 0$ mask over the requested day | all scans, `prDM` |
| `notebook_window_ascending_prDM` | regression | notebook file window, $\Delta z < 0$ mask | all scans, `prDM`, extrapolating |
| `upcast_k_depSM` | per upcast | upcast $k$ | downcast, `depSM` |

Oxygen variants follow the same selection rules and add the Winkler source (`summary` or `workbook`) and the depth column used to match
Winkler bottles to the CTD profile.

## Outputs

| file | content |
|---|---|
| `calibration_record.json` | JSON-LD record (schema.org, PROV-O): inputs with SHA-256, size and modification time; request metadata; parameters; code and config hashes; git commit; checks; variants; flags |
| `calibration_table.csv`, `.parquet` | one row per variant, with record id and input hashes |
| `binned_profiles.csv` | raw, corrected and reference value per depth bin and variant |
| `bottle_check.csv`, `reference_ctd_profile.csv`, `upcasts.csv` | salinity supporting tables |
| `winkler_check.csv`, `ctd_o2_profiles.csv`, `upcasts.csv` | oxygen supporting tables |

Inputs are named by their raw GitHub URL at the commit read (files from GitHub), by their path in this repository, or `uploaded/<role>/<name>`.

## Deployment summary

`outputs/deployments.yaml` (and `.json`) lists, per instrument and deployment: flat offset, conductivity slope or gain, the upcasts and times
used, and a graded status. Each check has a `target` and a `limit` (`[deployments]` in the config): `pass` if the value meets the target,
`marginal` between target and limit, `fail` beyond the limit. The severity of the check maps `marginal` and `fail` to a status level; the
deployment level is the maximum over its checks: 1 good, 2 minor issues, 3 use with caution, 4 not recommended. Every check carries the
booleans `assessed`, `passed`, `within_limit` and `sets_status`; each status carries `all_checks_pass`, the counts of each outcome and
`set_by_checks`. Targets, limits and severities are provisional.

Salinity checks: RMSE reduction, CTD-Autosal median, difference to the single-upcast result, IQR, sensor agreement, time gap.
Oxygen checks: RMSE reduction, chain against direct gain, difference to the single-upcast gain, gain IQR, $|g - 1|$, number of Winkler
bottles, time gap.

## OOINet requests

```
python scripts/fetch_profiler.py --year 2024 --site RS01SBPS --from-index 6     # CTDPFA: the first 6 profiles after the cast
python scripts/fetch_profiler.py --year 2024 --site RS01SBPS --variable oxygen --match-profiler
python scripts/fetch_profiler.py --resume                                       # save requests OOINet has completed since
```

The script prints the config lines to add and does not edit the config. Requests are submitted one at a time; pending requests are kept
in `data/raw/ooinet/pending_requests.json`.

## Viewer and uploaded files

`streamlit run app.py` shows the outputs of every run. The **Run your files** page runs the same pipelines on uploaded files: salinity needs
the profiler CTDPFA NetCDF, shipboard `.cnv` and `.btl`, the Autosal workbook and the cast log; oxygen needs the DOFSTA NetCDF, the Winkler
workbook and the same `.cnv` and `.btl`. Parameters and variants are those of `[upload] template_year`. Files are saved in a per-session
temporary workspace and are limited by `max_file_mb`.

## Limitations

- The reference `.cnv` holds the downcast only and was processed with `datcnv` only (no `alignctd`, `celltm`, `loopedit`); bottle averages
  are taken during the upcast.
- The comparison is by depth, with the profiler between hours and days away from the cast (`time_gap_h`).
- Oxygen sensor response time is not corrected.
- Salinity offsets and oxygen gains are single values per cruise; drift during the deployment is not estimated.
- Salinity casts have four to eight bottles; some oxygen casts have two or three.
- The shallowest profiler samples used in some runs are at 38-40 m (Oregon Offshore 2024, Slope Base 2023).
- OOI lists the Oregon Offshore shallow profiler range as 20-200 m; the data reach 4-5 m. Axial Base and Slope Base are listed as 5-200 m.
- The 2025 Autosal workbook has a header date of 2025-04-20 and a cell reading `2024 Salts`; both are flagged in the record and not changed.

## Tests

`python -m pytest tests` runs the unit tests (PSS-78 and EOS-80 check values, binning, upcast labelling, flag decoding), the regression tests
that reproduce the reference notebooks for 2025, end-to-end runs into a temporary directory, record consistency checks (hashes, no machine
paths) and the viewer pages. Tests that need a GitHub file skip if it is unavailable.

## Reference

Palevsky, H. I., Clayton, S., et al. (2023), OOI Biogeochemical Sensor Data: Best Practices & User Guide, version 1.1.1, doi:10.25607/OBP-1865.2.
