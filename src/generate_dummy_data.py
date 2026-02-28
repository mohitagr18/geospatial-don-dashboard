import csv
import random
from faker import Faker

fake = Faker()

NUM_CLIENTS = 50
NUM_STAFF = 20
NUM_SCHEDULES = 100

LAT_MIN = 37.25
LAT_MAX = 37.75
LON_MIN = -77.65
LON_MAX = -77.30

ROLES = ['RN', 'LPN', 'PCA']

def generate_lat_lon():
    return random.uniform(LAT_MIN, LAT_MAX), random.uniform(LON_MIN, LON_MAX)

def generate_clients():
    clients = []
    for i in range(1, NUM_CLIENTS + 1):
        lat, lon = generate_lat_lon()
        clients.append({
            'Client_ID': f'C{i:03d}',
            'First Name': fake.first_name(),
            'Last Name': fake.last_name(),
            'Phone': fake.phone_number(),
            'Address': fake.street_address(),
            'City': fake.city(),
            'State': 'VA',
            'Zip': fake.zipcode_in_state('VA'),
            'Latitude': lat,
            'Longitude': lon
        })
    return clients

def generate_staff():
    staff = []
    for i in range(1, NUM_STAFF + 1):
        lat, lon = generate_lat_lon()
        staff.append({
            'Staff_ID': f'S{i:03d}',
            'First Name': fake.first_name(),
            'Last Name': fake.last_name(),
            'Address': fake.street_address(),
            'City': fake.city(),
            'State': 'VA',
            'Zip': fake.zipcode_in_state('VA'),
            'Phone': fake.phone_number(),
            'Role': random.choice(ROLES),
            'Max_Weekly_Hours': random.randint(20, 40),
            'Latitude': lat,
            'Longitude': lon
        })
    return staff

def generate_schedules(clients, staff):
    schedules = []
    for i in range(1, NUM_SCHEDULES + 1):
        schedules.append({
            'Schedule_ID': f'SCH{i:03d}',
            'Staff_ID': random.choice(staff)['Staff_ID'],
            'Client_ID': random.choice(clients)['Client_ID'],
            'Hours_Committed': random.randint(1, 8)
        })
    return schedules

def save_to_csv(data, filename, fieldnames):
    with open(filename, mode='w', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(data)

def main():
    clients = generate_clients()
    staff = generate_staff()
    schedules = generate_schedules(clients, staff)

    save_to_csv(clients, 'data/clients.csv', clients[0].keys())
    save_to_csv(staff, 'data/staff.csv', staff[0].keys())
    save_to_csv(schedules, 'data/schedule.csv', schedules[0].keys())
    
    print("Dummy data generated and saved to data/ directory.")

if __name__ == "__main__":
    main()
