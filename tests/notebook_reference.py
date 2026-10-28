"""Copy of cells 1 and 3 of CTDPFA_salinity_correction.ipynb (matplotlib import removed). Regression reference in the tests."""
import pandas as pd
import numpy as np
import xarray as xr
import re
from io import StringIO

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

def load_ctdpfa_ascending(nc_path, bin_size=1, smooth_window=5):
    ds = xr.open_dataset(nc_path).load()
    sample_dim = ds['depth'].dims[0]
    is_ascending = ds['depth'].diff(sample_dim) < 0
    ascending_data = ds.isel({sample_dim: slice(1, None)}).where(is_ascending, drop=True)
    depth_bins = np.arange(0, ascending_data['depth'].max().item() + bin_size, bin_size)
    binned = ascending_data.groupby_bins('depth', bins=depth_bins).mean()
    binned = binned.assign_coords(depth_bins=[interval.mid for interval in binned['depth_bins'].values])
    binned['sea_water_practical_salinity'] = binned['sea_water_practical_salinity'].rolling(
        depth_bins=smooth_window, center=True, min_periods=1
    ).mean()
    return ascending_data, binned

def salinity_offset(sensor_binned, df_ctd, depth_col='prDM', sal_col='sal00'):
    # Median (sensor - shipboard CTD) across depth bins -- robust to outliers,
    # and additive since the bias doesn't scale with the salinity value itself
    ctd_sorted = df_ctd.sort_values(depth_col)
    ctd_at_bins = np.interp(sensor_binned['depth_bins'], ctd_sorted[depth_col], ctd_sorted[sal_col])
    diff = sensor_binned['sea_water_practical_salinity'].values - ctd_at_bins
    valid = ~np.isnan(diff)
    offset = float(np.nanmedian(diff))
    iqr = float(np.nanpercentile(diff[valid], 75) - np.nanpercentile(diff[valid], 25))
    rmse = float(np.sqrt(np.nanmean(diff[valid] ** 2)))
    return offset, iqr, rmse

