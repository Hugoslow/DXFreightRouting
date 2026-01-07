import pandas as pd
from app.database import SessionLocal, engine, Base
from app.models import Depot, CollectionPoint, CPDepotDistance, User
from app.auth import get_password_hash
from math import radians, sin, cos, sqrt, atan2

# Create tables
Base.metadata.create_all(bind=engine)

db = SessionLocal()

# Check if already has data
existing_depots = db.query(Depot).count()
if existing_depots > 0:
    print(f"Already have {existing_depots} depots - clearing and reloading...")
    db.query(CPDepotDistance).delete()
    db.query(CollectionPoint).delete()
    db.query(Depot).delete()
    db.commit()

# Load Depots
print("Loading depots...")
depots_df = pd.read_excel(r"C:\PowerBI God Forsaken Files\Depot Listing D numbers with Capacities.xlsx")
for _, row in depots_df.iterrows():
    depot = Depot(
        depot_id=str(row.iloc[0]).strip(),
        name=str(row.iloc[1]).strip(),
        latitude=float(row.iloc[2]),
        longitude=float(row.iloc[3]),
        daily_capacity=int(row.iloc[4]),
        is_active=True
    )
    db.add(depot)
db.commit()
print(f"Loaded {len(depots_df)} depots")

# Load Collection Points
print("Loading collection points...")
cps_df = pd.read_excel(r"C:\PowerBI God Forsaken Files\CPID list with Lon And Lat.xlsx")
for _, row in cps_df.iterrows():
    cp = CollectionPoint(
        cpid=str(row.iloc[0]).strip(),
        name=str(row.iloc[1]).strip(),
        latitude=float(row.iloc[2]),
        longitude=float(row.iloc[3]),
        is_active=True
    )
    db.add(cp)
db.commit()
print(f"Loaded {len(cps_df)} collection points")

# Calculate distances
print("Calculating distances (this may take a minute)...")
def haversine(lat1, lon1, lat2, lon2):
    R = 3959
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat/2)**2 + cos(lat1)*cos(lat2)*sin(dlon/2)**2
    return R * 2 * atan2(sqrt(a), sqrt(1-a))

depots = db.query(Depot).all()
cps = db.query(CollectionPoint).all()

for i, cp in enumerate(cps):
    distances = []
    for d in depots:
        dist = haversine(cp.latitude, cp.longitude, d.latitude, d.longitude)
        distances.append((d.depot_id, dist))
    distances.sort(key=lambda x: x[1])
    for rank, (depot_id, dist) in enumerate(distances, 1):
        db.add(CPDepotDistance(cpid=cp.cpid, depot_id=depot_id, distance_miles=round(dist, 2), rank=rank))
    if (i + 1) % 50 == 0:
        print(f"  Processed {i + 1}/{len(cps)} collection points...")
        db.commit()

db.commit()
print("Distances calculated!")

# Make sure admin user exists
admin = db.query(User).filter(User.username == "admin").first()
if not admin:
    admin = User(username="admin", email="admin@dx.com", password_hash=get_password_hash("admin123"), role="Admin", is_active=True)
    db.add(admin)
    db.commit()
    print("Admin user created")

db.close()
print("\nDone! You can now start the server.")
