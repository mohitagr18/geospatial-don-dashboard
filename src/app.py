import sqlite3
import math
import pandas as pd
import streamlit as st
import folium
from streamlit_folium import st_folium

st.set_page_config(layout="wide", page_title="DON Dashboard")

# ─── HELPERS ──────────────────────────────────────────────────────────────────

def haversine(lat1, lon1, lat2, lon2):
    R = 3958.8
    dLat = math.radians(lat2 - lat1)
    dLon = math.radians(lon2 - lon1)
    lat1 = math.radians(lat1)
    lat2 = math.radians(lat2)
    a = math.sin(dLat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dLon/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

def fmt_hours(val):
    """Return the value as a string, or 'N/A' if missing/invalid."""
    try:
        v = float(val)
        if pd.isna(v):
            return "N/A"
        return str(int(v))
    except (TypeError, ValueError):
        return "N/A"

# ─── DATA ─────────────────────────────────────────────────────────────────────

@st.cache_data
def load_data():
    conn = sqlite3.connect('data/staffing_engine.db')
    clients_df = pd.read_sql_query("SELECT * FROM clients", conn)
    staff_df   = pd.read_sql_query("SELECT * FROM vw_staff_capacity", conn)
    conn.close()
    clients_df['Full Name'] = clients_df['First Name'] + ' ' + clients_df['Last Name']
    return clients_df, staff_df

clients_df, staff_df = load_data()

# Sorted client name list for dropdown
sorted_client_names = sorted(clients_df['Full Name'].tolist())
dropdown_options    = ["— Select a Client —"] + sorted_client_names

# ─── SESSION STATE ────────────────────────────────────────────────────────────

if 'selected_client' not in st.session_state:
    st.session_state.selected_client = None

# ─── SIDEBAR ──────────────────────────────────────────────────────────────────

st.sidebar.header("Select Client")

# Drive the selectbox index directly from session state.
# This is the ONLY correct Streamlit pattern for bidirectional sync:
# do NOT use key= here — index= must control the value, not session_state.
sel = st.session_state.selected_client
current_idx = sorted_client_names.index(sel) if sel in sorted_client_names else None

dropdown_choice = st.sidebar.selectbox(
    "Client Name",
    options=sorted_client_names,
    index=current_idx,
    placeholder="Type or scroll to find a client…",
    label_visibility="collapsed",
)

# Sync dropdown ↔ session state (covers selection, x-clear, and map-click updates)
if dropdown_choice != st.session_state.selected_client:
    st.session_state.selected_client = dropdown_choice   # None if user hit x
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.header("Filters")

has_client = bool(st.session_state.selected_client)

# Use a Unicode 'greater-than' character so Streamlit doesn't treat > as HTML
RADIUS_OPTIONS = ["5", "10", "15", "20", "25", "› 25 miles"]
INF_OPTION     = "› 25 miles"   # must match exactly

radius_filter = st.sidebar.radio(
    "Distance Radius (miles)",
    RADIUS_OPTIONS,
    disabled=not has_client,
    help="Select a client first to filter by distance." if not has_client else "Filter staff by distance to the selected client."
)

max_dist = {"5": 5, "10": 10, "15": 15, "20": 20, "25": 25}.get(radius_filter, float('inf'))

# ── Sidebar Legend ────────────────────────────────────────────────────────────
st.sidebar.markdown("---")
st.sidebar.subheader("Legend")
st.sidebar.markdown("🔵 Circle = Client")
st.sidebar.markdown("**Staff Roles (Shapes & Colors):**")
st.sidebar.markdown(
    """
<div style='font-size: 15px; display: flex; flex-direction: column; gap: 10px;'>
    <div style='display: flex; align-items: center; gap: 10px;'>
        <svg width='24' height='24' viewBox='0 0 26 26'>
            <polygon points='13,3 23,23 3,23' stroke='#28a745' stroke-width='2' fill='#28a745' fill-opacity='0.7'/>
        </svg>
        <span style='color:#1a1a1a;'>PCA</span>
    </div>
    <div style='display: flex; align-items: center; gap: 10px;'>
        <svg width='24' height='24' viewBox='0 0 26 26'>
            <rect x='4' y='4' width='18' height='18' stroke='#7B2FBE' stroke-width='2' fill='#7B2FBE' fill-opacity='0.7'/>
        </svg>
        <span style='color:#1a1a1a;'>LPN</span>
    </div>
    <div style='display: flex; align-items: center; gap: 10px;'>
        <svg width='24' height='24' viewBox='0 0 26 26'>
            <polygon points='13,2 24,10 20,22 6,22 2,10' stroke='#E8700A' stroke-width='2' fill='#E8700A' fill-opacity='0.7'/>
        </svg>
        <span style='color:#1a1a1a;'>RN</span>
    </div>
</div>
""",
    unsafe_allow_html=True,
)

# Staff Availability (Colors) — disabled until real scheduling data is loaded
# st.sidebar.markdown(" ")
# st.sidebar.markdown("**Staff Availability (Colors):**")
# st.sidebar.markdown(
#     """
#     <div style='display: flex; flex-direction: column; gap: 2px; font-size: 16px;'>
#         <div><span style='color:#28a745; font-size:22px;'>■</span> &gt; 10 hrs available</div>
#         <div><span style='color:#fd7e14; font-size:22px;'>■</span> 1–10 hrs available</div>
#         <div><span style='color:#dc3545; font-size:22px;'>■</span> 0 hrs / unavailable</div>
#     </div>
#     """,
#     unsafe_allow_html=True,
# )

st.sidebar.markdown("---")
if st.sidebar.button("Reset Map"):
    st.session_state.selected_client = None
    st.rerun()

# ─── MAP SETUP ────────────────────────────────────────────────────────────────

# MAP_SETUP: default center; fit_bounds below will override it when a client is selected
map_center = [37.5, -77.5]

st.title("Staffing Dashboard")
if st.session_state.selected_client:
    st.write(f"Displaying focused map for **{st.session_state.selected_client}**.")
else:
    st.write("Select a client from the sidebar or click a blue circle on the map to find nearby staff.")

m = folium.Map(location=[37.5, -77.5], zoom_start=11)

fg_clients = folium.FeatureGroup(name="Clients")
fg_rns     = folium.FeatureGroup(name="RNs")
fg_lpns    = folium.FeatureGroup(name="LPNs")
fg_pcas    = folium.FeatureGroup(name="PCAs")

# ─── DYNAMIC LAYER LOGIC ─────────────────────────────────────────────────────

if st.session_state.selected_client:
    selected_client_row = clients_df[
        clients_df['Full Name'] == st.session_state.selected_client
    ].iloc[0]
    clients_to_draw = clients_df[clients_df['Full Name'] == st.session_state.selected_client]
    staff_work = staff_df.copy()
    staff_work['Distance_Miles'] = staff_work.apply(
        lambda row: haversine(
            selected_client_row['Latitude'], selected_client_row['Longitude'],
            row['Latitude'], row['Longitude']
        ), axis=1
    )
    staff_to_draw = staff_work[staff_work['Distance_Miles'] <= max_dist]

    # ── Auto-fit map bounds to contain the client + all staff in range ─────────
    all_lats = list(staff_to_draw['Latitude'].dropna()) + [selected_client_row['Latitude']]
    all_lons = list(staff_to_draw['Longitude'].dropna()) + [selected_client_row['Longitude']]
    if all_lats and all_lons:
        padding = 0.02   # ~1.5 miles of breathing room around the outermost pin
        sw = [min(all_lats) - padding, min(all_lons) - padding]
        ne = [max(all_lats) + padding, max(all_lons) + padding]
        m.fit_bounds([sw, ne])
else:
    clients_to_draw = clients_df
    staff_to_draw   = staff_df.copy()
    staff_to_draw['Distance_Miles'] = None

# ── Client markers ────────────────────────────────────────────────────────────
for _, client in clients_to_draw.iterrows():
    # Build address line gracefully — Address2 may be blank
    addr_parts = [client.get('Address', '')]
    if pd.notna(client.get('Address2')) and str(client.get('Address2', '')).strip():
        addr_parts.append(str(client['Address2']).strip())
    addr_parts.append(f"{client.get('City', '')}, {client.get('State', '')} {client.get('Zip', '')}")
    address_str = " ".join(p for p in addr_parts if p.strip())

    phone = client.get('Phone', '')
    phone = str(phone).strip() if pd.notna(phone) and str(phone).strip() else 'N/A'

    client_tooltip = (
        f"<b>{client['Full Name']}</b><br>"
        f"📞 {phone}<br>"
        f"📍 {address_str}"
    )

    folium.CircleMarker(
        location=[client['Latitude'], client['Longitude']],
        radius=6,
        color='blue',
        fill=True,
        fill_opacity=0.7,
        tooltip=folium.Tooltip(client_tooltip, sticky=True),
    ).add_to(fg_clients)

# ── Staff markers ─────────────────────────────────────────────────────────────
for _, staff in staff_to_draw.iterrows():
    role = staff['Role']
    if role == 'LPN':
        sides, fg = 4, fg_lpns
    elif role == 'RN':
        sides, fg = 5, fg_rns
    else:                       # PCA / anything else
        sides, fg = 3, fg_pcas

    # Color by role (availability colors re-enable when schedule data is loaded)
    if role == 'LPN':
        fill_color = '#7B2FBE'   # purple
    elif role == 'RN':
        fill_color = '#E8700A'   # orange
    else:
        fill_color = '#28a745'   # green for PCA

    max_h   = fmt_hours(staff.get('Max_Weekly_Hours'))
    avail_h = fmt_hours(staff.get('Available_Hours'))

    mobile = staff.get('Mobile', '')
    if pd.isna(mobile) or str(mobile).strip() == '':
        mobile = 'N/A'

    tooltip_html = (
        f"<b>Name:</b> {staff['First Name']} {staff['Last Name']}<br>"
        f"<b>Phone:</b> {mobile}<br>"
        f"<b>Role:</b> {role}<br>"
        f"<b>Max Weekly Hours:</b> {max_h}<br>"
        f"<b>Available Hours:</b> {avail_h}"
    )

    folium.RegularPolygonMarker(
        location=[staff['Latitude'], staff['Longitude']],
        number_of_sides=sides,
        radius=10,
        color=fill_color,
        fill=True,
        fill_color=fill_color,
        fill_opacity=0.7,
        tooltip=tooltip_html,
    ).add_to(fg)

fg_clients.add_to(m)
fg_rns.add_to(m)
fg_lpns.add_to(m)
fg_pcas.add_to(m)
folium.LayerControl().add_to(m)

# ─── RENDER MAP ───────────────────────────────────────────────────────────────

st_data = st_folium(
    m,
    use_container_width=True,
    height=650,
    returned_objects=["last_object_clicked_tooltip", "last_clicked"],
    key=f"map_{st.session_state.selected_client}_{max_dist}",
)

# Sync map click → session state
# Tooltip is now HTML so we do a substring search for each client's name
clicked_tooltip = st_data.get("last_object_clicked_tooltip") or ""
if clicked_tooltip:
    matched_name = next(
        (name for name in clients_df['Full Name'].values if name in clicked_tooltip),
        None,
    )
    if matched_name and st.session_state.selected_client != matched_name:
        st.session_state.selected_client = matched_name
        st.rerun()

# ─── DATAFRAME TABLE ─────────────────────────────────────────────────────────

if st.session_state.selected_client:
    st.subheader(f"Showing Nearby Staff for: {st.session_state.selected_client}")
    filtered_staff = staff_to_draw.copy()

    if not filtered_staff.empty:
        # Safe sort: Distance_Miles always numeric; Available_Hours may be NaN
        filtered_staff = filtered_staff.sort_values(
            by='Distance_Miles',
            ascending=True,
            na_position='last',
        )

        display_cols = ['First Name', 'Last Name', 'Mobile', 'Role', 'Distance_Miles', 'Max_Weekly_Hours', 'Available_Hours']
        formatted_df = filtered_staff[display_cols].copy()

        # Round distance
        formatted_df['Distance_Miles'] = formatted_df['Distance_Miles'].round(1)

        # Replace NaN / invalid hours with "N/A" for display
        formatted_df['Max_Weekly_Hours'] = formatted_df['Max_Weekly_Hours'].apply(fmt_hours)
        formatted_df['Available_Hours']  = formatted_df['Available_Hours'].apply(fmt_hours)

        formatted_df = formatted_df.rename(columns={
            'Mobile':           'Phone',
            'Distance_Miles':   'Distance (Miles)',
            'Max_Weekly_Hours': 'Max Weekly Hours',
            'Available_Hours':  'Available Hours',
        })

        # Expand table to fit all rows — no fixed height, no inner scroll
        row_px = 35
        header_px = 38
        table_height = header_px + len(formatted_df) * row_px
        st.dataframe(
            formatted_df,
            use_container_width=True,
            height=table_height,
            hide_index=True,
        )
    else:
        st.info("No staff members found within the selected distance radius.")
else:
    st.info("Select a client from the sidebar or click a blue circle on the map to find nearby staff.")
