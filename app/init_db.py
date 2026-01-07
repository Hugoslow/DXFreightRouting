from app.database import engine, Base
from app.models import User, CollectionPoint, Depot, CPDepotDistance, DailyVolume, ManualOverride, CapacityOverride, AuditLog
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def create_tables():
    Base.metadata.create_all(bind=engine)
    print("All database tables created successfully.")

def create_admin_user():
    from app.database import SessionLocal
    db = SessionLocal()
    
    existing_admin = db.query(User).filter(User.username == "admin").first()
    if existing_admin:
        print("Admin user already exists.")
        db.close()
        return
    
    admin = User(
        username="admin",
        email="admin@dx.com",
        password_hash=pwd_context.hash("admin123"),
        role="Admin",
        is_active=True
    )
    db.add(admin)
    db.commit()
    print("Admin user created. Username: admin, Password: admin123")
    db.close()

if __name__ == "__main__":
    create_tables()
    create_admin_user()
