import sqlite3
import pandas as pd
import streamlit as st
import folium
import math
from streamlit_folium import st_folium

st.set_page_config(layout="wide", page_title="DON Dashboard")

def haversine(lat1, lon1, lat2, lon2):
    R = 3958.8 # Earth radius in miles
    dLat = math.radians(lat2 - lat1)
    dLon = math.radians(lon2 - lon1)
    lat1 = math.radians(lat1)
    lat2 = math.radians(lat2)

    a = math.sin(dLat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dLon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

@st.cache_data
def load_data():
    conn = sqlite3.connect('data/staffing_engine.db')
    clients_df = pd.read_sql_query("SELECT * FROM clients", conn)
    staff_df = pd.read_sql_query("SELECT * FROM vw_staff_capacity", conn)
    conn.close()
    
    # Pre-compute full name for clients
    clients_df['Full Name'] = clients_df['First Name'] + ' ' + clients_df['Last Name']
    return clients_df, staff_df

clients_df, staff_df = load_data()

# Initialize session state for selected client
if 'selected_client' not in st.session_state:
    st.session_state.selected_client = None

st.title("Geospatial Intelligence Dashboard")

st.sidebar.header("Filters")
radius_filter = st.sidebar.radio("Distance Radius", ["5", "10", "15", "20", ">20 miles"])

if radius_filter == "5":
    max_dist = 5
elif radius_filter == "10":
    max_dist = 10
elif radius_filter == "15":
    max_dist = 15
elif radius_filter == "20":
    max_dist = 20
else:
    max_dist = float('inf')

# Sidebar Legend
st.sidebar.markdown("---")
st.sidebar.subheader("Legend")
st.sidebar.markdown("🔵 Client")
st.sidebar.markdown("Staff Roles (Shapes):")

# The Shapes Legend
st.sidebar.markdown(
    """
<div style='color: #B0B0B0; font-size: 16px; display: flex; flex-direction: column; gap: 8px;'>
    <div style='display: flex; align-items: center; gap: 8px;'>
        <svg width='26' height='26' viewBox='0 0 26 26'>
            <polygon points='13,3 23,23 3,23' stroke='#B0B0B0' stroke-width='2.5' fill='none'/>
        </svg>
        PCA
    </div>
    <div style='display: flex; align-items: center; gap: 8px;'>
        <svg width='26' height='26' viewBox='0 0 26 26'>
            <rect x='4' y='4' width='18' height='18' stroke='#B0B0B0' stroke-width='2.5' fill='none'/>
        </svg>
        LPN
    </div>
    <div style='display: flex; align-items: center; gap: 8px;'>
        <svg width='26' height='26' viewBox='0 0 26 26'>
            <polygon points='13,2 24,10 20,22 6,22 2,10'
                     stroke='#B0B0B0' stroke-width='2.5' fill='none'/>
        </svg>
        RN
    </div>
</div>
"""
, 
    unsafe_allow_html=True
)
st.sidebar.markdown(" ")
# The Colors Legend
st.sidebar.markdown("Staff Availability (Colors):")
st.sidebar.markdown(
    """
    <div style='display: flex; flex-direction: column; gap: 2px; font-size: 16px; margin: 0; padding: 0;'>
        <div style='margin: 0; padding: 0; line-height: 1.2;'><span style='color: #28a745; font-size: 26px; vertical-align: middle; display: inline-block; width: 30px; text-align: center;'>■</span> &gt; 10 Hours</div>
        <div style='margin: 0; padding: 0; line-height: 1.2;'><span style='color: #fd7e14; font-size: 26px; vertical-align: middle; display: inline-block; width: 30px; text-align: center;'>■</span> 1-10 Hours</div>
        <div style='margin: 0; padding: 0; line-height: 1.2;'><span style='color: #dc3545; font-size: 26px; vertical-align: middle; display: inline-block; width: 30px; text-align: center;'>■</span> 0 Hours</div>
    </div>
    """,
    unsafe_allow_html=True
)


st.sidebar.markdown("---")
if st.sidebar.button("Reset Map"):
    st.session_state.selected_client = None
    st.rerun()

# Dynamic Map Center based on session state
if st.session_state.selected_client:
    selected_client_row = clients_df[clients_df['Full Name'] == st.session_state.selected_client].iloc[0]
    map_center = [selected_client_row['Latitude'], selected_client_row['Longitude']]
else:
    map_center = [37.5, -77.5]

if st.session_state.selected_client:
    st.write("Displaying focused map for selected client.")
else:
    st.write("Click on a location to find nearby staff based on your selected distance radius.")

m = folium.Map(location=map_center, zoom_start=11)

fg_clients = folium.FeatureGroup(name="Clients")
fg_rns = folium.FeatureGroup(name="RNs")
fg_lpns = folium.FeatureGroup(name="LPNs")
fg_pcas = folium.FeatureGroup(name="PCAs")

# Dynamic Map Generation based on session state
if st.session_state.selected_client:
    clients_to_draw = clients_df[clients_df['Full Name'] == st.session_state.selected_client]
    staff_df['Distance_Miles'] = staff_df.apply(
        lambda row: haversine(selected_client_row['Latitude'], selected_client_row['Longitude'], row['Latitude'], row['Longitude']), axis=1
    )
    staff_to_draw = staff_df[staff_df['Distance_Miles'] <= max_dist]
else:
    clients_to_draw = clients_df
    staff_to_draw = staff_df

for _, client in clients_to_draw.iterrows():
    client_name = client['Full Name']
    folium.CircleMarker(
        location=[client['Latitude'], client['Longitude']],
        radius=6,
        color='blue',
        fill=True,
        fill_opacity=0.7,
        tooltip=client_name
    ).add_to(fg_clients)

for _, staff in staff_to_draw.iterrows():
    role = staff['Role']
    sides = 3 # default PCA
    if role == 'PCA':
        sides = 3
        fg = fg_pcas
    elif role == 'LPN':
        sides = 4
        fg = fg_lpns
    elif role == 'RN':
        sides = 5
        fg = fg_rns
    else:
        sides = 5
        fg = fg_rns
        
    avail_hours = staff['Available_Hours']
    if avail_hours > 10:
        fill_color = 'green'
    elif 1 <= avail_hours <= 10:
        fill_color = 'orange'
    else:
        fill_color = 'red'

    smokes = 'Yes' if int(staff['Smokes']) else 'No'
    cats_ok = 'Yes' if int(staff['Cats_OK']) else 'No'
    dogs_ok = 'Yes' if int(staff['Dogs_OK']) else 'No'

    tooltip_html = f"""
    <b>Name:</b> {staff['First Name']} {staff['Last Name']}<br>
    <b>Role:</b> {role}<br>
    <b>Total Max Hours:</b> {staff['Max_Weekly_Hours']}<br>
    <b>Available Hours:</b> {avail_hours}<br>
    <b>Smokes:</b> {smokes}<br>
    <b>Cats OK:</b> {cats_ok}<br>
    <b>Dogs OK:</b> {dogs_ok}
    """

    folium.RegularPolygonMarker(
        location=[staff['Latitude'], staff['Longitude']],
        number_of_sides=sides,
        radius=10,
        color=fill_color,
        fill=True,
        fill_color=fill_color,
        fill_opacity=0.7,
        tooltip=tooltip_html
    ).add_to(fg)

fg_clients.add_to(m)
fg_rns.add_to(m)
fg_lpns.add_to(m)
fg_pcas.add_to(m)

folium.LayerControl().add_to(m)

st_data = st_folium(m, width=1200, height=650, returned_objects=["last_object_clicked_tooltip", "last_clicked"])

clicked_tooltip = st_data.get("last_object_clicked_tooltip")

# State change hook -> Check if client marker clicked and update state
if clicked_tooltip and clicked_tooltip in clients_df['Full Name'].values:
    if st.session_state.selected_client != clicked_tooltip:
        st.session_state.selected_client = clicked_tooltip
        st.rerun()

# Display formatted dataframe table
if st.session_state.selected_client:
    selected_client_name = st.session_state.selected_client
    st.subheader(f"Showing Nearby Staff for: {selected_client_name}")
    
    filtered_staff = staff_to_draw.copy()
    
    if not filtered_staff.empty:
        filtered_staff = filtered_staff.sort_values(by=['Distance_Miles', 'Available_Hours'], ascending=[True, False])
        
        filtered_staff['Smokes'] = filtered_staff['Smokes'].apply(lambda x: "Yes" if int(x) else "No")
        filtered_staff['Cats_OK'] = filtered_staff['Cats_OK'].apply(lambda x: "Yes" if int(x) else "No")
        filtered_staff['Dogs_OK'] = filtered_staff['Dogs_OK'].apply(lambda x: "Yes" if int(x) else "No")
        
        display_cols = ['First Name', 'Last Name', 'Role', 'Distance_Miles', 'Max_Weekly_Hours', 'Available_Hours', 'Smokes', 'Cats_OK', 'Dogs_OK']
        formatted_df = filtered_staff[display_cols].copy()
        formatted_df['Distance_Miles'] = formatted_df['Distance_Miles'].round(1)

        
        formatted_df = formatted_df.rename(columns={
            'Distance_Miles': 'Distance (Miles)',
            'Max_Weekly_Hours': 'Max Weekly Hours',
            'Available_Hours': 'Available Hours',
            'Cats_OK': 'Cats OK',
            'Dogs_OK': 'Dogs OK'
        })
        
        st.dataframe(formatted_df)
    else:
        st.info("No staff members found within the selected distance radius.")
else:
    st.info("Click on a client marker on the map to find nearby staff.")
