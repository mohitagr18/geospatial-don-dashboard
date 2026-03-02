import pandas as pd
import sqlite3
import time
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError

# ─── CONFIG ───────────────────────────────────────────────────────────────────
CLIENTS_CSV = "data/clients.csv"
STAFF_CSV   = "data/staff.csv"
DB_PATH     = "data/staffing_engine.db"
USER_AGENT  = "yti_homecare_routing_app"
SLEEP_SEC   = 1.2   # Respect OSM free-tier rate limit
# ──────────────────────────────────────────────────────────────────────────────

geolocator = Nominatim(user_agent=USER_AGENT)

def safe_geocode(address: str):
    """Attempt to geocode an address; return (lat, lon) or (None, None) on failure."""
    try:
        location = geolocator.geocode(address, timeout=10)
        if location:
            return location.latitude, location.longitude
        else:
            print(f"  ⚠ No result for: {address}")
            return None, None
    except (GeocoderTimedOut, GeocoderServiceError) as e:
        print(f"  ✗ Geocoder error for '{address}': {e}")
        return None, None


def build_address(*parts) -> str:
    """Concatenate non-null address parts into a single string."""
    return ", ".join(str(p).strip() for p in parts if pd.notna(p) and str(p).strip())


def geocode_df(df: pd.DataFrame, label: str) -> pd.DataFrame:
    """Add Latitude and Longitude columns by geocoding each row's address."""
    latitudes  = []
    longitudes = []
    total = len(df)

    for idx, row in df.iterrows():
        full_addr = build_address(
            row.get("Address 1"),
            row.get("Address 2"),
            row.get("City"),
            row.get("State"),
            row.get("Zip"),
        )
        print(f"  [{idx+1}/{total}] {label}: {full_addr}")
        lat, lon = safe_geocode(full_addr)
        latitudes.append(lat)
        longitudes.append(lon)
        time.sleep(SLEEP_SEC)

    df["Latitude"]  = latitudes
    df["Longitude"] = longitudes
    return df


# ─── LOAD CSVs ────────────────────────────────────────────────────────────────
# Both files have 2 junk rows before the real header (row index 0 & 1)
print("Loading clients CSV...")
clients_raw = pd.read_csv(CLIENTS_CSV, header=2)          # header on row 3 (0-indexed: 2)
clients_raw.columns = clients_raw.columns.str.strip()
# Drop the leading unnamed index column if present
if clients_raw.columns[0].startswith("Unnamed"):
    clients_raw = clients_raw.drop(columns=clients_raw.columns[0])
# Drop fully empty rows
clients_raw = clients_raw.dropna(how="all").reset_index(drop=True)
print(f"  → {len(clients_raw)} client rows loaded")
print(f"  Columns: {list(clients_raw.columns)}\n")

print("Loading staff CSV...")
staff_raw = pd.read_csv(STAFF_CSV, header=2)
staff_raw.columns = staff_raw.columns.str.strip()
if staff_raw.columns[0].startswith("Unnamed"):
    staff_raw = staff_raw.drop(columns=staff_raw.columns[0])
staff_raw = staff_raw.dropna(how="all").reset_index(drop=True)
print(f"  → {len(staff_raw)} staff rows loaded")
print(f"  Columns: {list(staff_raw.columns)}\n")

# ─── GEOCODE ─────────────────────────────────────────────────────────────────
print("=" * 60)
print("GEOCODING CLIENTS  (this will take a while — ~1.2s per address)")
print("=" * 60)
clients_df = geocode_df(clients_raw.copy(), "Client")

failed_clients = clients_df[clients_df["Latitude"].isna()]
print(f"\nClients geocoded. Failed: {len(failed_clients)}")
if not failed_clients.empty:
    print(failed_clients[["First Name", "Last Name", "Address 1", "City", "Zip"]].to_string())

print("\n" + "=" * 60)
print("GEOCODING STAFF  (this will take a while — ~1.2s per address)")
print("=" * 60)
staff_df = geocode_df(staff_raw.copy(), "Staff")

failed_staff = staff_df[staff_df["Latitude"].isna()]
print(f"\nStaff geocoded. Failed: {len(failed_staff)}")
if not failed_staff.empty:
    print(failed_staff[["First Name", "Last Name", "Address 1", "City", "Zip"]].to_string())

# ─── MAP TO DB SCHEMA ─────────────────────────────────────────────────────────
# Clients table: standardise column names to match app expectations
clients_final = clients_df.rename(columns={
    "Address 1": "Address",
    "Address 2": "Address2",
})
# Keep only the columns the app uses (add Client_ID as row number)
clients_final.insert(0, "Client_ID", ["C{:03d}".format(i+1) for i in range(len(clients_final))])

# Staff table: map Skills → Role; add numeric Max_Weekly_Hours; add Staff_ID
# Skills column: CARE → PCA by default; LPN → LPN; no RN in source data
def map_role(skill):
    if pd.isna(skill):
        return "PCA"
    s = str(skill).strip().upper()
    if s == "LPN":
        return "LPN"
    if s == "RN":
        return "RN"
    return "PCA"   # CARE and anything else → PCA

staff_final = staff_df.rename(columns={
    "Address 1": "Address",
    "Address 2": "Address2",
    "Weekly Max Hours" : "Max_Weekly_Hours_Raw",
})
staff_final.insert(0, "Staff_ID", ["S{:03d}".format(i+1) for i in range(len(staff_final))])
staff_final["Role"] = staff_final["Skills"].apply(map_role)
# "No Max" → default 40 hours
staff_final["Max_Weekly_Hours"] = staff_final["Max_Weekly_Hours_Raw"].apply(
    lambda x: 40 if pd.isna(x) or str(x).strip().lower() == "no max" else int(x)
)
# Preserve pet/smoke columns from previous synthetic data if they exist, else default False
for col in ["Smokes", "Cats_OK", "Dogs_OK"]:
    if col not in staff_final.columns:
        staff_final[col] = False

# ─── WRITE TO SQLITE ──────────────────────────────────────────────────────────
print("\nConnecting to", DB_PATH)
conn = sqlite3.connect(DB_PATH)

print("Writing clients table...")
clients_final.to_sql("clients", conn, if_exists="replace", index=False)

print("Writing staff table...")
staff_final.to_sql("staff", conn, if_exists="replace", index=False)

# Rebuild the capacity view to reflect real column names
print("Rebuilding vw_staff_capacity view...")
conn.execute("DROP VIEW IF EXISTS vw_staff_capacity")
conn.execute("""
CREATE VIEW vw_staff_capacity AS
SELECT
    s.Staff_ID,
    s."First Name",
    s."Last Name",
    s.Role,
    s.Latitude,
    s.Longitude,
    s.Max_Weekly_Hours,
    s.Smokes,
    s.Cats_OK,
    s.Dogs_OK,
    COALESCE(SUM(sch.Hours_Committed), 0)                            AS Total_Committed_Hours,
    (s.Max_Weekly_Hours - COALESCE(SUM(sch.Hours_Committed), 0))    AS Available_Hours
FROM staff s
LEFT JOIN schedule sch ON s.Staff_ID = sch.Staff_ID
GROUP BY
    s.Staff_ID, s."First Name", s."Last Name", s.Role,
    s.Latitude, s.Longitude, s.Max_Weekly_Hours,
    s.Smokes, s.Cats_OK, s.Dogs_OK
""")
conn.commit()
conn.close()

print("\n✅  Database updated successfully!")
print(f"   Clients written : {len(clients_final)}")
print(f"   Staff written   : {len(staff_final)}")
print(f"   Failed clients  : {len(failed_clients)}")
print(f"   Failed staff    : {len(failed_staff)}")
