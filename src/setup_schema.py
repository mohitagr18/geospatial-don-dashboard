"""
setup_schema.py  ─  Rebuild the SQLite view and any schema changes
──────────────────────────────────────────────────────────────────
Run this whenever you change the view definition, add/remove columns
from the display, etc. Does NOT call Geocodio — safe to run anytime.
"""

import sqlite3

DB_PATH = "data/staffing_engine.db"

conn = sqlite3.connect(DB_PATH)

print("Rebuilding vw_staff_capacity…")
conn.execute("DROP VIEW IF EXISTS vw_staff_capacity")
conn.execute("""
CREATE VIEW vw_staff_capacity AS
SELECT
    s.Staff_ID,
    s."First Name",
    s."Last Name",
    s.Mobile,
    s.Role,
    s.Latitude,
    s.Longitude,
    s.Max_Weekly_Hours,
    COALESCE(SUM(sch.Hours_Committed), 0)                         AS Total_Committed_Hours,
    (s.Max_Weekly_Hours - COALESCE(SUM(sch.Hours_Committed), 0)) AS Available_Hours
FROM staff s
LEFT JOIN schedule sch ON s.Staff_ID = sch.Staff_ID
GROUP BY
    s.Staff_ID, s."First Name", s."Last Name", s.Mobile, s.Role,
    s.Latitude, s.Longitude, s.Max_Weekly_Hours
""")
conn.commit()

# Sanity check
clients = conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0]
staff   = conn.execute("SELECT COUNT(*) FROM staff").fetchone()[0]
view    = conn.execute("SELECT COUNT(*) FROM vw_staff_capacity").fetchone()[0]
conn.close()

print(f"  clients rows          : {clients}")
print(f"  staff rows            : {staff}")
print(f"  vw_staff_capacity rows: {view}")
print("\n✅  Schema rebuild complete.")
