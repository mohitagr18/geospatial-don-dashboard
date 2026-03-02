"""
geocode_geocodio.py  ─  Privacy-safe, incremental geocoder
─────────────────────────────────────────────────────────────
• Reads address columns ONLY (no names / PII) from the CSVs.
• Checks a local `geocode_cache` table in the SQLite DB first.
• Only sends UNCACHED addresses to the Geocodio batch API.
• Stores new results back into the cache, then rebuilds the
  clients + staff tables using the full cached coordinates.

Run this script ONLY when new clients or staff are added.
For schema / view changes use:  uv run python src/setup_schema.py
"""

import os
import sqlite3

import pandas as pd
import requests
from dotenv import load_dotenv

# ─── CONFIG ───────────────────────────────────────────────────────────────────
ENV_FILE          = ".env"
CLIENTS_CSV       = "data/clients.csv"
STAFF_CSV         = "data/staff.csv"
DB_PATH           = "data/staffing_engine.db"
GEOCODIO_ENDPOINT = "https://api.geocod.io/v1.7/geocode"
BATCH_SIZE        = 10_000
ADDRESS_COLS      = ["Address 1", "Address 2", "City", "State", "Zip"]
# ──────────────────────────────────────────────────────────────────────────────

load_dotenv(ENV_FILE)
API_KEY = os.getenv("geocodio_api_key") or os.getenv("GEOCODIO_API_KEY")
if not API_KEY:
    raise ValueError("geocodio_api_key not found in .env file!")
print(f"API key loaded: {API_KEY[:8]}{'*' * (len(API_KEY) - 8)}")


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def build_address(*parts) -> str:
    return ", ".join(str(p).strip() for p in parts if pd.notna(p) and str(p).strip())


def load_cache(conn: sqlite3.Connection) -> dict:
    """Return {address_string: (lat, lng)} for every cached entry."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS geocode_cache (
            address  TEXT PRIMARY KEY,
            latitude  REAL,
            longitude REAL
        )
    """)
    rows = conn.execute("SELECT address, latitude, longitude FROM geocode_cache").fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


def save_cache(conn: sqlite3.Connection, new_entries: dict):
    """Upsert {address: (lat, lng)} into the cache table."""
    conn.executemany(
        "INSERT OR REPLACE INTO geocode_cache (address, latitude, longitude) VALUES (?, ?, ?)",
        [(addr, lat, lng) for addr, (lat, lng) in new_entries.items()],
    )
    conn.commit()


def batch_geocode_new(addresses: list) -> dict:
    """
    Geocode a list of address strings via Geocodio batch endpoint.
    Returns {address: (lat, lng)}. Only address strings are sent — no PII.
    """
    results = {}
    total = len(addresses)

    for chunk_start in range(0, total, BATCH_SIZE):
        chunk = addresses[chunk_start : chunk_start + BATCH_SIZE]
        print(f"  → Sending {len(chunk)} NEW addresses to Geocodio "
              f"(batch {chunk_start+1}–{chunk_start+len(chunk)} of {total})…")

        resp = requests.post(
            GEOCODIO_ENDPOINT,
            params={"api_key": API_KEY},
            json=chunk,
            timeout=60,
        )

        if resp.status_code != 200:
            print(f"  ✗ HTTP {resp.status_code}: {resp.text[:300]}")
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


def enrich_with_coords(df: pd.DataFrame, cache: dict) -> pd.DataFrame:
    """Add Latitude / Longitude columns to df by looking up each row's address in cache."""
    addrs = [
        build_address(*[r[c] for c in ADDRESS_COLS if c in r.index])
        for _, r in df[ADDRESS_COLS].iterrows()
    ]
    df = df.copy()
    df["Latitude"]  = [cache.get(a, (None, None))[0] for a in addrs]
    df["Longitude"] = [cache.get(a, (None, None))[1] for a in addrs]
    return df


# ─── LOAD CSVs ────────────────────────────────────────────────────────────────
print("\nLoading CSVs…")

def read_csv(path):
    df = pd.read_csv(path, header=2)
    df.columns = df.columns.str.strip()
    if df.columns[0].startswith("Unnamed"):
        df = df.iloc[:, 1:]
    return df.dropna(how="all").reset_index(drop=True)

clients_raw = read_csv(CLIENTS_CSV)
staff_raw   = read_csv(STAFF_CSV)
print(f"  Clients: {len(clients_raw)} rows")
print(f"  Staff:   {len(staff_raw)} rows")

# ─── BUILD ADDRESS LISTS (address columns only — no PII) ─────────────────────
all_client_addrs = [
    build_address(*[r[c] for c in ADDRESS_COLS if c in r.index])
    for _, r in clients_raw[ADDRESS_COLS].iterrows()
]
all_staff_addrs = [
    build_address(*[r[c] for c in ADDRESS_COLS if c in r.index])
    for _, r in staff_raw[ADDRESS_COLS].iterrows()
]
all_addresses = list(set(all_client_addrs + all_staff_addrs))

# ─── CHECK CACHE ──────────────────────────────────────────────────────────────
conn = sqlite3.connect(DB_PATH)
cache = load_cache(conn)

uncached = [a for a in all_addresses if a not in cache]
print(f"\nCache stats: {len(cache)} cached  |  {len(uncached)} new addresses to geocode")

# ─── GEOCODE ONLY NEW ADDRESSES ───────────────────────────────────────────────
if uncached:
    print(f"\nSending {len(uncached)} new address(es) to Geocodio (no PII included)…")
    new_results = batch_geocode_new(uncached)
    save_cache(conn, new_results)
    cache.update(new_results)

    failed = [a for a, (lat, _) in new_results.items() if lat is None]
    print(f"\nGeocoding complete: {len(new_results)-len(failed)} succeeded, {len(failed)} failed")
    if failed:
        for f in failed:
            print(f"  ⚠ Failed: {f}")
else:
    print("✅  All addresses already cached — skipping Geocodio API call entirely.")

conn.close()

# ─── REBUILD TABLES & SCHEMA ──────────────────────────────────────────────────
print("\nRebuilding database tables from cached coordinates…")

# Normalise clients
clients_final = enrich_with_coords(clients_raw, cache)
clients_final = clients_final.rename(columns={"Address 1": "Address", "Address 2": "Address2"})
clients_final.insert(0, "Client_ID", [f"C{i+1:03d}" for i in range(len(clients_final))])

# Normalise staff
def map_role(skill) -> str:
    if pd.isna(skill): return "PCA"
    s = str(skill).strip().upper()
    return s if s in ("LPN", "RN") else "PCA"

staff_final = enrich_with_coords(staff_raw, cache)
staff_final = staff_final.rename(columns={"Address 1": "Address", "Address 2": "Address2"})
staff_final.insert(0, "Staff_ID", [f"S{i+1:03d}" for i in range(len(staff_final))])
staff_final["Role"] = staff_final["Skills"].apply(map_role)
staff_final["Max_Weekly_Hours"] = staff_final["Weekly Max Hours"].apply(
    lambda x: None if pd.isna(x) or str(x).strip().lower() == "no max" else int(x)
)

# Write to DB then rebuild schema
conn = sqlite3.connect(DB_PATH)
clients_final.to_sql("clients", conn, if_exists="replace", index=False)
staff_final.to_sql("staff",   conn, if_exists="replace", index=False)
conn.commit()
conn.close()

# Delegate view + schema rebuild to setup_schema.py logic
import subprocess, sys
print("\nRunning setup_schema.py to rebuild view…")
subprocess.run([sys.executable, "src/setup_schema.py"], check=True)
