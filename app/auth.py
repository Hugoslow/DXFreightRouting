from datetime import datetime, timedelta
from typing import Optional, Tuple
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User
import secrets
import os

SECRET_KEY = os.getenv("SECRET_KEY", "dx-freight-routing-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
SESSION_INACTIVITY_MINUTES = 30

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def validate_password(password: str) -> Tuple[bool, str]:
    """Check password meets requirements. Returns (is_valid, error_message)."""
    if len(password) < 8:
        return False, "Password must be at least 8 characters long"
    if not any(c.isupper() for c in password):
        return False, "Password must contain at least one uppercase letter"
    if not any(c.islower() for c in password):
        return False, "Password must contain at least one lowercase letter"
    if not any(c.isdigit() for c in password):
        return False, "Password must contain at least one number"
    return True, ""


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    # Include issued_at for session timeout tracking
    to_encode.update({"exp": expire, "iat": datetime.utcnow().timestamp()})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def get_current_user_from_cookie(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    token = request.cookies.get("access_token")
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            return None
        
        # Session timeout check - token expires after inactivity
        issued_at = payload.get("iat")
        if issued_at:
            token_age = datetime.utcnow().timestamp() - issued_at
            if token_age > (SESSION_INACTIVITY_MINUTES * 60):
                return None
                
    except JWTError:
        return None
    user = db.query(User).filter(User.username == username).first()
    return user


def require_login(request: Request, db: Session = Depends(get_db)) -> User:
    user = get_current_user_from_cookie(request, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/login"}
        )
    return user


def require_role(allowed_roles: list):
    def role_checker(request: Request, db: Session = Depends(get_db)) -> User:
        user = get_current_user_from_cookie(request, db)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_303_SEE_OTHER,
                headers={"Location": "/login"}
            )
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to access this resource"
            )
        return user
    return role_checker


# CSRF Protection
def generate_csrf_token() -> str:
    """Generate a random CSRF token."""
    return secrets.token_urlsafe(32)


def get_csrf_token(request: Request) -> str:
    """Get existing CSRF token from cookie or generate new one."""
    token = request.cookies.get("csrf_token")
    if not token:
        token = generate_csrf_token()
    return token


def validate_csrf_token(request: Request, form_token: str) -> bool:
    """Validate that form token matches cookie token."""
    cookie_token = request.cookies.get("csrf_token")
    if not cookie_token or not form_token:
        return False
    return secrets.compare_digest(cookie_token, form_token)