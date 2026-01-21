from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User, AuditLog
from app.auth import verify_password, create_access_token, ACCESS_TOKEN_EXPIRE_MINUTES

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.username == username).first()
    
    # Check if account is locked
    if user and user.locked_until:
        if datetime.utcnow() < user.locked_until:
            minutes_left = int((user.locked_until - datetime.utcnow()).total_seconds() / 60) + 1
            return templates.TemplateResponse("login.html", {
                "request": request, 
                "error": f"Account locked. Try again in {minutes_left} minute(s)"
            })
        else:
            # Lock expired, reset
            user.locked_until = None
            user.failed_login_attempts = 0
            db.commit()
    
    if not user or not verify_password(password, user.password_hash):
        # Log failed attempt
        audit = AuditLog(
            user_id=None,
            action_type="LOGIN_FAILED",
            entity_type="User",
            entity_id=username,
            old_value=None,
            new_value=None,
            ip_address=request.client.host
        )
        db.add(audit)
        
        # Track failed attempts for existing users
        if user:
            user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
            # Lock after 5 failed attempts for 15 minutes
            if user.failed_login_attempts >= 5:
                user.locked_until = datetime.utcnow() + timedelta(minutes=15)
                db.commit()
                return templates.TemplateResponse("login.html", {
                    "request": request, 
                    "error": "Too many failed attempts. Account locked for 15 minutes"
                })
        
        db.commit()
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid username or password"})
    
    if not user.is_active:
        return templates.TemplateResponse("login.html", {"request": request, "error": "Account is disabled"})
    
    # Successful login - reset failed attempts
    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login = datetime.utcnow()
    db.commit()
    
    # Log successful login
    audit = AuditLog(
        user_id=user.id,
        action_type="LOGIN_SUCCESS",
        entity_type="User",
        entity_id=str(user.id),
        old_value=None,
        new_value=None,
        ip_address=request.client.host
    )
    db.add(audit)
    db.commit()
    
    access_token = create_access_token(
        data={"sub": user.username},
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    
    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60
    )
    return response


@router.get("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("access_token")
    return response