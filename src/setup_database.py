import sqlite3
import pandas as pd

def main():
    print("Reading CSVs...")
    clients_df = pd.read_csv('data/clients.csv')
    staff_df = pd.read_csv('data/staff.csv')
    schedule_df = pd.read_csv('data/schedule.csv')

    print("Connecting to SQLite database at 'data/staffing_engine.db'...")
    conn = sqlite3.connect('data/staffing_engine.db')

    print("Ingesting data into SQLite...")
    clients_df.to_sql('clients', conn, if_exists='replace', index=False)
    staff_df.to_sql('staff', conn, if_exists='replace', index=False)
    schedule_df.to_sql('schedule', conn, if_exists='replace', index=False)

    print("Creating view 'vw_staff_capacity'...")
    create_view_sql = """
    DROP VIEW IF EXISTS vw_staff_capacity;
    """
    conn.execute(create_view_sql)

    create_view_sql = """
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
        COALESCE(SUM(sch.Hours_Committed), 0) AS Total_Committed_Hours,
        (s.Max_Weekly_Hours - COALESCE(SUM(sch.Hours_Committed), 0)) AS Available_Hours
    FROM 
        staff s
    LEFT JOIN 
        schedule sch ON s.Staff_ID = sch.Staff_ID
    GROUP BY 
        s.Staff_ID, s."First Name", s."Last Name", s.Role, s.Latitude, s.Longitude, s.Max_Weekly_Hours, s.Smokes, s.Cats_OK, s.Dogs_OK;
    """
    conn.execute(create_view_sql)
    conn.commit()
    conn.close()

    print("Database setup complete.")

if __name__ == "__main__":
    main()
