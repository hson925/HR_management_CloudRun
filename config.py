import os

# ============================================================================
# Public-mirror config — real Sheet IDs / campus codes redacted.
# See README for how to wire your own values via env / config override.
# ============================================================================

SHEET_CONFIG = {
    'ROSTER_ID':  'REDACTED_ROSTER_SHEET_ID',
    'EMP_DB_ID':  'REDACTED_EMP_DB_SHEET_ID',
    'NT_INFO_ID': 'REDACTED_EMP_DB_SHEET_ID',
    'NT_SHEETS':  ['main', 'sub', 'rnd', 'brand_x', 'brand_y', 'brand_z'],
    'RETIRE_ID':  'REDACTED_RETIRE_SHEET_ID',
    'RETIRE_START_YEAR': 2024,
}

# Campus codes anonymized for the public mirror (CMA..CMN)
CAMPUS_AUTH_CODES = {
    'CMA': 'Campus A', 'CMB': 'Campus B', 'CMC': 'Campus C', 'CMD': 'Campus D',
    'CME': 'Campus E', 'CMF': 'Campus F', 'CMG': 'Campus G', 'CMH': 'Campus H',
    'CMI': 'Campus I', 'CMJ': 'Campus J', 'CMK': 'Campus K', 'CML': 'Campus L',
    'CMM': 'Campus M', 'SUB': 'SUB',
}

SERVICE_ACCOUNT_FILE = 'service-account-key.json'
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive',
]
