import pandas as pd
from app.database import SessionLocal
from app.models import CollectionPoint, Depot, CPDepotDistance
from math import radians, sin, cos, sqrt, atan2

def haversine_miles(lat1, lon1, lat2, lon2):
    R = 3959
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    return R * c

def import_collection_points(filepath):
    db = SessionLocal()
    df = pd.read_excel(filepath)
    
    count = 0
    for _, row in df.iterrows():
        existing = db.query(CollectionPoint).filter(CollectionPoint.cpid == row['CPID']).first()
        if existing:
            continue
        
        cp = CollectionPoint(
            cpid=row['CPID'],
            name=row['Collection Name'],
            latitude=row['Latitude'],
            longitude=row['Longitude'],
            is_active=True
        )
        db.add(cp)
        count += 1
    
    db.commit()
    db.close()
    print(f"Imported {count} collection points.")

def import_depots(filepath):
    db = SessionLocal()
    df = pd.read_excel(filepath)
    
    count = 0
    for _, row in df.iterrows():
        existing = db.query(Depot).filter(Depot.depot_id == row['DepotID']).first()
        if existing:
            continue
        
        depot = Depot(
            depot_id=row['DepotID'],
            name=row['DepotName'],
            latitude=row['Latitude'],
            longitude=row['Longitude'],
            daily_capacity=int(row['Daily Capacity']) if pd.notna(row['Daily Capacity']) else 0,
            is_active=True
        )
        db.add(depot)
        count += 1
    
    db.commit()
    db.close()
    print(f"Imported {count} depots.")

def calculate_distances():
    db = SessionLocal()
    
    existing_count = db.query(CPDepotDistance).count()
    if existing_count > 0:
        print(f"Distances already calculated ({existing_count} records). Skipping.")
        db.close()
        return
    
    cps = db.query(CollectionPoint).all()
    depots = db.query(Depot).all()
    
    print(f"Calculating distances for {len(cps)} CPs to {len(depots)} depots...")
    
    count = 0
    for cp in cps:
        distances = []
        for depot in depots:
            dist = haversine_miles(cp.latitude, cp.longitude, depot.latitude, depot.longitude)
            distances.append((depot.depot_id, dist))
        
        distances.sort(key=lambda x: x[1])
        
        for rank, (depot_id, dist) in enumerate(distances, 1):
            record = CPDepotDistance(
                cpid=cp.cpid,
                depot_id=depot_id,
                distance_miles=round(dist, 2),
                rank=rank
            )
            db.add(record)
            count += 1
        
        if count % 5000 == 0:
            print(f"  Processed {count} distance records...")
            db.commit()
    
    db.commit()
    db.close()
    print(f"Calculated {count} distance records.")

if __name__ == "__main__":
    print("Starting data import...")
    print("")
    import_collection_points(r"C:\Projects\DXFreightRouting\data\CPID list with Lon And Lat.xlsx")
    print("")
    import_depots(r"C:\Projects\DXFreightRouting\data\Depot Listing D numbers with Capacities.xlsx")
    print("")
    calculate_distances()
    print("")
    print("Import complete!")
