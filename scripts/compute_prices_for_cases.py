#!/usr/bin/python3

import pandas as pd
import numpy as np

cases = pd.read_csv('data/PC_Specs - Cases.csv', index_col=0)
cpu_table = pd.read_csv('data/PC_Specs - CPU Table.csv', index_col=0)
gpu_table = pd.read_csv('data/PC_Specs - GPU Table.csv', index_col=0)
hdd_table = pd.read_csv('data/PC_Specs - HDD Table.csv', index_col=0)
ssd_table = pd.read_csv('data/PC_Specs - SSD Table.csv', index_col=0)
ram_table = pd.read_csv('data/PC_Specs - RAM Table.csv', index_col=0)
opdr_table = pd.read_csv('data/PC_Specs - Optical Drive Table.csv', index_col=0)

cases.columns

cpu_prices = cases['CPU'].map(cpu_table['MSRP'].to_dict()).astype(float)
gpu_prices = cases['GPU'].map(gpu_table['MSRP'].to_dict()).astype(float)
ram_prices = cases['RAM (GB)'].map(ram_table['Price'].to_dict()).astype(float)
ssd_prices = cases['SSD (GB)'].map(ssd_table['Price'].to_dict()).astype(float)
hdd_prices = cases['HDD (GB)'].map(hdd_table['Price'].to_dict()).astype(float)
opdr_prices = cases['Optical Drive (1 = DVD, 0 = None)'].map(opdr_table['Price'].to_dict()).astype(float)

df = pd.DataFrame([cpu_prices, gpu_prices, ram_prices, ssd_prices, hdd_prices, opdr_prices]).T
cases['Price (€)'] = df.sum(axis=1)

cases.round(decimals=2)

cases.to_csv('data/new_prices.csv')
