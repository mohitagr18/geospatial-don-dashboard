# YTI Homecare — Geospatial DON Dashboard

An internal, privacy-first staffing tool for the Director of Nursing and scheduling team at YTI Homecare. The dashboard displays clients and caregivers on an interactive map, calculates drive distances on the fly, and surfaces the nearest available staff for any selected client — making it faster to decide who to assign before picking up the phone.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Key Features](#2-key-features)
3. [Architecture](#3-architecture)
4. [Directory Structure](#4-directory-structure)
5. [Prerequisites](#5-prerequisites)
6. [Installation](#6-installation)
7. [Database and Data Preparation](#7-database-and-data-preparation)
8. [Running the App](#8-running-the-app)
9. [Using the Dashboard](#9-using-the-dashboard)
10. [Security and Privacy](#10-security-and-privacy)
11. [Customization](#11-customization)
12. [Limitations and Future Work](#12-limitations-and-future-work)

---

## 1. Project Overview

YTI Homecare serves clients across the greater Richmond, VA metro area. Matching the right caregiver to a client involves balancing distance, schedule availability, and role qualifications (PCA, LPN, RN). Doing this lookup manually — cross-referencing spreadsheets, calling staff, checking hours — is slow and error-prone.

This dashboard gives the Director of Nursing and schedulers a single screen to answer the question: *"Who is closest to this client and has hours available this week?"* Clicking a client on the map or selecting them from the sidebar dropdown instantly filters the entire caregiver roster to those within the chosen mile radius, sorted by distance. The result is a ranked, actionable list — ready for a phone call.

---

## 2. Key Features

- **Interactive Folium map** — clients shown as blue circles, caregivers as role-coded polygon shapes
- **Role-coded marker shapes and colors**
  - 🟢 Triangle = PCA (green)
  - 🟣 Square = LPN (purple)
  - 🟠 Pentagon = RN (orange)
- **Bidirectional client selection** — select from the sidebar dropdown *or* click any client circle on the map; both stay in sync
- **Distance radius filter** — 5 / 10 / 15 / 20 / 25 / › 25 miles; map auto-zooms to fit all visible pins
- **Hover tooltips** — client tooltips show name, phone, and address; staff tooltips show name, phone, role, and hours
- **Ranked staff table** — sorted by distance, showing phone, role, distance, and weekly hour availability
- **Incremental geocoding** — addresses are geocoded with Geocodio only once; subsequent runs use a local cache and skip the API entirely for unchanged records
- **Layer control** — toggle Clients, PCAs, LPNs, and RNs on/off independently
- **Reset Map button** — clears the selection and returns to the full Richmond overview

---

## 3. Architecture

| Layer | Technology |
|-------|-----------|
| **UI** | [Streamlit](https://streamlit.io) — Python-native web UI, wide layout |
| **Map** | [Folium](https://python-visualization.github.io/folium/) — Leaflet.js wrapper |
| **Map ↔ Streamlit bridge** | [streamlit-folium](https://folium.streamlit.app) — captures click events from the Folium map |
| **Data store** | [SQLite](https://www.sqlite.org) (`data/staffing_engine.db`) — zero-config embedded DB |
| **Geocoding** | [Geocodio](https://www.geocod.io) batch API — converts addresses to lat/lng; results cached locally |
| **ETL** | Custom Python scripts in `src/` |

> **Privacy note:** The database contains real client and staff PII (names, phone numbers, home addresses). This application is designed for **internal deployment only** — a local laptop, an office machine on the LAN, or behind a VPN with access controls. It must **not** be deployed on Streamlit Community Cloud or any other public hosting platform.

---

## 4. Directory Structure

```
geospatial-don-dashboard/
│
├── src/
│   ├── app.py                  # Main Streamlit dashboard
│   ├── geocode_geocodio.py     # Incremental geocoder — run when new clients/staff are added
│   └── setup_schema.py         # Rebuilds the SQLite view — run after schema changes
│
├── data/                       # ⚠️  Excluded from Git (.gitignore)
│   ├── staffing_engine.db      # SQLite database (tables + geocode cache + view)
│   ├── CustomerData.xlsx       # Source client data
│   └── CaregiverData.xlsx      # Source staff/caregiver data
│
├── .env                        # API keys — never commit this file
├── .gitignore
├── pyproject.toml              # Managed by uv
├── uv.lock
└── README.md
```

---

## 5. Prerequisites

- **Python 3.9+** (project uses 3.9 by default; see `.python-version`)
- **uv** (recommended) or **pip** for dependency management
- A **Geocodio API key** — create a free account at [geocod.io](https://www.geocod.io); the free tier covers thousands of addresses
- The source Excel files (`CustomerData.xlsx`, `CaregiverData.xlsx`) placed in `data/`

> The `data/` directory is excluded from Git. You will need to obtain the source files separately and place them locally before running the ETL scripts.

---

## 6. Installation

### Clone the repository

```bash
git clone https://github.com/your-org/geospatial-don-dashboard.git
cd geospatial-don-dashboard
```

### Set up the environment

**With uv (recommended):**
```bash
pip install uv          # one-time global install
uv sync                 # installs all dependencies from uv.lock
```

**With pip and a virtual environment:**
```bash
python -m venv .venv
source .venv/bin/activate       # macOS/Linux
# .venv\Scripts\activate        # Windows

pip install streamlit folium streamlit-folium pandas python-dotenv requests openpyxl
```

### Configure the API key

Create a `.env` file in the project root:

```
geocodio_api_key=YOUR_KEY_HERE
```

> `python-dotenv` loads this automatically when the geocoding script runs. **Do not commit `.env` to source control.**

---

## 7. Database and Data Preparation

The SQLite database is built and maintained by two scripts:

### `src/geocode_geocodio.py` — run when new clients or staff are added

This script performs the full ETL pipeline:

1. Reads `data/CustomerData.xlsx` and `data/CaregiverData.xlsx`
2. Extracts **address columns only** (Street, City, State, Zip) — no names or other PII are ever sent to the Geocodio API
3. Checks a local `geocode_cache` table in `staffing_engine.db` for addresses already processed
4. Sends only **new, uncached** addresses to Geocodio's batch endpoint in a single API call
5. Stores the returned coordinates in the cache for future runs
6. Rebuilds the `clients` and `staff` tables using the full cached coordinate set
7. Calls `setup_schema.py` to recreate the database view

```bash
uv run python src/geocode_geocodio.py
```

On subsequent runs with no new records, the script detects that all addresses are already cached and exits without touching the API:
```
✅  No new addresses — skipping Geocodio API entirely.
```

### `src/setup_schema.py` — run after schema/view changes only

Rebuilds the `vw_staff_capacity` view from the existing `staff` table. Use this when you've changed view columns or logic without adding new records from the Excel files.

```bash
uv run python src/setup_schema.py
```

### Database schema at a glance

| Table / View | Purpose |
|---|---|
| `clients` | All client records with geocoded lat/lng |
| `staff` | All caregiver records with geocoded lat/lng |
| `schedule` | Future scheduling data (currently empty) |
| `vw_staff_capacity` | View joining `staff` + `schedule` to compute `Available_Hours` |
| `geocode_cache` | Address → lat/lng cache, keyed by address string |

---

## 8. Running the App

```bash
uv run streamlit run src/app.py
```

Or if you installed with pip:
```bash
streamlit run src/app.py
```

Streamlit will print the local URL — open it in your browser:
```
Local URL: http://localhost:8501
```

The app uses `st.set_page_config(layout="wide")`, so it expands to fill the full browser width. A widescreen monitor (1440px+) gives the best experience.

---

## 9. Using the Dashboard

### Selecting a client

You have two options — both do the same thing:

- **Sidebar dropdown** — type part of a name to filter, then click to select
- **Map click** — click any blue circle directly on the map

The dropdown and the map stay in sync: clicking a map marker updates the dropdown, and selecting from the dropdown updates the map focus.

### Filtering by distance

Once a client is selected, the **Distance Radius** filter in the sidebar becomes active. Choose one of:

`5 mi → 10 mi → 15 mi → 20 mi → 25 mi → › 25 mi (all)`

The map automatically re-zooms using `fit_bounds()` to frame the client and all visible staff within the chosen radius. The staff table below updates simultaneously.

### Reading the map

| Marker | Meaning |
|--------|---------|
| 🔵 Blue circle | Client |
| 🟢 Green triangle | PCA (Personal Care Aide) |
| 🟣 Purple square | LPN (Licensed Practical Nurse) |
| 🟠 Orange pentagon | RN (Registered Nurse) |

Hover over any marker to see a tooltip with the person's name, phone number, role, and (for staff) weekly hour details.

Use the **layer control** (top-right of the map) to toggle individual groups on or off.

### Reading the staff table

The table below the map lists all staff within the selected radius, sorted by distance (nearest first):

| Column | Description |
|--------|-------------|
| First Name / Last Name | Caregiver name |
| Phone | Mobile number |
| Role | PCA / LPN / RN |
| Distance (Miles) | Straight-line distance from client, rounded to 1 decimal |
| Max Weekly Hours | Cap from the source data; shows N/A if none set |
| Available Hours | Max minus committed hours; shows N/A until scheduling data is loaded |

### Resetting the view

To return to the full Richmond overview and clear the selected client, either:
- Click the **✕** icon inside the dropdown box to clear it, or
- Click the **Reset Map** button in the sidebar

---

## 10. Security and Privacy

> ⚠️  **This application handles personally identifiable information (PII) and may touch protected health information (PHI). Handle it accordingly.**

- **Internal use only** — run on a local machine, an office computer on the LAN, or behind a VPN with access controls. Never deploy on Streamlit Community Cloud, Heroku, Railway, or any other public platform.
- **Do not commit sensitive files** — both `data/` and `.env` are listed in `.gitignore`. Verify this before every `git push`, especially if you fork or move the repo.
- **API key** — the Geocodio key in `.env` has billing implications. Store it securely and rotate it if it is ever exposed.
- **Database** — `staffing_engine.db` contains home addresses of both clients and staff. Treat it like any other sensitive HR document. Do not email it, store it in shared cloud folders, or include it in backups that leave the office network unencrypted.

**Recommended deployment patterns:**
- Local laptop (single-user, ideal for the DON's workstation)
- Office mini-PC or NAS on the internal network, accessible only over LAN
- Cloud VM inside a private VPC, behind a reverse proxy (e.g., Nginx) with HTTP Basic Auth or SSO

---

## 11. Customization

### Change distance radius options

Edit the `RADIUS_OPTIONS` list and the `max_dist` lookup dict in `src/app.py`:

```python
RADIUS_OPTIONS = ["5", "10", "15", "20", "25", "› 25 miles"]
max_dist = {"5": 5, "10": 10, "15": 15, "20": 20, "25": 25}.get(radius_filter, float('inf'))
```

### Add a new staff role or marker style

1. Add the new role string to the `map_role()` function in `src/geocode_geocodio.py`
2. Add a matching `elif` branch in the marker-drawing loop in `src/app.py` with the desired `number_of_sides` and `fill_color`
3. Add a corresponding entry to the sidebar legend HTML block

### Modify table columns

Find the `display_cols` list in `src/app.py` and add or remove column names. Add a matching entry to the `rename()` dict for a clean display name.

### Add preference filters (smoker, pets, etc.)

The ETL pipeline is ready to carry extra columns from the Excel files. To add a pet/smoking filter:

1. Ensure the column (e.g., `Has_Cats`, `Prefers_Non_Smoker`) flows through `geocode_geocodio.py` into the DB
2. Add it to the `SELECT` in `setup_schema.py`
3. Add an `st.sidebar.checkbox` or `st.sidebar.multiselect` in `app.py`
4. Apply the filter to `staff_work` before the distance calculation

---

## 12. Limitations and Future Work

### Current limitations

- **All staff markers are currently the same color** within each role — availability-based color coding (green / orange / red) is stubbed out pending real scheduling data in the `schedule` table
- **Hours often show N/A** — the `Max_Weekly_Hours` field is not populated for most staff ("No Max"); `Available_Hours` depends on the `schedule` table which is currently empty
- **No authentication** — Streamlit has no built-in auth; access control must be handled at the network or reverse-proxy level
- **Straight-line distance only** — the Haversine formula gives crow-flies distance, not drive time or drive distance

### Suggested future enhancements

| Enhancement | Notes |
|---|---|
| **Scheduling data integration** | Populate the `schedule` table from the agency's scheduling system to get real availability |
| **Drive time / routing** | Replace Haversine with Google Maps Distance Matrix or OSRM for accurate drive times |
| **Authentication** | Add [Streamlit-Authenticator](https://github.com/mkhorasani/Streamlit-Authenticator) or put the app behind an authenticated reverse proxy |
| **Preference matching filters** | Sidebar checkboxes for pet compatibility, smoking preference, language, gender |
| **Export to CSV** | Add an `st.download_button` to export the nearby staff table for a given client |
| **Automated data refresh** | Schedule `geocode_geocodio.py` as a cron job or Windows Task Scheduler task to refresh nightly from the source Excel files |
| **Mobile layout** | Streamlit's wide layout is desktop-optimised; a narrow layout variant would help on tablets |