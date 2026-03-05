# YTI Homecare — Staffing Dashboard

An internal tool for the Director of Nursing (DON) to visualise clients and available staff on an interactive map, filtered by distance.

---

## What it does

- Shows all **clients** (blue circles) and **staff** (coloured shapes by role) on an interactive map of the Richmond, VA area.
- Select a client — by clicking the map or using the sidebar dropdown — to instantly see which staff are within a chosen distance radius (5, 10, 15, 20, 25, or > 25 miles).
- The table below the map lists matching staff sorted by distance, with hours and contact info.
- Data is kept up to date by dropping two Excel exports from the agency system into the `data/` folder and clicking **Refresh Data**.

---

## Quick Start (first time)

### 1 — Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.9 + | Check with `python --version` |
| [`uv`](https://docs.astral.sh/uv/) | Fast Python package manager — `pip install uv` |
| Geocodio API key | Only needed when new addresses appear |

### 2 — Clone and install

```bash
git clone https://github.com/mohitagr18/geospatial-don-dashboard.git
cd geospatial-don-dashboard
uv sync            # installs all dependencies into .venv
```

### 3 — Add your API key

Create a file called `.env` in the project root:

```
GEOCODIO_API_KEY=your_key_here
```

> The key is only used when the app detects a **new or changed address** that isn't already in its local cache. Day-to-day refreshes won't trigger any API calls.

### 4 — Add the data files

Place **both** Excel exports from the agency system into the `data/` folder:

```
data/
├── CustomerData.xlsx     ← client export
├── CaregiverData.xlsx    ← caregiver/staff export
└── staffing_engine.db    ← auto-created on first run
```

> **File names must match exactly.** The app looks for `CustomerData.xlsx` and `CaregiverData.xlsx`.

### 5 — Run the app

```bash
uv run streamlit run src/app.py
```

Then open **http://localhost:8501** in your browser.

---

## Day-to-day use

### Updating data (e.g., after a weekly export)

1. Export fresh **CustomerData.xlsx** and **CaregiverData.xlsx** from the agency system.
2. Drop them into the `data/` folder (overwrite the old files).
3. Click **🔄 Refresh Data** in the sidebar.
4. Done — the app updates the database automatically.

The app is smart about geocoding:
- **New or address-changed records** → geocoded via Geocodio.
- **Unchanged records** → coordinates reused from cache, no API call.
- **Records removed from the Excel** → removed from the map.

### Using the map

| Action | Result |
|---|---|
| Click a blue circle on the map | Selects that client; highlights nearby staff |
| Use the **Select Client** dropdown | Same as clicking on the map |
| Change **Distance Radius** buttons | Filters staff to within that many miles |
| Click **Reset Map** | Clears selection and zooms back out |

### What the tooltips show

**Client markers (blue circle):**  Name · Phone · Address · Gender · Class

**Staff markers (coloured shape):**  Name · Phone · Role · Gender · Max Weekly Hours · Available Hours

---

## Required Excel columns

The app validates the spreadsheets on every load. It needs at minimum:

| File | Required columns |
|---|---|
| `CustomerData.xlsx` | First Name, Last Name, Address 1, City, State, Zip |
| `CaregiverData.xlsx` | First Name, Last Name, Address 1, City, State, Zip |

**Optional columns** (Gender, Phone, Class, Mobile, Weekly Max Hours, etc.) are used if present and shown as *N/A* if missing — the app will never crash because of a missing optional column.

**Extra columns** in the Excel are silently ignored — you can export the full sheet as-is.

---

## Project structure

```
geospatial-don-dashboard/
├── src/
│   └── app.py                 # Streamlit app — map, filters, UI
├── etl/
│   ├── __init__.py
│   └── sync.py                # ETL: reads Excel → geocodes → updates SQLite
├── data/
│   ├── CustomerData.xlsx      # Client export (drop updated file here)
│   ├── CaregiverData.xlsx     # Staff export  (drop updated file here)
│   └── staffing_engine.db     # SQLite database (auto-managed)
├── .env                       # API key (never committed to git)
├── pyproject.toml
└── README.md
```

---

## For technical users

### How the ETL works (`etl/sync.py`)

1. Reads both Excel files and strips whitespace from column headers.
2. Validates required columns; aborts with a clear error if any are missing.
3. Fills missing optional columns with `None` (never crashes on schema changes).
4. Applies the **exclusion list** — names in `EXCLUDED_CLIENT_NAMES` / `EXCLUDED_STAFF_NAMES` at the top of `sync.py` are always dropped before the DB is written (case-insensitive).
5. Assigns a stable **surrogate key** (`lower(first_name|last_name|address)`) to each row so records can be tracked across refreshes without relying on IDs.
6. Classifies each row as `new`, `addr_changed`, or `existing`.
7. Geocodes only `new` and `addr_changed` rows via the [Geocodio](https://www.geocod.io/) batch API — only address strings are sent, never any PII.
8. Writes final DataFrames to SQLite using a replace strategy, then rebuilds `vw_staff_capacity`.

### Key configuration in `etl/sync.py`

```python
# File names the app expects in data/
CLIENTS_EXCEL = "data/CustomerData.xlsx"
STAFF_EXCEL   = "data/CaregiverData.xlsx"

# Names excluded from every sync (test / training accounts)
EXCLUDED_CLIENT_NAMES = ["Jane Doe", ...]
EXCLUDED_STAFF_NAMES  = ["Test Aide", ...]
```

### Tech stack

| Component | Technology |
|---|---|
| UI framework | [Streamlit](https://streamlit.io/) |
| Map | [Folium](https://python-visualization.github.io/folium/) + [streamlit-folium](https://github.com/randyzwitch/streamlit-folium) |
| Database | SQLite (`staffing_engine.db`) |
| Geocoding | [Geocodio](https://www.geocod.io/) batch API |
| Package manager | [uv](https://docs.astral.sh/uv/) |

### Possible future enhancements

- Real scheduling data to show true available-hours per caregiver.
- Preference matching (smoker, pets, language).
- Role-based login / multi-user access.
- Automated scheduled refresh (e.g., cron or Task Scheduler).

---

> ⚠️ **Privacy notice:** This app stores client and staff data locally. The only data ever sent externally is plain address strings to the Geocodio API. Never deploy this app on a public server or Streamlit Community Cloud.