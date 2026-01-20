from sqlalchemy import Column, Integer, String, Float, Boolean, Date, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from app.database import Base
from datetime import datetime


class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    email = Column(String(100), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False, default="Viewer")  # Admin, Operator, Viewer
    is_active = Column(Boolean, default=True)
    last_login = Column(DateTime, nullable=True)
    failed_login_attempts = Column(Integer, default=0)
    locked_until = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class CollectionPoint(Base):
    __tablename__ = "collection_points"
    
    id = Column(Integer, primary_key=True, index=True)
    cpid = Column(String(20), unique=True, index=True, nullable=False)
    name = Column(String(100), nullable=False)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    is_active = Column(Boolean, default=True)


class Depot(Base):
    __tablename__ = "depots"
    
    id = Column(Integer, primary_key=True, index=True)
    depot_id = Column(String(20), unique=True, index=True, nullable=False)
    name = Column(String(100), nullable=False)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    daily_capacity = Column(Integer, nullable=False, default=10000)
    sortation_start_time = Column(String(5), default="08:00")
    cutoff_time = Column(String(5), default="18:00")
    is_active = Column(Boolean, default=True)


class DailyVolume(Base):
    __tablename__ = "daily_volumes"
    
    id = Column(Integer, primary_key=True, index=True)
    date = Column(Date, nullable=False, index=True)
    cpid = Column(String(20), ForeignKey("collection_points.cpid"), nullable=False)
    parcels = Column(Integer, nullable=False)
    trailers = Column(Integer, nullable=False)
    imported_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    imported_at = Column(DateTime, default=datetime.utcnow)
    collection_time = Column(String(5), default="09:00")


class ManualOverride(Base):
    __tablename__ = "manual_overrides"
    
    id = Column(Integer, primary_key=True, index=True)
    date = Column(Date, nullable=False, index=True)
    cpid = Column(String(20), ForeignKey("collection_points.cpid"), nullable=False)
    trailer_number = Column(Integer, nullable=False)
    collection_time = Column(String(5), default="09:00")
    to_depot_id = Column(String(20), ForeignKey("depots.depot_id"), nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class CapacityOverride(Base):
    __tablename__ = "capacity_overrides"
    
    id = Column(Integer, primary_key=True, index=True)
    date = Column(Date, nullable=False, index=True)
    depot_id = Column(String(20), ForeignKey("depots.depot_id"), nullable=False)
    override_capacity = Column(Integer, nullable=False)
    reason = Column(String(255), nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"
    
    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    action_type = Column(String(50), nullable=False, index=True)
    entity_type = Column(String(50), nullable=True)
    entity_id = Column(String(100), nullable=True)
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)
    ip_address = Column(String(45), nullable=True)


class CPDepotDistance(Base):
    __tablename__ = "cp_depot_distances"
    
    id = Column(Integer, primary_key=True, index=True)
    cpid = Column(String(20), ForeignKey("collection_points.cpid"), nullable=False)
    depot_id = Column(String(20), ForeignKey("depots.depot_id"), nullable=False)
    distance_miles = Column(Float, nullable=False)
    rank = Column(Integer, nullable=False)
