"""Copy of the helper cell of DOFSTA_gain.ipynb and the imports of its first cell. Regression reference in the tests."""
import pandas as pd
import numpy as np
import xarray as xr
import re
from io import StringIO

# Helper functions

def read_ctd_cnv(path):
    with open(path, 'r') as f:
        lines = f.readlines()
    for i, line in enumerate(lines):
        if line.strip().startswith('*END*'):
            data_start = i + 1
            break
    column_names = []
    for line in lines:
        if line.strip().startswith('# name'):
            match = re.search(r'# name \d+ = ([^:]+)', line)
            if match:
                column_names.append(match.group(1).strip())
    data_str = ''.join(lines[data_start:])
    df = pd.read_csv(StringIO(data_str), sep=r'\s+', header=None)
    df.columns = column_names[:df.shape[1]]
    return df

def calibrate_ctd_to_winkler(df_wink, df_ctd, depth_col='prDM', o2_col='sbox0Mm/Kg'):
    # Gain-only correction (Bittig et al. 2018): interpolate CTD O2 onto Winkler depths,
    # take the median ratio as a single multiplicative gain, apply across the full cast
    ctd_sorted = df_ctd.sort_values(depth_col)
    ctd_o2_at_wink = np.interp(df_wink['Depth_wink'], ctd_sorted[depth_col], ctd_sorted[o2_col])
    gain = np.median(df_wink['O2_wink'] / ctd_o2_at_wink)
    df_ctd = df_ctd.copy()
    df_ctd['O2_calibrated'] = df_ctd[o2_col] * gain
    return gain, df_ctd

def load_dofsta_ascending(nc_path, bin_size=1):
    ds = xr.open_dataset(nc_path).load()
    sample_dim = ds['depth'].dims[0]
    is_ascending = ds['depth'].diff(sample_dim) < 0
    ascending_data = ds.isel({sample_dim: slice(1, None)}).where(is_ascending, drop=True)
    depth_bins = np.arange(0, ascending_data['depth'].max().item() + bin_size, bin_size)
    binned = ascending_data.groupby_bins('depth', bins=depth_bins).mean()
    binned = binned.assign_coords(depth_bins=[interval.mid for interval in binned['depth_bins'].values])
    return ascending_data, binned

def dofsta_gain(dofsta_binned, ctd_calibrated, depth_col='prDM'):
    # Match DOFSTA depth bins to the Winkler-calibrated CTD profile, take median ratio as gain
    ctd_sorted = ctd_calibrated.sort_values(depth_col)
    ctd_at_dofsta = np.interp(dofsta_binned['depth_bins'], ctd_sorted[depth_col], ctd_sorted['O2_calibrated'])
    ratio = ctd_at_dofsta / dofsta_binned['corrected_dissolved_oxygen'].values
    return float(np.nanmedian(ratio))

