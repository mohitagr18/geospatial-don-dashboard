import sqlite3
import pandas as pd
import streamlit as st
import folium
import math
from streamlit_folium import st_folium

def haversine(lat1, lon1, lat2, lon2):
    R = 3958.8 # Earth radius in miles
    dLat = math.radians(lat2 - lat1)
    dLon = math.radians(lon2 - lon1)
    lat1 = math.radians(lat1)
    lat2 = math.radians(lat2)

    a = math.sin(dLat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dLon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

st.set_page_config(layout="wide", page_title="DON Dashboard")

@st.cache_data
def load_data():
    conn = sqlite3.connect('data/staffing_engine.db')
    clients_df = pd.read_sql_query("SELECT * FROM clients", conn)
    staff_df = pd.read_sql_query("SELECT * FROM vw_staff_capacity", conn)
    conn.close()
    return clients_df, staff_df

clients_df, staff_df = load_data()

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

st.write("Click on a location to find nearby staff based on your selected distance radius.")

m = folium.Map(location=[37.5, -77.5], zoom_start=11)

fg_clients = folium.FeatureGroup(name="Clients")
fg_rns = folium.FeatureGroup(name="RNs")
fg_lpns = folium.FeatureGroup(name="LPNs")
fg_pcas = folium.FeatureGroup(name="PCAs")

for _, client in clients_df.iterrows():
    tooltip_html = f"<b>Client ID:</b> {client['Client_ID']}<br><b>Cats:</b> {'Yes' if client['Has_Cats'] else 'No'}<br><b>Dogs:</b> {'Yes' if client['Has_Dogs'] else 'No'}<br><b>Prefers Non-Smoker:</b> {'Yes' if client['Prefers_Non_Smoker'] else 'No'}"
    folium.CircleMarker(
        location=[client['Latitude'], client['Longitude']],
        radius=6,
        color='blue',
        fill=True,
        fill_opacity=0.7,
        tooltip=tooltip_html,
        popup=folium.Popup(client['Client_ID'], parse_html=True, name=client['Client_ID'])
    ).add_to(fg_clients)

for _, staff in staff_df.iterrows():
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

    smokes = 'Yes' if staff['Smokes'] else 'No'
    cats_ok = 'Yes' if staff['Cats_OK'] else 'No'
    dogs_ok = 'Yes' if staff['Dogs_OK'] else 'No'

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

st_data = st_folium(m, width=1000, height=500)

if st_data and st_data.get("last_clicked"):
    clicked_lat = st_data["last_clicked"]["lat"]
    clicked_lon = st_data["last_clicked"]["lng"]

    staff_df['Distance_Miles'] = staff_df.apply(
        lambda row: haversine(clicked_lat, clicked_lon, row['Latitude'], row['Longitude']), axis=1
    )

    filtered_staff = staff_df[staff_df['Distance_Miles'] <= max_dist].copy()
    filtered_staff = filtered_staff.sort_values(by=['Distance_Miles', 'Available_Hours'], ascending=[True, False])
    
    display_cols = ['First Name', 'Last Name', 'Role', 'Distance_Miles', 'Max_Weekly_Hours', 'Available_Hours', 'Smokes', 'Cats_OK', 'Dogs_OK']
    
    st.subheader(f"Nearby Staff (within {radius_filter if max_dist < float('inf') else 'any'} distance)")
    st.dataframe(filtered_staff[display_cols])
else:
    st.info("Click on a location on the map to find nearby staff.")
