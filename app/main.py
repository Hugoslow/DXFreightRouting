from fastapi import FastAPI, Request, Depends, Query, UploadFile, File, Form
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from datetime import date, datetime, timedelta
from app.routers import auth_router
from app.auth import get_current_user_from_cookie
from app.database import get_db
from app.models import User, CollectionPoint, Depot, DailyVolume, ManualOverride, AuditLog, CPDepotDistance, CapacityOverride
import os

app = FastAPI(title="DX Freight Routing System")

@app.post("/calculate-distances")
@app.get("/calculate-distances")
def calculate_distances():
    from app.database import SessionLocal
    from app.models import Depot, CollectionPoint, CPDepotDistance
    from math import radians, sin, cos, sqrt, atan2
    
    def haversine(lat1, lon1, lat2, lon2):
        R = 3959
        lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
        dlat, dlon = lat2 - lat1, lon2 - lon1
        a = sin(dlat/2)**2 + cos(lat1)*cos(lat2)*sin(dlon/2)**2
        return R * 2 * atan2(sqrt(a), sqrt(1-a))
    
    db = SessionLocal()
    try:
        # Clear existing distances
        db.query(CPDepotDistance).delete()
        db.commit()
        
        depots = db.query(Depot).all()
        cps = db.query(CollectionPoint).all()
        
        count = 0
        for cp in cps:
            distances = [(d.depot_id, haversine(cp.latitude, cp.longitude, d.latitude, d.longitude)) for d in depots]
            distances.sort(key=lambda x: x[1])
            for rank, (depot_id, dist) in enumerate(distances, 1):
                db.add(CPDepotDistance(cpid=cp.cpid, depot_id=depot_id, distance_miles=round(dist, 2), rank=rank))
            count += 1
        db.commit()
        return {"message": f"Calculated distances for {count} collection points to {len(depots)} depots!"}
    finally:
        db.close()

        

@app.get("/cleanup-depots")
def cleanup_depots(db: Session = Depends(get_db)):
    from app.models import Depot, CPDepotDistance, CapacityOverride, ManualOverride
    
    # These are the ONLY depot IDs that should exist
    valid_depot_ids = [
        "D0012", "D0056", "D0050", "D0051", "D0080", "D0077", "D0060", "D0071",
        "D0070", "D0032", "D0052", "D0034", "D0090", "D0014", "D0201", "D0073",
        "D0202", "D0059", "D0203", "D0015", "D0023", "D0053", "D0033", "D0047",
        "D0011", "D0048", "D0204", "D0055", "D0058", "D0054", "D0030", "D0025",
        "D0020", "D0022", "D0044", "D0049", "D0042", "D0999", "D0998", "D0013",
        "D0072", "D0031", "D0024", "D0035", "D0092", "D0036", "D0069", "D0205",
        "D0206", "D0075", "D0076", "D0037"
    ]
    
    # Delete related records first
    distances_deleted = db.query(CPDepotDistance).filter(
        ~CPDepotDistance.depot_id.in_(valid_depot_ids)
    ).delete(synchronize_session=False)
    
    capacity_overrides_deleted = db.query(CapacityOverride).filter(
        ~CapacityOverride.depot_id.in_(valid_depot_ids)
    ).delete(synchronize_session=False)
    
    manual_overrides_deleted = db.query(ManualOverride).filter(
        ~ManualOverride.to_depot_id.in_(valid_depot_ids)
    ).delete(synchronize_session=False)
    
    # Now delete the old depots
    depots_deleted = db.query(Depot).filter(
        ~Depot.depot_id.in_(valid_depot_ids)
    ).delete(synchronize_session=False)
    
    db.commit()
    
    return {
        "message": "Old depots removed!",
        "depots_deleted": depots_deleted,
        "distance_records_deleted": distances_deleted,
        "capacity_overrides_deleted": capacity_overrides_deleted,
        "manual_overrides_deleted": manual_overrides_deleted
    }

app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

app.include_router(auth_router.router)

# Configuration for watched folder
WATCHED_FOLDER_INCOMING = os.getenv("WATCHED_FOLDER_INCOMING", "C:/VolumeImports/Incoming")
WATCHED_FOLDER_PROCESSED = os.getenv("WATCHED_FOLDER_PROCESSED", "C:/VolumeImports/Processed")
WATCHED_FOLDER_ERRORS = os.getenv("WATCHED_FOLDER_ERRORS", "C:/VolumeImports/Errors")

# Session timeout in minutes
SESSION_TIMEOUT_MINUTES = int(os.getenv("SESSION_TIMEOUT_MINUTES", "30"))


def calculate_cost(distance_miles):
    """Calculate transport cost: £50 base + £1.50/mile, minimum £120"""
    cost = 50 + (distance_miles * 1.50)
    return max(cost, 120)


def get_allocations(db: Session, selected_date: date):
    """Core routing logic - allocates trailers from CPs to depots based on distance ranking and capacity"""
    volumes = db.query(DailyVolume).filter(DailyVolume.date == selected_date).all()
    
    if not volumes:
        return [], []
    
    overrides = db.query(ManualOverride).filter(ManualOverride.date == selected_date).all()
    override_map = {(o.cpid, o.trailer_number): o.to_depot_id for o in overrides}
    
    capacity_overrides = db.query(CapacityOverride).filter(CapacityOverride.date == selected_date).all()
    capacity_override_map = {co.depot_id: co.override_capacity for co in capacity_overrides}
    
    depots = db.query(Depot).filter(Depot.is_active == True).all()
    depot_map = {d.depot_id: d for d in depots}
    
    depot_allocated = {d.depot_id: 0 for d in depots}
    depot_capacities = {}
    for d in depots:
        if d.depot_id in capacity_override_map:
            depot_capacities[d.depot_id] = capacity_override_map[d.depot_id]
        else:
            depot_capacities[d.depot_id] = d.daily_capacity
    
    allocations = []
    
    for volume in volumes:
        cp = db.query(CollectionPoint).filter(CollectionPoint.cpid == volume.cpid).first()
        if not cp:
            continue
        
        parcels_per_trailer = volume.parcels // volume.trailers if volume.trailers > 0 else volume.parcels
        remainder = volume.parcels % volume.trailers if volume.trailers > 0 else 0
        
        distances = db.query(CPDepotDistance).filter(
            CPDepotDistance.cpid == volume.cpid
        ).order_by(CPDepotDistance.rank).all()
        
        for trailer_num in range(1, volume.trailers + 1):
            trailer_parcels = parcels_per_trailer + (1 if trailer_num <= remainder else 0)
            
            override_key = (volume.cpid, trailer_num)
            if override_key in override_map:
                assigned_depot_id = override_map[override_key]
                is_override = True
            else:
                assigned_depot_id = None
                for dist in distances:
                    depot_cap = depot_capacities.get(dist.depot_id, 0)
                    if depot_cap > 0 and depot_allocated[dist.depot_id] + trailer_parcels <= depot_cap:
                        assigned_depot_id = dist.depot_id
                        break
                
                if not assigned_depot_id and distances:
                    for dist in distances:
                        if depot_capacities.get(dist.depot_id, 0) > 0:
                            assigned_depot_id = dist.depot_id
                            break
                
                is_override = False
            
            if assigned_depot_id:
                depot_allocated[assigned_depot_id] += trailer_parcels
                
                dist_record = next((d for d in distances if d.depot_id == assigned_depot_id), None)
                distance = dist_record.distance_miles if dist_record else 0
                
                depot = depot_map.get(assigned_depot_id)
                
                allocations.append({
                    'cpid': volume.cpid,
                    'cp_name': cp.name,
                    'trailer_num': trailer_num,
                    'parcels': trailer_parcels,
                    'depot_id': assigned_depot_id,
                    'depot_name': depot.name if depot else assigned_depot_id,
                    'distance': distance,
                    'cost': calculate_cost(distance),
                    'is_override': is_override
                })
    
    depot_summary = []
    for depot_id, allocated in depot_allocated.items():
        if allocated > 0:
            depot = depot_map.get(depot_id)
            capacity = depot_capacities.get(depot_id, 0)
            utilisation = (allocated / capacity * 100) if capacity > 0 else 0
            depot_summary.append({
                'depot_id': depot_id,
                'name': depot.name if depot else depot_id,
                'allocated_parcels': allocated,
                'capacity': capacity,
                'utilisation': utilisation
            })
    
    depot_summary.sort(key=lambda x: x['utilisation'], reverse=True)
    
    return allocations, depot_summary


@app.get("/")
def home(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_from_cookie(request, db)
    if user:
        return RedirectResponse(url="/dashboard", status_code=303)
    return RedirectResponse(url="/login", status_code=303)


@app.get("/dashboard")
def dashboard(
    request: Request,
    db: Session = Depends(get_db),
    date_str: str = Query(None, alias="date")
):
    user = get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    
    selected_date = date.today()
    if date_str:
        try:
            selected_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            pass
    
    allocations, depot_summary = get_allocations(db, selected_date)
    
    total_trailers = len(allocations)
    total_parcels = sum(a['parcels'] for a in allocations)
    total_cost = sum(a['cost'] for a in allocations)
    active_cps = len(set(a['cpid'] for a in allocations))
    depots_used = len(depot_summary)
    
    # Only get CPs and depots that are in today's allocations (for map display)
    active_cpids = set(a['cpid'] for a in allocations)
    active_depot_ids = set(a['depot_id'] for a in allocations)
    
    depots = db.query(Depot).filter(Depot.depot_id.in_(active_depot_ids)).all() if active_depot_ids else []
    collection_points = db.query(CollectionPoint).filter(CollectionPoint.cpid.in_(active_cpids)).all() if active_cpids else []
    
    # Get CP volumes for map popups
    volumes = db.query(DailyVolume).filter(DailyVolume.date == selected_date).all()
    cp_volumes = []
    for v in volumes:
        cp = db.query(CollectionPoint).filter(CollectionPoint.cpid == v.cpid).first()
        if cp:
            cp_volumes.append({
                'cpid': v.cpid,
                'name': cp.name,
                'parcels': v.parcels,
                'trailers': v.trailers
            })
    
    # Get depot stats for map popups
    capacity_overrides = db.query(CapacityOverride).filter(CapacityOverride.date == selected_date).all()
    capacity_override_map = {co.depot_id: co.override_capacity for co in capacity_overrides}
    
    depot_stats = []
    for depot_id in active_depot_ids:
        depot = db.query(Depot).filter(Depot.depot_id == depot_id).first()
        if depot:
            depot_allocations = [a for a in allocations if a['depot_id'] == depot_id]
            depot_parcels = sum(a['parcels'] for a in depot_allocations)
            depot_trailers = len(depot_allocations)
            capacity = capacity_override_map.get(depot_id, depot.daily_capacity)
            depot_stats.append({
                'depot_id': depot_id,
                'name': depot.name,
                'parcels': depot_parcels,
                'trailers': depot_trailers,
                'capacity': capacity
            })
    
    # What's New since last login
    whats_new = {"new_volumes": 0, "new_overrides": 0, "failed_imports": 0, "total_changes": 0}
    if user.last_login:
        new_volumes = db.query(DailyVolume).filter(DailyVolume.imported_at > user.last_login).count()
        new_overrides = db.query(ManualOverride).filter(ManualOverride.created_at > user.last_login).count()
        failed_imports = db.query(AuditLog).filter(
            AuditLog.timestamp > user.last_login,
            AuditLog.action_type == "VOLUME_IMPORT_FAILED"
        ).count()
        whats_new = {
            "new_volumes": new_volumes,
            "new_overrides": new_overrides,
            "failed_imports": failed_imports,
            "total_changes": new_volumes + new_overrides
        }
    
    stats = {
        "total_cps": active_cps,
        "total_trailers": total_trailers,
        "total_parcels": f"{total_parcels:,}",
        "depots_used": depots_used,
        "estimated_cost": f"{total_cost:,.2f}"
    }
    
    # Build allocation lines for map
    allocation_lines = []
    for alloc in allocations:
        cp = next((c for c in collection_points if c.cpid == alloc['cpid']), None)
        depot = next((d for d in depots if d.depot_id == alloc['depot_id']), None)
        if cp and depot:
            allocation_lines.append({
                'cp_lat': cp.latitude,
                'cp_lon': cp.longitude,
                'depot_lat': depot.latitude,
                'depot_lon': depot.longitude,
                'cpid': alloc['cpid'],
                'depot_id': alloc['depot_id'],
                'is_override': alloc['is_override']
            })
    
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "user": user,
        "active_page": "dashboard",
        "selected_date": selected_date.strftime("%Y-%m-%d"),
        "stats": stats,
        "depots": depots,
        "collection_points": collection_points,
        "cp_volumes": cp_volumes,
        "depot_stats": depot_stats,
        "whats_new": whats_new,
        "allocation_lines": allocation_lines
    })


@app.get("/collections")
def collections_page(
    request: Request,
    db: Session = Depends(get_db),
    date_str: str = Query(None, alias="date"),
    cpid: str = Query(None),
    depot: str = Query(None)
):
    user = get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    
    selected_date = date.today()
    if date_str:
        try:
            selected_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            pass
    
    allocations, depot_summary = get_allocations(db, selected_date)
    
    # Apply filters
    filter_type = None
    filter_value = None
    
    if cpid:
        allocations = [a for a in allocations if a['cpid'] == cpid]
        filter_type = "CPID"
        filter_value = cpid
    elif depot:
        allocations = [a for a in allocations if a['depot_id'] == depot]
        filter_type = "Depot"
        depot_obj = db.query(Depot).filter(Depot.depot_id == depot).first()
        filter_value = depot_obj.name if depot_obj else depot
    
    return templates.TemplateResponse("collections.html", {
        "request": request,
        "user": user,
        "active_page": "collections",
        "selected_date": selected_date.strftime("%Y-%m-%d"),
        "allocations": allocations,
        "depot_summary": depot_summary,
        "filter_type": filter_type,
        "filter_value": filter_value
    })


@app.get("/import-volumes")
def import_volumes_page(
    request: Request,
    db: Session = Depends(get_db),
    message: str = Query(None),
    error: bool = Query(False)
):
    user = get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    
    if user.role not in ["Admin", "Operator"]:
        return RedirectResponse(url="/dashboard", status_code=303)
    
    from sqlalchemy import func
    recent = db.query(
        DailyVolume.date,
        func.count(DailyVolume.id).label('count'),
        func.max(DailyVolume.imported_at).label('imported_at'),
        DailyVolume.imported_by
    ).group_by(DailyVolume.date, DailyVolume.imported_by).order_by(DailyVolume.date.desc()).limit(10).all()
    
    recent_imports = []
    for r in recent:
        imp_user = db.query(User).filter(User.id == r.imported_by).first()
        recent_imports.append({
            'date': r.date.strftime('%Y-%m-%d'),
            'count': r.count,
            'user': imp_user.username if imp_user else 'System',
            'imported_at': r.imported_at.strftime('%Y-%m-%d %H:%M') if r.imported_at else ''
        })
    
    # Folder paths for display
    folder_info = {
        'incoming': WATCHED_FOLDER_INCOMING,
        'processed': WATCHED_FOLDER_PROCESSED,
        'errors': WATCHED_FOLDER_ERRORS
    }
    
    return templates.TemplateResponse("import_volumes.html", {
        "request": request,
        "user": user,
        "active_page": "import-volumes",
        "message": message,
        "error": error,
        "recent_imports": recent_imports,
        "folder_info": folder_info
    })


@app.post("/import-volumes")
async def import_volumes_upload(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    user = get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    
    if user.role not in ["Admin", "Operator"]:
        return RedirectResponse(url="/dashboard", status_code=303)
    
    import pandas as pd
    from io import BytesIO
    
    try:
        contents = await file.read()
        df = pd.read_excel(BytesIO(contents))
        
        df.columns = [str(c).strip() for c in df.columns]
        
        required_cols = ['Date', 'CPID', 'Parcels', 'Trailers']
        missing = [c for c in required_cols if c not in df.columns]
        if missing:
            return RedirectResponse(
                url=f"/import-volumes?message=Missing columns: {', '.join(missing)}&error=true",
                status_code=303
            )
        
        imported = 0
        skipped = 0
        errors = []
        
        for idx, row in df.iterrows():
            try:
                row_date = pd.to_datetime(row['Date']).date()
                cpid = str(row['CPID']).strip()
                parcels = int(row['Parcels'])
                trailers = int(row['Trailers'])
                
                cp = db.query(CollectionPoint).filter(CollectionPoint.cpid == cpid).first()
                if not cp:
                    errors.append(f"Row {idx+2}: CPID '{cpid}' not found")
                    skipped += 1
                    continue
                
                existing = db.query(DailyVolume).filter(
                    DailyVolume.date == row_date,
                    DailyVolume.cpid == cpid
                ).first()
                
                if existing:
                    skipped += 1
                    continue
                
                volume = DailyVolume(
                    date=row_date,
                    cpid=cpid,
                    parcels=parcels,
                    trailers=trailers,
                    imported_by=user.id,
                    imported_at=datetime.utcnow()
                )
                db.add(volume)
                imported += 1
                
            except Exception as e:
                errors.append(f"Row {idx+2}: {str(e)}")
                skipped += 1
        
        db.commit()
        
        audit = AuditLog(
            user_id=user.id,
            action_type="VOLUME_IMPORT",
            entity_type="DailyVolume",
            entity_id=file.filename,
            old_value=None,
            new_value=f"Imported {imported}, skipped {skipped}",
            ip_address=request.client.host
        )
        db.add(audit)
        db.commit()
        
        message = f"Imported {imported} records, skipped {skipped}"
        if errors:
            message += f". Errors: {'; '.join(errors[:3])}"
        
        return RedirectResponse(url=f"/import-volumes?message={message}", status_code=303)
        
    except Exception as e:
        return RedirectResponse(
            url=f"/import-volumes?message=Error reading file: {str(e)}&error=true",
            status_code=303
        )


@app.get("/download-template/{template_type}")
def download_template(template_type: str, request: Request, db: Session = Depends(get_db)):
    """Download import template files"""
    user = get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    
    import pandas as pd
    from io import BytesIO
    
    if template_type == "volumes":
        df = pd.DataFrame({
            'Date': ['2026-01-07', '2026-01-07', '2026-01-08'],
            'CPID': ['CP001', 'CP002', 'CP001'],
            'Parcels': [5000, 3500, 4200],
            'Trailers': [3, 2, 3]
        })
        filename = "volumes_import_template.xlsx"
    elif template_type == "capacity":
        df = pd.DataFrame({
            'Date': ['2026-01-07', '2026-01-07'],
            'DepotID': ['D001', 'D002'],
            'OverrideCapacity': [15000, 8000],
            'Reason': ['Bank holiday reduced staff', 'Vehicle maintenance']
        })
        filename = "capacity_override_template.xlsx"
    elif template_type == "cplist":
        cps = db.query(CollectionPoint).order_by(CollectionPoint.cpid).all()
        df = pd.DataFrame({
            'CPID': [cp.cpid for cp in cps],
            'Name': [cp.name for cp in cps]
        })
        filename = "collection_point_list.xlsx"
    else:
        return RedirectResponse(url="/import-volumes?message=Unknown template type&error=true", status_code=303)
    
    output = BytesIO()
    df.to_excel(output, index=False)
    output.seek(0)
    
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.get("/overrides")
def overrides_page(
    request: Request,
    db: Session = Depends(get_db),
    date_str: str = Query(None, alias="date"),
    message: str = Query(None),
    error: bool = Query(False)
):
    user = get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    
    selected_date = date.today()
    if date_str:
        try:
            selected_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            pass
    
    overrides = db.query(ManualOverride).filter(ManualOverride.date == selected_date).all()
    
    override_list = []
    for o in overrides:
        cp = db.query(CollectionPoint).filter(CollectionPoint.cpid == o.cpid).first()
        depot = db.query(Depot).filter(Depot.depot_id == o.to_depot_id).first()
        created_by = db.query(User).filter(User.id == o.created_by).first()
        override_list.append({
            'id': o.id,
            'cpid': o.cpid,
            'cp_name': cp.name if cp else '',
            'trailer_number': o.trailer_number,
            'to_depot_id': o.to_depot_id,
            'depot_name': depot.name if depot else '',
            'created_by_name': created_by.username if created_by else '',
            'created_at': o.created_at.strftime('%Y-%m-%d %H:%M') if o.created_at else ''
        })
    
    # BUG-002 FIX: Only show CPs that have volumes for the selected date
    volumes_for_date = db.query(DailyVolume).filter(DailyVolume.date == selected_date).all()
    cpids_with_volumes = set(v.cpid for v in volumes_for_date)
    
    # Get max trailer count for each CP with volumes
    cp_trailer_counts = {v.cpid: v.trailers for v in volumes_for_date}
    
    collection_points = db.query(CollectionPoint).filter(
        CollectionPoint.is_active == True,
        CollectionPoint.cpid.in_(cpids_with_volumes)
    ).order_by(CollectionPoint.cpid).all() if cpids_with_volumes else []
    
    # Add trailer count to each CP for the dropdown
    cp_list = []
    for cp in collection_points:
        cp_list.append({
            'cpid': cp.cpid,
            'name': cp.name,
            'max_trailers': cp_trailer_counts.get(cp.cpid, 1)
        })
    
    depots = db.query(Depot).filter(Depot.is_active == True).order_by(Depot.depot_id).all()
    
    return templates.TemplateResponse("overrides.html", {
        "request": request,
        "user": user,
        "active_page": "overrides",
        "selected_date": selected_date.strftime("%Y-%m-%d"),
        "overrides": override_list,
        "collection_points": cp_list,
        "depots": depots,
        "message": message,
        "error": error
    })


@app.post("/overrides/add")
def add_override(
    request: Request,
    db: Session = Depends(get_db),
    date: str = Form(...),
    cpid: str = Form(...),
    trailer_number: int = Form(...),
    to_depot_id: str = Form(...)
):
    user = get_current_user_from_cookie(request, db)
    if not user or user.role not in ['Admin', 'Operator']:
        return RedirectResponse(url="/login", status_code=303)
    
    override_date = datetime.strptime(date, "%Y-%m-%d").date()
    
    existing = db.query(ManualOverride).filter(
        ManualOverride.date == override_date,
        ManualOverride.cpid == cpid,
        ManualOverride.trailer_number == trailer_number
    ).first()
    
    if existing:
        return RedirectResponse(
            url=f"/overrides?date={date}&message=Override already exists for this CP and trailer&error=true",
            status_code=303
        )
    
    override = ManualOverride(
        date=override_date,
        cpid=cpid,
        trailer_number=trailer_number,
        to_depot_id=to_depot_id,
        created_by=user.id,
        created_at=datetime.utcnow()
    )
    db.add(override)
    
    audit = AuditLog(
        user_id=user.id,
        action_type="OVERRIDE_CREATED",
        entity_type="ManualOverride",
        entity_id=f"{cpid}-{trailer_number}",
        old_value=None,
        new_value=f"Redirected to {to_depot_id}",
        ip_address=request.client.host
    )
    db.add(audit)
    db.commit()
    
    return RedirectResponse(
        url=f"/overrides?date={date}&message=Override added successfully",
        status_code=303
    )


@app.post("/overrides/delete/{override_id}")
def delete_override(
    request: Request,
    override_id: int,
    db: Session = Depends(get_db),
    date: str = Form(...)
):
    user = get_current_user_from_cookie(request, db)
    if not user or user.role not in ['Admin', 'Operator']:
        return RedirectResponse(url="/login", status_code=303)
    
    override = db.query(ManualOverride).filter(ManualOverride.id == override_id).first()
    
    if override:
        audit = AuditLog(
            user_id=user.id,
            action_type="OVERRIDE_DELETED",
            entity_type="ManualOverride",
            entity_id=f"{override.cpid}-{override.trailer_number}",
            old_value=f"Was redirected to {override.to_depot_id}",
            new_value=None,
            ip_address=request.client.host
        )
        db.add(audit)
        db.delete(override)
        db.commit()
    
    return RedirectResponse(
        url=f"/overrides?date={date}&message=Override deleted",
        status_code=303
    )


@app.get("/audit-log")
def audit_log_page(
    request: Request,
    db: Session = Depends(get_db),
    from_date: str = Query(None),
    to_date: str = Query(None),
    action_type: str = Query(None),
    user_id: int = Query(None)
):
    user = get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    
    query = db.query(AuditLog).order_by(AuditLog.timestamp.desc())
    
    if from_date:
        try:
            from_dt = datetime.strptime(from_date, "%Y-%m-%d")
            query = query.filter(AuditLog.timestamp >= from_dt)
        except ValueError:
            pass
    
    if to_date:
        try:
            to_dt = datetime.strptime(to_date, "%Y-%m-%d")
            to_dt = to_dt.replace(hour=23, minute=59, second=59)
            query = query.filter(AuditLog.timestamp <= to_dt)
        except ValueError:
            pass
    
    if action_type:
        query = query.filter(AuditLog.action_type == action_type)
    
    if user_id:
        query = query.filter(AuditLog.user_id == user_id)
    
    logs = query.limit(500).all()
    
    log_list = []
    for log in logs:
        log_user = db.query(User).filter(User.id == log.user_id).first()
        log_list.append({
            'timestamp': log.timestamp.strftime('%Y-%m-%d %H:%M:%S') if log.timestamp else '',
            'username': log_user.username if log_user else 'System',
            'action_type': log.action_type,
            'entity_type': log.entity_type,
            'entity_id': log.entity_id or '',
            'old_value': log.old_value or '',
            'new_value': log.new_value or '',
            'ip_address': log.ip_address or ''
        })
    
    users = db.query(User).all()
    
    return templates.TemplateResponse("audit_log.html", {
        "request": request,
        "user": user,
        "active_page": "audit-log",
        "logs": log_list,
        "users": users,
        "from_date": from_date or '',
        "to_date": to_date or '',
        "action_type": action_type or '',
        "user_id": user_id
    })


@app.get("/audit-log/export")
def export_audit_log(
    request: Request,
    db: Session = Depends(get_db),
    from_date: str = Query(None),
    to_date: str = Query(None),
    action_type: str = Query(None),
    user_id: int = Query(None)
):
    """Export audit log to Excel"""
    user = get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    
    import pandas as pd
    from io import BytesIO
    
    query = db.query(AuditLog).order_by(AuditLog.timestamp.desc())
    
    if from_date:
        try:
            from_dt = datetime.strptime(from_date, "%Y-%m-%d")
            query = query.filter(AuditLog.timestamp >= from_dt)
        except ValueError:
            pass
    
    if to_date:
        try:
            to_dt = datetime.strptime(to_date, "%Y-%m-%d")
            to_dt = to_dt.replace(hour=23, minute=59, second=59)
            query = query.filter(AuditLog.timestamp <= to_dt)
        except ValueError:
            pass
    
    if action_type:
        query = query.filter(AuditLog.action_type == action_type)
    
    if user_id:
        query = query.filter(AuditLog.user_id == user_id)
    
    logs = query.all()
    
    data = []
    for log in logs:
        log_user = db.query(User).filter(User.id == log.user_id).first()
        data.append({
            'Timestamp': log.timestamp.strftime('%Y-%m-%d %H:%M:%S') if log.timestamp else '',
            'User': log_user.username if log_user else 'System',
            'Action': log.action_type,
            'Entity Type': log.entity_type,
            'Entity ID': log.entity_id or '',
            'Old Value': log.old_value or '',
            'New Value': log.new_value or '',
            'IP Address': log.ip_address or ''
        })
    
    df = pd.DataFrame(data)
    output = BytesIO()
    df.to_excel(output, index=False)
    output.seek(0)
    
    filename = f"audit_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.get("/depot-allocations")
def depot_allocations_page(
    request: Request,
    db: Session = Depends(get_db),
    date_str: str = Query(None, alias="date"),
    depot_id: str = Query(None, alias="depot")
):
    user = get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    
    selected_date = date.today()
    if date_str:
        try:
            selected_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            pass
    
    allocations, _ = get_allocations(db, selected_date)
    
    # Get capacity overrides for the date
    capacity_overrides = db.query(CapacityOverride).filter(CapacityOverride.date == selected_date).all()
    capacity_override_map = {co.depot_id: co.override_capacity for co in capacity_overrides}
    
    depot_data = {}
    for alloc in allocations:
        did = alloc['depot_id']
        if did not in depot_data:
            depot = db.query(Depot).filter(Depot.depot_id == did).first()
            capacity = capacity_override_map.get(did, depot.daily_capacity if depot else 0)
            depot_data[did] = {
                'depot_id': did,
                'name': alloc['depot_name'],
                'capacity': capacity,
                'has_override': did in capacity_override_map,
                'allocated_parcels': 0,
                'trailer_count': 0,
                'allocations': []
            }
        depot_data[did]['allocated_parcels'] += alloc['parcels']
        depot_data[did]['trailer_count'] += 1
        depot_data[did]['allocations'].append(alloc)
    
    for did in depot_data:
        cap = depot_data[did]['capacity']
        alloc_parcels = depot_data[did]['allocated_parcels']
        depot_data[did]['utilisation'] = (alloc_parcels / cap * 100) if cap > 0 else 0
    
    depot_summary = sorted(depot_data.values(), key=lambda x: x['utilisation'], reverse=True)
    
    selected_depot = None
    if depot_id and depot_id in depot_data:
        selected_depot = depot_data[depot_id]
    
    return templates.TemplateResponse("depot_allocations.html", {
        "request": request,
        "user": user,
        "active_page": "depot-allocations",
        "selected_date": selected_date.strftime("%Y-%m-%d"),
        "depot_summary": depot_summary,
        "selected_depot": selected_depot
    })


@app.get("/expected-costs")
def expected_costs_page(
    request: Request,
    db: Session = Depends(get_db),
    date_str: str = Query(None, alias="date"),
    cpid: str = Query(None),
    depot: str = Query(None)
):
    user = get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    
    selected_date = date.today()
    if date_str:
        try:
            selected_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            pass
    
    allocations, _ = get_allocations(db, selected_date)
    
    # Apply filters for clickable filtering
    filter_type = None
    filter_value = None
    
    if cpid:
        allocations = [a for a in allocations if a['cpid'] == cpid]
        filter_type = "CPID"
        filter_value = cpid
    elif depot:
        allocations = [a for a in allocations if a['depot_id'] == depot]
        filter_type = "Depot"
        depot_obj = db.query(Depot).filter(Depot.depot_id == depot).first()
        filter_value = depot_obj.name if depot_obj else depot
    
    total_cost = sum(a['cost'] for a in allocations)
    total_miles = sum(a['distance'] for a in allocations)
    total_trailers = len(allocations)
    total_parcels = sum(a['parcels'] for a in allocations)
    avg_cost = total_cost / total_trailers if total_trailers > 0 else 0
    cost_per_parcel = total_cost / total_parcels if total_parcels > 0 else 0
    
    summary = {
        'total_cost': total_cost,
        'total_trailers': total_trailers,
        'total_parcels': total_parcels,
        'avg_cost_per_trailer': avg_cost,
        'cost_per_parcel': cost_per_parcel,
        'total_miles': total_miles
    }
    
    cp_data = {}
    for alloc in allocations:
        cpid_key = alloc['cpid']
        if cpid_key not in cp_data:
            cp_data[cpid_key] = {
                'cpid': cpid_key,
                'name': alloc['cp_name'],
                'trailers': 0,
                'parcels': 0,
                'total_miles': 0,
                'total_cost': 0
            }
        cp_data[cpid_key]['trailers'] += 1
        cp_data[cpid_key]['parcels'] += alloc['parcels']
        cp_data[cpid_key]['total_miles'] += alloc['distance']
        cp_data[cpid_key]['total_cost'] += alloc['cost']
    
    for cpid_key in cp_data:
        cp_data[cpid_key]['avg_cost'] = cp_data[cpid_key]['total_cost'] / cp_data[cpid_key]['trailers']
        cp_data[cpid_key]['cost_per_parcel'] = cp_data[cpid_key]['total_cost'] / cp_data[cpid_key]['parcels'] if cp_data[cpid_key]['parcels'] > 0 else 0
    
    costs_by_cp = sorted(cp_data.values(), key=lambda x: x['total_cost'], reverse=True)
    
    depot_data = {}
    for alloc in allocations:
        did = alloc['depot_id']
        if did not in depot_data:
            depot_data[did] = {
                'depot_id': did,
                'name': alloc['depot_name'],
                'trailers': 0,
                'parcels': 0,
                'total_miles': 0,
                'total_cost': 0
            }
        depot_data[did]['trailers'] += 1
        depot_data[did]['parcels'] += alloc['parcels']
        depot_data[did]['total_miles'] += alloc['distance']
        depot_data[did]['total_cost'] += alloc['cost']
    
    for did in depot_data:
        depot_data[did]['cost_per_parcel'] = depot_data[did]['total_cost'] / depot_data[did]['parcels'] if depot_data[did]['parcels'] > 0 else 0
    
    costs_by_depot = sorted(depot_data.values(), key=lambda x: x['total_cost'], reverse=True)
    
    return templates.TemplateResponse("expected_costs.html", {
        "request": request,
        "user": user,
        "active_page": "expected-costs",
        "selected_date": selected_date.strftime("%Y-%m-%d"),
        "summary": summary,
        "costs_by_cp": costs_by_cp,
        "costs_by_depot": costs_by_depot,
        "filter_type": filter_type,
        "filter_value": filter_value
    })


@app.get("/capacity-overrides")
def capacity_overrides_page(
    request: Request,
    db: Session = Depends(get_db),
    date_str: str = Query(None, alias="date"),
    message: str = Query(None),
    error: bool = Query(False)
):
    """Manage temporary capacity overrides for specific dates"""
    user = get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    
    if user.role not in ["Admin", "Operator"]:
        return RedirectResponse(url="/dashboard", status_code=303)
    
    selected_date = date.today()
    if date_str:
        try:
            selected_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            pass
    
    overrides = db.query(CapacityOverride).filter(CapacityOverride.date == selected_date).all()
    
    override_list = []
    for o in overrides:
        depot = db.query(Depot).filter(Depot.depot_id == o.depot_id).first()
        created_by = db.query(User).filter(User.id == o.created_by).first()
        override_list.append({
            'id': o.id,
            'depot_id': o.depot_id,
            'depot_name': depot.name if depot else '',
            'original_capacity': depot.daily_capacity if depot else 0,
            'override_capacity': o.override_capacity,
            'reason': o.reason or '',
            'created_by_name': created_by.username if created_by else '',
            'created_at': o.created_at.strftime('%Y-%m-%d %H:%M') if o.created_at else ''
        })
    
    depots = db.query(Depot).filter(Depot.is_active == True).order_by(Depot.name).all()
    
    return templates.TemplateResponse("capacity_overrides.html", {
        "request": request,
        "user": user,
        "active_page": "capacity-overrides",
        "selected_date": selected_date.strftime("%Y-%m-%d"),
        "overrides": override_list,
        "depots": depots,
        "message": message,
        "error": error
    })


@app.post("/capacity-overrides/add")
def add_capacity_override(
    request: Request,
    db: Session = Depends(get_db),
    date: str = Form(...),
    depot_id: str = Form(...),
    override_capacity: int = Form(...),
    reason: str = Form("")
):
    user = get_current_user_from_cookie(request, db)
    if not user or user.role not in ['Admin', 'Operator']:
        return RedirectResponse(url="/login", status_code=303)
    
    override_date = datetime.strptime(date, "%Y-%m-%d").date()
    
    existing = db.query(CapacityOverride).filter(
        CapacityOverride.date == override_date,
        CapacityOverride.depot_id == depot_id
    ).first()
    
    if existing:
        return RedirectResponse(
            url=f"/capacity-overrides?date={date}&message=Override already exists for this depot on this date&error=true",
            status_code=303
        )
    
    depot = db.query(Depot).filter(Depot.depot_id == depot_id).first()
    
    override = CapacityOverride(
        date=override_date,
        depot_id=depot_id,
        override_capacity=override_capacity,
        reason=reason,
        created_by=user.id,
        created_at=datetime.utcnow()
    )
    db.add(override)
    
    audit = AuditLog(
        user_id=user.id,
        action_type="CAPACITY_OVERRIDE_CREATED",
        entity_type="CapacityOverride",
        entity_id=f"{depot_id}-{date}",
        old_value=f"Original: {depot.daily_capacity}" if depot else None,
        new_value=f"Override: {override_capacity}. Reason: {reason}",
        ip_address=request.client.host
    )
    db.add(audit)
    db.commit()
    
    return RedirectResponse(
        url=f"/capacity-overrides?date={date}&message=Capacity override added successfully",
        status_code=303
    )


@app.post("/capacity-overrides/delete/{override_id}")
def delete_capacity_override(
    request: Request,
    override_id: int,
    db: Session = Depends(get_db),
    date: str = Form(...)
):
    user = get_current_user_from_cookie(request, db)
    if not user or user.role not in ['Admin', 'Operator']:
        return RedirectResponse(url="/login", status_code=303)
    
    override = db.query(CapacityOverride).filter(CapacityOverride.id == override_id).first()
    
    if override:
        audit = AuditLog(
            user_id=user.id,
            action_type="CAPACITY_OVERRIDE_DELETED",
            entity_type="CapacityOverride",
            entity_id=f"{override.depot_id}-{override.date}",
            old_value=f"Was overridden to {override.override_capacity}",
            new_value=None,
            ip_address=request.client.host
        )
        db.add(audit)
        db.delete(override)
        db.commit()
    
    return RedirectResponse(
        url=f"/capacity-overrides?date={date}&message=Capacity override deleted",
        status_code=303
    )


@app.get("/admin/users")
def user_management_page(
    request: Request,
    db: Session = Depends(get_db),
    message: str = Query(None),
    error: bool = Query(False)
):
    user = get_current_user_from_cookie(request, db)
    if not user or user.role != 'Admin':
        return RedirectResponse(url="/dashboard", status_code=303)
    
    users = db.query(User).order_by(User.username).all()
    
    user_list = []
    for u in users:
        user_list.append({
            'id': u.id,
            'username': u.username,
            'email': u.email,
            'role': u.role,
            'is_active': u.is_active,
            'last_login': u.last_login.strftime('%Y-%m-%d %H:%M') if u.last_login else None,
            'created_at': u.created_at.strftime('%Y-%m-%d') if u.created_at else ''
        })
    
    return templates.TemplateResponse("user_management.html", {
        "request": request,
        "user": user,
        "active_page": "users",
        "users": user_list,
        "message": message,
        "error": error
    })


@app.post("/admin/users/add")
def add_user(
    request: Request,
    db: Session = Depends(get_db),
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    role: str = Form(...)
):
    user = get_current_user_from_cookie(request, db)
    if not user or user.role != 'Admin':
        return RedirectResponse(url="/dashboard", status_code=303)
    
    existing = db.query(User).filter(
        (User.username == username) | (User.email == email)
    ).first()
    
    if existing:
        return RedirectResponse(
            url="/admin/users?message=Username or email already exists&error=true",
            status_code=303
        )
    
    from app.auth import get_password_hash
    
    new_user = User(
        username=username,
        email=email,
        password_hash=get_password_hash(password),
        role=role,
        is_active=True,
        created_at=datetime.utcnow()
    )
    db.add(new_user)
    
    audit = AuditLog(
        user_id=user.id,
        action_type="USER_CREATED",
        entity_type="User",
        entity_id=username,
        old_value=None,
        new_value=f"Role: {role}",
        ip_address=request.client.host
    )
    db.add(audit)
    db.commit()
    
    return RedirectResponse(
        url=f"/admin/users?message=User {username} created successfully",
        status_code=303
    )


@app.post("/admin/users/disable/{user_id}")
def disable_user(
    request: Request,
    user_id: int,
    db: Session = Depends(get_db)
):
    user = get_current_user_from_cookie(request, db)
    if not user or user.role != 'Admin':
        return RedirectResponse(url="/dashboard", status_code=303)
    
    target_user = db.query(User).filter(User.id == user_id).first()
    if target_user and target_user.username != 'admin':
        target_user.is_active = False
        
        audit = AuditLog(
            user_id=user.id,
            action_type="USER_DISABLED",
            entity_type="User",
            entity_id=target_user.username,
            old_value="Active",
            new_value="Disabled",
            ip_address=request.client.host
        )
        db.add(audit)
        db.commit()
    
    return RedirectResponse(url="/admin/users?message=User disabled", status_code=303)


@app.post("/admin/users/enable/{user_id}")
def enable_user(
    request: Request,
    user_id: int,
    db: Session = Depends(get_db)
):
    user = get_current_user_from_cookie(request, db)
    if not user or user.role != 'Admin':
        return RedirectResponse(url="/dashboard", status_code=303)
    
    target_user = db.query(User).filter(User.id == user_id).first()
    if target_user:
        target_user.is_active = True
        
        audit = AuditLog(
            user_id=user.id,
            action_type="USER_ENABLED",
            entity_type="User",
            entity_id=target_user.username,
            old_value="Disabled",
            new_value="Active",
            ip_address=request.client.host
        )
        db.add(audit)
        db.commit()
    
    return RedirectResponse(url="/admin/users?message=User enabled", status_code=303)


@app.post("/admin/users/reset-password/{user_id}")
def reset_password(
    request: Request,
    user_id: int,
    db: Session = Depends(get_db)
):
    user = get_current_user_from_cookie(request, db)
    if not user or user.role != 'Admin':
        return RedirectResponse(url="/dashboard", status_code=303)
    
    target_user = db.query(User).filter(User.id == user_id).first()
    if target_user and target_user.username != 'admin':
        from app.auth import get_password_hash
        new_password = "password123"
        target_user.password_hash = get_password_hash(new_password)
        
        audit = AuditLog(
            user_id=user.id,
            action_type="PASSWORD_RESET",
            entity_type="User",
            entity_id=target_user.username,
            old_value=None,
            new_value="Password reset to default",
            ip_address=request.client.host
        )
        db.add(audit)
        db.commit()
        
        return RedirectResponse(
            url=f"/admin/users?message=Password reset to 'password123' for {target_user.username}",
            status_code=303
        )
    
    return RedirectResponse(url="/admin/users", status_code=303)


@app.get("/admin/setup")
def system_setup_page(
    request: Request,
    db: Session = Depends(get_db),
    message: str = Query(None),
    error: bool = Query(False)
):
    user = get_current_user_from_cookie(request, db)
    if not user or user.role != 'Admin':
        return RedirectResponse(url="/dashboard", status_code=303)
    
    collection_points = db.query(CollectionPoint).order_by(CollectionPoint.cpid).all()
    depots = db.query(Depot).order_by(Depot.depot_id).all()
    
    return templates.TemplateResponse("system_setup.html", {
        "request": request,
        "user": user,
        "active_page": "setup",
        "collection_points": collection_points,
        "depots": depots,
        "message": message,
        "error": error
    })


@app.post("/admin/setup/add-cp")
def add_collection_point(
    request: Request,
    db: Session = Depends(get_db),
    cpid: str = Form(...),
    name: str = Form(...),
    latitude: float = Form(...),
    longitude: float = Form(...)
):
    user = get_current_user_from_cookie(request, db)
    if not user or user.role != 'Admin':
        return RedirectResponse(url="/dashboard", status_code=303)
    
    existing = db.query(CollectionPoint).filter(CollectionPoint.cpid == cpid).first()
    if existing:
        return RedirectResponse(
            url=f"/admin/setup?message=CPID {cpid} already exists&error=true",
            status_code=303
        )
    
    new_cp = CollectionPoint(
        cpid=cpid,
        name=name,
        latitude=latitude,
        longitude=longitude,
        is_active=True
    )
    db.add(new_cp)
    db.commit()
    
    from math import radians, sin, cos, sqrt, atan2
    
    def haversine_miles(lat1, lon1, lat2, lon2):
        R = 3959
        lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
        c = 2 * atan2(sqrt(a), sqrt(1-a))
        return R * c
    
    depots = db.query(Depot).all()
    distances = []
    for depot in depots:
        dist = haversine_miles(latitude, longitude, depot.latitude, depot.longitude)
        distances.append((depot.depot_id, dist))
    
    distances.sort(key=lambda x: x[1])
    
    for rank, (depot_id, dist) in enumerate(distances, 1):
        record = CPDepotDistance(
            cpid=cpid,
            depot_id=depot_id,
            distance_miles=round(dist, 2),
            rank=rank
        )
        db.add(record)
    
    db.commit()
    
    audit = AuditLog(
        user_id=user.id,
        action_type="CP_CREATED",
        entity_type="CollectionPoint",
        entity_id=cpid,
        old_value=None,
        new_value=f"{name} at {latitude}, {longitude}",
        ip_address=request.client.host
    )
    db.add(audit)
    db.commit()
    
    return RedirectResponse(
        url=f"/admin/setup?message=Collection Point {cpid} added with {len(distances)} distance calculations",
        status_code=303
    )


@app.post("/admin/setup/update-capacity")
def update_depot_capacity(
    request: Request,
    db: Session = Depends(get_db),
    depot_id: str = Form(...),
    capacity: int = Form(...)
):
    user = get_current_user_from_cookie(request, db)
    if not user or user.role != 'Admin':
        return RedirectResponse(url="/dashboard", status_code=303)
    
    depot = db.query(Depot).filter(Depot.depot_id == depot_id).first()
    if depot:
        old_capacity = depot.daily_capacity
        depot.daily_capacity = capacity
        
        audit = AuditLog(
            user_id=user.id,
            action_type="DEPOT_CAPACITY_UPDATED",
            entity_type="Depot",
            entity_id=depot_id,
            old_value=f"Capacity: {old_capacity}",
            new_value=f"Capacity: {capacity}",
            ip_address=request.client.host
        )
        db.add(audit)
        db.commit()
        
        return RedirectResponse(
            url=f"/admin/setup?message=Capacity updated for {depot.name}",
            status_code=303
        )
    
    return RedirectResponse(url="/admin/setup?message=Depot not found&error=true", status_code=303)

@app.get("/admin/setup/export-cps")
def export_collection_points(request: Request, db: Session = Depends(get_db)):
    """Export all collection points to Excel"""
    user = get_current_user_from_cookie(request, db)
    if not user or user.role != 'Admin':
        return RedirectResponse(url="/dashboard", status_code=303)
    
    import pandas as pd
    from io import BytesIO
    
    cps = db.query(CollectionPoint).order_by(CollectionPoint.cpid).all()
    
    data = []
    for cp in cps:
        data.append({
            'CPID': cp.cpid,
            'Name': cp.name,
            'Latitude': cp.latitude,
            'Longitude': cp.longitude,
            'Active': 'Yes' if cp.is_active else 'No'
        })
    
    df = pd.DataFrame(data)
    output = BytesIO()
    df.to_excel(output, index=False)
    output.seek(0)
    
    filename = f"collection_points_{datetime.now().strftime('%Y%m%d')}.xlsx"
    
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.get("/admin/setup/export-depots")
def export_depots(request: Request, db: Session = Depends(get_db)):
    """Export all depots to Excel"""
    user = get_current_user_from_cookie(request, db)
    if not user or user.role != 'Admin':
        return RedirectResponse(url="/dashboard", status_code=303)
    
    import pandas as pd
    from io import BytesIO
    
    depots = db.query(Depot).order_by(Depot.depot_id).all()
    
    data = []
    for depot in depots:
        data.append({
            'Depot ID': depot.depot_id,
            'Name': depot.name,
            'Latitude': depot.latitude,
            'Longitude': depot.longitude,
            'Daily Capacity': depot.daily_capacity,
            'Active': 'Yes' if depot.is_active else 'No'
        })
    
    df = pd.DataFrame(data)
    output = BytesIO()
    df.to_excel(output, index=False)
    output.seek(0)
    
    filename = f"depots_{datetime.now().strftime('%Y%m%d')}.xlsx"
    
    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )