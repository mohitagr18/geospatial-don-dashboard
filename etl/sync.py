"""
etl/sync.py  ─  Automatic Excel → SQLite sync with incremental geocoding
═════════════════════════════════════════════════════════════════════════
Designed for non-technical users.  The DON only needs to:

    1.  Export updated CustomerData.xlsx and CaregiverData.xlsx into  data/
    2.  Start (or refresh) the Streamlit app

This module will:
    • Read both Excel files
    • Compare every row against the existing DB using a stable surrogate key
    • Geocode ONLY new or address-changed records (via Geocodio)
    • Update all other fields (hours, status, etc.) from the latest Excel
    • Remove DB rows that no longer appear in the Excel files
    • Rebuild the vw_staff_capacity view

Privacy:  Only address strings are ever sent to the Geocodio API.
          Names, phones, and all other PII stay on the local machine.
"""

from __future__ import annotations

import os
import sqlite3
import warnings
from dataclasses import dataclass, field

import pandas as pd
import requests
from dotenv import load_dotenv

# ─── CONFIGURATION ────────────────────────────────────────────────────────────
# File paths — these match the agency system's export filenames exactly,
# so the DON can drop them into data/ without renaming anything.
DB_PATH           = "data/staffing_engine.db"
CLIENTS_EXCEL     = "data/CustomerData.xlsx"
STAFF_EXCEL       = "data/CaregiverData.xlsx"
GEOCODIO_ENDPOINT = "https://api.geocod.io/v1.7/geocode"
BATCH_SIZE        = 10_000

# Columns used to build the geocodable address string
# These are the ONLY values sent to the Geocodio API  — no PII.
ADDRESS_COLS = ["Address 1", "Address 2", "City", "State", "Zip"]

# ─── SCHEMA DEFINITION: REQUIRED vs OPTIONAL ─────────────────────────────────
# Required columns MUST exist in the Excel export.  If missing, the ETL aborts
# with a friendly error message listing exactly which columns are absent.
#
# Optional columns are used if present.  If missing, a safe default (None / NaN)
# is filled in automatically, and a warning is surfaced in the UI.
# Extra columns in the Excel that are not listed here are silently ignored.

REQUIRED_CLIENT_COLS = ["First Name", "Last Name", "Address 1", "City", "State", "Zip"]
OPTIONAL_CLIENT_COLS = ["Address 2", "Phone", "Gender", "Class", "Birth Date"]

REQUIRED_STAFF_COLS  = ["First Name", "Last Name", "Address 1", "City", "State", "Zip"]
OPTIONAL_STAFF_COLS  = ["Address 2", "Mobile", "Gender", "Status", "Hire Date",
                        "Birth Date", "Skills", "Weekly Max Hours", "Daily Max Hours"]

# ─── EXCLUSION LISTS ─────────────────────────────────────────────────────────────
# Add "First Last" names here to permanently exclude them from the dashboard.
# These rows will be dropped every time the Excel files are synced, even if
# they appear in the agency export (e.g., test accounts, training records).
#
# ▸  To add a name, just append it to the list:  "Jane Doe"
# ▸  Matching is case-insensitive.
# ▸  The name must match  "First Name" + " " + "Last Name"  from the Excel.

EXCLUDED_CLIENT_NAMES: list[str] = [
    # Example:  "Test Client",
]

EXCLUDED_STAFF_NAMES: list[str] = [
    "Pragya Chaurasia", "Divy Chaurasia", "Tierra Flowers",
    "Lakisha Rose", "Mohit Aggarwal", "Marquise Lane"
]


# ─── RESULT CONTAINER ─────────────────────────────────────────────────────────

@dataclass
class SyncResult:
    """Returned by sync_db_from_excels(); the app uses this for status messages."""
    ok: bool = True
    error: str = ""

    total_clients: int = 0
    total_staff: int = 0
    new_geocoded: int = 0       # addresses sent to Geocodio
    addr_updated: int = 0       # existing rows whose address changed → re-geocoded
    fields_updated: int = 0     # existing rows refreshed (non-address fields)
    clients_removed: int = 0
    staff_removed: int = 0
    geocode_failures: list = field(default_factory=list)  # address strings that failed
    missing_optional: dict = field(default_factory=dict)  # {"Clients": [...], "Staff": [...]}


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def _build_address(*parts) -> str:
    """Join non-null address parts into a single comma-separated string."""
    return ", ".join(str(p).strip() for p in parts if pd.notna(p) and str(p).strip())


def _addr_for_row(row: pd.Series) -> str:
    """
    Build a geocodable address string from a row's address columns.
    Handles both Excel names ('Address 1') and DB names ('Address').
    """
    parts = []
    for col in ADDRESS_COLS:
        if col in row.index:
            parts.append(row[col])
        else:
            # Try DB-style alias:  'Address 1' → 'Address', 'Address 2' → 'Address2'
            alias = col.replace(" 1", "").replace(" 2", "2")
            if alias in row.index:
                parts.append(row[alias])
    return _build_address(*parts)


def _surrogate_key(row: pd.Series) -> str:
    """
    Stable surrogate key = lower(first_name|last_name|full_address).
    We can't rely on Client_ID / Staff_ID because the source Excel doesn't
    contain them — they are assigned by us.  Name+Address is stable enough
    to detect same-person across refreshes.
    """
    fname = str(row.get("First Name", "")).strip().lower()
    lname = str(row.get("Last Name", "")).strip().lower()
    addr  = _addr_for_row(row).lower()
    return f"{fname}|{lname}|{addr}"


def _surrogate_key_name_only(row: pd.Series) -> str:
    """Key using only name (for detecting address changes on existing people)."""
    fname = str(row.get("First Name", "")).strip().lower()
    lname = str(row.get("Last Name", "")).strip().lower()
    return f"{fname}|{lname}"


def _read_excel(path: str) -> pd.DataFrame:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = pd.read_excel(path, header=0)
    df.columns = df.columns.str.strip()
    return df.dropna(how="all").reset_index(drop=True)


def _check_required_cols(df: pd.DataFrame, required: list, label: str) -> str | None:
    """Return a friendly error string if any required column is missing."""
    missing = [c for c in required if c not in df.columns]
    if missing:
        return (
            f"The {label} spreadsheet is missing required column(s): "
            f"**{', '.join(missing)}**.\n\n"
            f"Found columns: {', '.join(df.columns)}.\n\n"
            f"Please check the export from the agency system."
        )
    return None


def _fill_optional_cols(df: pd.DataFrame, optional: list, label: str) -> list[str]:
    """
    For each optional column: if missing from the dataframe, add it with NaN.
    Returns a list of the column names that were missing (for user warning).
    """
    missing = []
    for col in optional:
        if col not in df.columns:
            df[col] = None
            missing.append(col)
    if missing:
        print(f"  ⚠ {label}: optional column(s) missing and defaulted to N/A: {missing}")
    return missing


def _select_known_cols(df: pd.DataFrame, required: list, optional: list,
                       extra_keep: list | None = None) -> pd.DataFrame:
    """
    Return only the columns the app knows about (required + optional + extra_keep).
    Extra/unknown columns from the Excel are silently dropped so they never
    break the DB schema or the Streamlit code.
    """
    known = set(required + optional + (extra_keep or []))
    keep = [c for c in df.columns if c in known]
    return df[keep]


def _map_role(skill) -> str:
    if pd.isna(skill):
        return "PCA"
    s = str(skill).strip().upper()
    return s if s in ("LPN", "RN") else "PCA"


# ─── GEOCODING ────────────────────────────────────────────────────────────────

def _ensure_cache_table(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS geocode_cache (
            address   TEXT PRIMARY KEY,
            latitude  REAL,
            longitude REAL
        )
    """)
    conn.commit()


def _load_cache(conn: sqlite3.Connection) -> dict:
    _ensure_cache_table(conn)
    rows = conn.execute("SELECT address, latitude, longitude FROM geocode_cache").fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


def _save_cache(conn: sqlite3.Connection, entries: dict):
    conn.executemany(
        "INSERT OR REPLACE INTO geocode_cache (address, latitude, longitude) VALUES (?, ?, ?)",
        [(addr, lat, lng) for addr, (lat, lng) in entries.items()],
    )
    conn.commit()


def _batch_geocode(addresses: list, api_key: str) -> dict:
    """
    Call Geocodio batch endpoint.  Only address strings are sent — NO PII.
    Returns {address: (lat, lng)}.  Failed entries get (None, None).
    """
    results = {}
    total = len(addresses)
    for start in range(0, total, BATCH_SIZE):
        chunk = addresses[start: start + BATCH_SIZE]
        print(f"  📡 Geocoding batch {start+1}–{start+len(chunk)} of {total}…")
        try:
            resp = requests.post(
                GEOCODIO_ENDPOINT,
                params={"api_key": api_key},
                json=chunk,
                timeout=120,
            )
        except requests.RequestException as exc:
            print(f"  ✗ Request error: {exc}")
            for addr in chunk:
                results[addr] = (None, None)
            continue

        if resp.status_code != 200:
            print(f"  ✗ HTTP {resp.status_code}: {resp.text[:300]}")
            for addr in chunk:
                results[addr] = (None, None)
            continue

        for addr, item in zip(chunk, resp.json().get("results", [])):
            try:
                candidates = item["response"]["results"]
                if candidates:
                    loc = candidates[0]["location"]
                    results[addr] = (loc["lat"], loc["lng"])
                else:
                    results[addr] = (None, None)
            except (KeyError, IndexError, TypeError):
                results[addr] = (None, None)

    return results


# ─── VIEW REBUILD ─────────────────────────────────────────────────────────────

def _rebuild_view(conn: sqlite3.Connection):
    """
    Re-create the vw_staff_capacity view.
    Also create the schedule table if it doesn't exist yet.
    The view only references columns that are always guaranteed to exist
    because _fill_optional_cols ensures they are present (even if NaN).
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schedule (
            Schedule_ID TEXT, Staff_ID TEXT, Hours_Committed REAL
        )
    """)
    conn.execute("DROP VIEW IF EXISTS vw_staff_capacity")
    conn.execute("""
        CREATE VIEW vw_staff_capacity AS
        SELECT
            s.Staff_ID,
            s."First Name",
            s."Last Name",
            s.Mobile,
            s.Gender,
            s.Role,
            s.Latitude,
            s.Longitude,
            s.Max_Weekly_Hours,
            COALESCE(SUM(sch.Hours_Committed), 0) AS Total_Committed_Hours,
            (s.Max_Weekly_Hours - COALESCE(SUM(sch.Hours_Committed), 0)) AS Available_Hours
        FROM staff s
        LEFT JOIN schedule sch ON s.Staff_ID = sch.Staff_ID
        GROUP BY
            s.Staff_ID, s."First Name", s."Last Name", s.Mobile, s.Gender, s.Role,
            s.Latitude, s.Longitude, s.Max_Weekly_Hours
    """)
    conn.commit()


# ─── MAIN SYNC FUNCTION ──────────────────────────────────────────────────────

def sync_db_from_excels() -> SyncResult:
    """
    Full sync pipeline.  Call this once at Streamlit startup.
    Returns a SyncResult with counts, warnings, and status.
    """
    result = SyncResult()

    # ── 0.  Check files exist ────────────────────────────────────────────────
    for path, label in [(CLIENTS_EXCEL, "Clients"), (STAFF_EXCEL, "Staff")]:
        if not os.path.isfile(path):
            result.ok = False
            result.error = (
                f"**{label} file not found.**\n\n"
                f"Please export `{os.path.basename(path)}` from the agency system "
                f"and place it in the `data/` folder, then restart the app."
            )
            return result

    # ── 1.  Read Excel files ─────────────────────────────────────────────────
    try:
        clients_xl = _read_excel(CLIENTS_EXCEL)
        staff_xl   = _read_excel(STAFF_EXCEL)
    except Exception as exc:
        result.ok = False
        result.error = f"Error reading Excel files: {exc}"
        return result

    # ── 1a.  Validate required columns ───────────────────────────────────────
    for df, req, label in [
        (clients_xl, REQUIRED_CLIENT_COLS, "Clients"),
        (staff_xl,   REQUIRED_STAFF_COLS,  "Staff"),
    ]:
        err = _check_required_cols(df, req, label)
        if err:
            result.ok = False
            result.error = err
            return result

    # ── 1b.  Fill missing optional columns with safe defaults (NaN) ──────────
    # This ensures downstream code can always reference these columns via .get()
    # without KeyErrors, even if the DON's export omits them.
    missing_client_opt = _fill_optional_cols(clients_xl, OPTIONAL_CLIENT_COLS, "Clients")
    missing_staff_opt  = _fill_optional_cols(staff_xl,   OPTIONAL_STAFF_COLS,  "Staff")
    if missing_client_opt:
        result.missing_optional["Clients"] = missing_client_opt
    if missing_staff_opt:
        result.missing_optional["Staff"] = missing_staff_opt

    # ── 1c.  Apply exclusion lists ───────────────────────────────────────────
    # Drop rows whose "First Name" + " " + "Last Name" matches any excluded name.
    # Runs every sync so excluded names never make it into the DB.
    if EXCLUDED_CLIENT_NAMES:
        excl_lower = {n.strip().lower() for n in EXCLUDED_CLIENT_NAMES}
        full_names = (clients_xl["First Name"].str.strip() + " " + clients_xl["Last Name"].str.strip()).str.lower()
        before = len(clients_xl)
        clients_xl = clients_xl[~full_names.isin(excl_lower)].reset_index(drop=True)
        dropped = before - len(clients_xl)
        if dropped:
            print(f"  🚫 Excluded {dropped} client(s) by name")

    if EXCLUDED_STAFF_NAMES:
        excl_lower = {n.strip().lower() for n in EXCLUDED_STAFF_NAMES}
        full_names = (staff_xl["First Name"].str.strip() + " " + staff_xl["Last Name"].str.strip()).str.lower()
        before = len(staff_xl)
        staff_xl = staff_xl[~full_names.isin(excl_lower)].reset_index(drop=True)
        dropped = before - len(staff_xl)
        if dropped:
            print(f"  🚫 Excluded {dropped} staff member(s) by name")

    # Update counts after exclusion
    result.total_clients = len(clients_xl)
    result.total_staff   = len(staff_xl)

    # ── 2.  Load API key ─────────────────────────────────────────────────────
    load_dotenv(".env")
    api_key = os.getenv("geocodio_api_key") or os.getenv("GEOCODIO_API_KEY")
    # We don't crash if key is missing — we just skip geocoding and warn

    # ── 3.  Open DB, load cache and existing data ────────────────────────────
    conn = sqlite3.connect(DB_PATH)
    cache = _load_cache(conn)

    # Load existing DB tables (may not exist on first run)
    try:
        db_clients = pd.read_sql_query("SELECT * FROM clients", conn)
    except Exception:
        db_clients = pd.DataFrame()
    try:
        db_staff = pd.read_sql_query("SELECT * FROM staff", conn)
    except Exception:
        db_staff = pd.DataFrame()

    # ── 4.  Build surrogate keys ─────────────────────────────────────────────
    # Excel keys
    clients_xl["_skey"]      = clients_xl.apply(_surrogate_key, axis=1)
    clients_xl["_name_key"]  = clients_xl.apply(_surrogate_key_name_only, axis=1)
    clients_xl["_addr"]      = clients_xl.apply(_addr_for_row, axis=1)

    staff_xl["_skey"]        = staff_xl.apply(_surrogate_key, axis=1)
    staff_xl["_name_key"]    = staff_xl.apply(_surrogate_key_name_only, axis=1)
    staff_xl["_addr"]        = staff_xl.apply(_addr_for_row, axis=1)

    # DB keys (only if tables aren't empty)
    if not db_clients.empty:
        db_clients["_skey"]     = db_clients.apply(_surrogate_key, axis=1)
        db_clients["_name_key"] = db_clients.apply(_surrogate_key_name_only, axis=1)
        db_clients["_addr"]     = db_clients.apply(_addr_for_row, axis=1)
        existing_client_keys = set(db_clients["_skey"])
        existing_client_name_keys = dict(zip(db_clients["_name_key"], db_clients["_addr"]))
    else:
        existing_client_keys = set()
        existing_client_name_keys = {}

    if not db_staff.empty:
        db_staff["_skey"]     = db_staff.apply(_surrogate_key, axis=1)
        db_staff["_name_key"] = db_staff.apply(_surrogate_key_name_only, axis=1)
        db_staff["_addr"]     = db_staff.apply(_addr_for_row, axis=1)
        existing_staff_keys = set(db_staff["_skey"])
        existing_staff_name_keys = dict(zip(db_staff["_name_key"], db_staff["_addr"]))
    else:
        existing_staff_keys = set()
        existing_staff_name_keys = {}

    # ── 5.  Classify rows: NEW / ADDRESS-CHANGED / EXISTING ──────────────────
    addrs_needing_geocoding = set()

    def classify_rows(xl_df, existing_keys, existing_name_keys):
        """Tag each Excel row with _action: 'new', 'addr_changed', 'existing'."""
        actions = []
        for _, row in xl_df.iterrows():
            skey = row["_skey"]
            nkey = row["_name_key"]
            addr = row["_addr"]

            if skey not in existing_keys:
                # Same name exists with a different address?
                if nkey in existing_name_keys and existing_name_keys[nkey] != addr:
                    actions.append("addr_changed")
                    addrs_needing_geocoding.add(addr)
                else:
                    actions.append("new")
                    addrs_needing_geocoding.add(addr)
            else:
                actions.append("existing")
        xl_df["_action"] = actions

    classify_rows(clients_xl, existing_client_keys, existing_client_name_keys)
    classify_rows(staff_xl, existing_staff_keys, existing_staff_name_keys)

    # Only geocode addresses that are NOT already in the cache
    addrs_to_geocode = [a for a in addrs_needing_geocoding if a not in cache]

    # ── 6.  Geocode new / changed addresses ──────────────────────────────────
    if addrs_to_geocode:
        if api_key:
            print(f"  Geocoding {len(addrs_to_geocode)} new address(es) "
                  f"(only addresses sent — no PII)…")
            new_results = _batch_geocode(addrs_to_geocode, api_key)
            _save_cache(conn, new_results)
            cache.update(new_results)

            failures = [a for a, (lat, _) in new_results.items() if lat is None]
            result.geocode_failures = failures
            result.new_geocoded = len(new_results) - len(failures)
        else:
            print("  ⚠ GEOCODIO_API_KEY not set — skipping geocoding for new records")
            result.geocode_failures = list(addrs_to_geocode)
    else:
        print("  ✅ All addresses already cached — no Geocodio call needed.")

    # ── 7.  Prepare final DataFrames ─────────────────────────────────────────
    #  We only keep known columns (required + optional) plus computed ones.
    #  Extra columns from the Excel are dropped here so they never pollute
    #  the DB schema or break the Streamlit app.

    def finalise_clients(xl_df: pd.DataFrame) -> pd.DataFrame:
        df = xl_df.copy()
        # Add coordinates from cache
        df["Latitude"]  = [cache.get(a, (None, None))[0] for a in df["_addr"]]
        df["Longitude"] = [cache.get(a, (None, None))[1] for a in df["_addr"]]
        # Rename / tidy
        df = df.rename(columns={"Address 1": "Address", "Address 2": "Address2"})
        df.insert(0, "Client_ID", [f"C{i+1:03d}" for i in range(len(df))])
        # Drop internal keys and unknown extra columns
        df = df.drop(columns=["_skey", "_name_key", "_addr", "_action"], errors="ignore")
        # Keep only columns the app uses (renamed versions)
        known = {"Client_ID", "First Name", "Last Name", "Address", "Address2",
                 "City", "State", "Zip", "Phone", "Gender", "Class", "Birth Date",
                 "Latitude", "Longitude"}
        df = df[[c for c in df.columns if c in known]]
        return df

    def finalise_staff(xl_df: pd.DataFrame) -> pd.DataFrame:
        df = xl_df.copy()
        df["Latitude"]  = [cache.get(a, (None, None))[0] for a in df["_addr"]]
        df["Longitude"] = [cache.get(a, (None, None))[1] for a in df["_addr"]]
        df = df.rename(columns={"Address 1": "Address", "Address 2": "Address2"})
        df.insert(0, "Staff_ID", [f"S{i+1:03d}" for i in range(len(df))])
        df["Role"] = df["Skills"].apply(_map_role) if "Skills" in df.columns else "PCA"
        if "Weekly Max Hours" in df.columns:
            df["Max_Weekly_Hours"] = df["Weekly Max Hours"].apply(
                lambda x: None if pd.isna(x) or str(x).strip().lower() == "no max" else int(x)
            )
        else:
            df["Max_Weekly_Hours"] = None
        # Drop internal keys and unknown extra columns
        df = df.drop(columns=["_skey", "_name_key", "_addr", "_action"], errors="ignore")
        known = {"Staff_ID", "First Name", "Last Name", "Address", "Address2",
                 "City", "State", "Zip", "Mobile", "Gender", "Status",
                 "Hire Date", "Birth Date", "Skills", "Weekly Max Hours",
                 "Daily Max Hours", "Role", "Max_Weekly_Hours",
                 "Latitude", "Longitude"}
        df = df[[c for c in df.columns if c in known]]
        return df

    clients_final = finalise_clients(clients_xl)
    staff_final   = finalise_staff(staff_xl)

    # ── 8.  Count actions for the summary ────────────────────────────────────
    result.addr_updated    = int((clients_xl["_action"] == "addr_changed").sum() +
                                 (staff_xl["_action"]   == "addr_changed").sum())
    result.fields_updated  = int((clients_xl["_action"] == "existing").sum() +
                                  (staff_xl["_action"]   == "existing").sum())

    # Removals: rows in DB but not in Excel
    xl_client_name_keys = set(clients_xl["_name_key"])
    xl_staff_name_keys  = set(staff_xl["_name_key"])
    if not db_clients.empty:
        result.clients_removed = int((~db_clients["_name_key"].isin(xl_client_name_keys)).sum())
    if not db_staff.empty:
        result.staff_removed = int((~db_staff["_name_key"].isin(xl_staff_name_keys)).sum())

    # ── 9.  Write to DB (replace strategy — clean & simple) ──────────────────
    #  We write the full, freshly-built DataFrames.  This naturally handles:
    #    - inserts  (new rows from Excel)
    #    - updates  (existing rows with refreshed fields)
    #    - deletes  (rows removed from Excel simply don't appear)
    print(f"  Writing {len(clients_final)} clients, {len(staff_final)} staff to DB…")
    clients_final.to_sql("clients", conn, if_exists="replace", index=False)
    staff_final.to_sql("staff",     conn, if_exists="replace", index=False)
    conn.commit()

    # ── 10.  Rebuild the view ─────────────────────────────────────────────────
    _rebuild_view(conn)
    conn.close()

    print("  ✅ Sync complete.")
    return result
