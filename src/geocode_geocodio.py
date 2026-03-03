"""
geocode_geocodio.py  ─  Privacy-safe, incremental geocoder
─────────────────────────────────────────────────────────────
Reads CaregiverData.xlsx and CustomerData.xlsx.
• Checks a local `geocode_cache` table keyed by address string.
• Identifies NEW records vs what is already in the DB.
• Only sends uncached addresses to the Geocodio batch API.
• Writes results back to cache, then rebuilds clients + staff tables.

Run ONLY when adding new clients or staff.
For schema / view changes run:  uv run python src/setup_schema.py
"""

import os
import sqlite3

import pandas as pd
import requests
from dotenv import load_dotenv

# ─── CONFIG ───────────────────────────────────────────────────────────────────
ENV_FILE           = ".env"
CLIENTS_EXCEL      = "data/CustomerData.xlsx"
STAFF_EXCEL        = "data/CaregiverData.xlsx"
DB_PATH            = "data/staffing_engine.db"
GEOCODIO_ENDPOINT  = "https://api.geocod.io/v1.7/geocode"
BATCH_SIZE         = 10_000

# Address columns sent to Geocodio — NO names, phones, or other PII
ADDRESS_COLS = ["Address 1", "Address 2", "City", "State", "Zip"]
# ──────────────────────────────────────────────────────────────────────────────

load_dotenv(ENV_FILE)
API_KEY = os.getenv("geocodio_api_key") or os.getenv("GEOCODIO_API_KEY")
if not API_KEY:
    raise ValueError("geocodio_api_key not found in .env file!")
print(f"API key loaded: {API_KEY[:8]}{'*' * (len(API_KEY) - 8)}\n")


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def build_address(*parts) -> str:
    """Join non-null address parts into a single comma-separated string."""
    return ", ".join(str(p).strip() for p in parts if pd.notna(p) and str(p).strip())


def read_excel(path: str) -> pd.DataFrame:
    """Read an Excel file — header on row 0, strip column whitespace."""
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")   # suppress openpyxl style warnings
        df = pd.read_excel(path, header=0)
    df.columns = df.columns.str.strip()
    return df.dropna(how="all").reset_index(drop=True)


def load_cache(conn: sqlite3.Connection) -> dict:
    """Return {address_string: (lat, lng)} for every cached entry."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS geocode_cache (
            address   TEXT PRIMARY KEY,
            latitude  REAL,
            longitude REAL
        )
    """)
    conn.commit()
    rows = conn.execute(
        "SELECT address, latitude, longitude FROM geocode_cache"
    ).fetchall()
    return {r[0]: (r[1], r[2]) for r in rows}


def save_cache(conn: sqlite3.Connection, new_entries: dict):
    """Upsert {address: (lat, lng)} into the cache table."""
    conn.executemany(
        "INSERT OR REPLACE INTO geocode_cache (address, latitude, longitude) VALUES (?, ?, ?)",
        [(addr, lat, lng) for addr, (lat, lng) in new_entries.items()],
    )
    conn.commit()


def batch_geocode(addresses: list) -> dict:
    """
    Geocode a plain list of address strings via the Geocodio batch endpoint.
    Only address strings are sent — absolutely no PII.
    Returns {address_string: (lat, lng)}.
    """
    results = {}
    total   = len(addresses)

    for start in range(0, total, BATCH_SIZE):
        chunk = addresses[start : start + BATCH_SIZE]
        print(f"  → Sending {len(chunk)} addresses to Geocodio "
              f"({start + 1}–{start + len(chunk)} of {total})…")

        resp = requests.post(
            GEOCODIO_ENDPOINT,
            params={"api_key": API_KEY},
            json=chunk,
            timeout=120,
        )

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


def addr_for_row(row) -> str:
    """Build the address string for a single DataFrame row."""
    return build_address(*[row[c] for c in ADDRESS_COLS if c in row.index])


def enrich_coords(df: pd.DataFrame, cache: dict) -> pd.DataFrame:
    """Add Latitude / Longitude by address cache lookup."""
    df = df.copy()
    df["Latitude"]  = [cache.get(addr_for_row(r), (None, None))[0]
                       for _, r in df.iterrows()]
    df["Longitude"] = [cache.get(addr_for_row(r), (None, None))[1]
                       for _, r in df.iterrows()]
    return df


# ─── LOAD EXCEL FILES ─────────────────────────────────────────────────────────
print("Loading Excel files…")
clients_raw = read_excel(CLIENTS_EXCEL)
staff_raw   = read_excel(STAFF_EXCEL)
print(f"  CustomerData: {len(clients_raw)} rows  |  cols: {list(clients_raw.columns)}")
print(f"  CaregiverData: {len(staff_raw)} rows  |  cols: {list(staff_raw.columns)}")


# ─── IDENTIFY NEW RECORDS ────────────────────────────────────────────────────
# A record is "new" if its address is not yet in the geocode_cache.
# We key purely on address — same logic as the cache — so name/phone never
# leaves this machine.
conn = sqlite3.connect(DB_PATH)
cache = load_cache(conn)

client_addrs = [addr_for_row(r) for _, r in clients_raw[ADDRESS_COLS].iterrows()]
staff_addrs  = [addr_for_row(r) for _, r in staff_raw[ADDRESS_COLS].iterrows()]

all_unique_addrs = list(set(client_addrs + staff_addrs))
uncached = [a for a in all_unique_addrs if a not in cache]

new_client_count = sum(1 for a in client_addrs if a not in cache)
new_staff_count  = sum(1 for a in staff_addrs  if a not in cache)

print(f"\nCache: {len(cache)} addresses already cached")
print(f"New client addresses : {new_client_count}")
print(f"New staff addresses  : {new_staff_count}")
print(f"Total new to geocode : {len(uncached)}")


# ─── GEOCODE ONLY NEW ADDRESSES ───────────────────────────────────────────────
if uncached:
    print(f"\n📡 Geocoding {len(uncached)} new address(es)…")
    print("   (Only address strings are sent — no names, phones, or PII)")
    new_results = batch_geocode(uncached)
    save_cache(conn, new_results)
    cache.update(new_results)

    failed = [a for a, (lat, _) in new_results.items() if lat is None]
    success = len(new_results) - len(failed)
    print(f"\n  ✅ Geocoded: {success}  |  Failed: {len(failed)}")
    for f in failed:
        print(f"     ⚠ {f}")
else:
    print("\n✅  No new addresses — skipping Geocodio API entirely.")

conn.close()


# ─── NORMALISE SCHEMAS ────────────────────────────────────────────────────────

# ── Clients ──
clients_final = enrich_coords(clients_raw, cache)
clients_final = clients_final.rename(columns={
    "Address 1": "Address",
    "Address 2": "Address2",
})
clients_final.insert(0, "Client_ID", [f"C{i+1:03d}" for i in range(len(clients_final))])

# ── Staff ──
def map_role(skill) -> str:
    if pd.isna(skill): return "PCA"
    s = str(skill).strip().upper()
    return s if s in ("LPN", "RN") else "PCA"

staff_final = enrich_coords(staff_raw, cache)
staff_final = staff_final.rename(columns={
    "Address 1": "Address",
    "Address 2": "Address2",
})
staff_final.insert(0, "Staff_ID", [f"S{i+1:03d}" for i in range(len(staff_final))])
staff_final["Role"]            = staff_final["Skills"].apply(map_role)
staff_final["Max_Weekly_Hours"] = staff_final["Weekly Max Hours"].apply(
    lambda x: None if pd.isna(x) or str(x).strip().lower() == "no max" else int(x)
)


# ─── WRITE TABLES ─────────────────────────────────────────────────────────────
print("\nWriting tables to database…")
conn = sqlite3.connect(DB_PATH)
clients_final.to_sql("clients", conn, if_exists="replace", index=False)
staff_final.to_sql("staff",     conn, if_exists="replace", index=False)
conn.commit()
conn.close()
print(f"  clients : {len(clients_final)} rows written")
print(f"  staff   : {len(staff_final)} rows written")


# ─── REBUILD SCHEMA (view etc.) ───────────────────────────────────────────────
import subprocess, sys
print("\nRebuilding schema (view)…")
subprocess.run([sys.executable, "src/setup_schema.py"], check=True)
