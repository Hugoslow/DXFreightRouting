from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import User, AuditLog
from app.auth import (
    verify_password, create_access_token, ACCESS_TOKEN_EXPIRE_MINUTES,
    get_csrf_token, validate_csrf_token, generate_csrf_token
)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    csrf_token = get_csrf_token(request)
    response = templates.TemplateResponse("login.html", {
        "request": request, 
        "error": None,
        "csrf_token": csrf_token
    })
    response.set_cookie(
        key="csrf_token",
        value=csrf_token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=3600
    )
    return response


@router.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db)
):
    # Validate CSRF token first
    if not validate_csrf_token(request, csrf_token):
        new_csrf = generate_csrf_token()
        response = templates.TemplateResponse("login.html", {
            "request": request, 
            "error": "Security validation failed. Please try again.",
            "csrf_token": new_csrf
        })
        response.set_cookie(
            key="csrf_token",
            value=new_csrf,
            httponly=True,
            secure=True,
            samesite="strict",
            max_age=3600
        )
        return response
    
    user = db.query(User).filter(User.username == username).first()
    
    # Check if account is locked
    if user and user.locked_until:
        if datetime.utcnow() < user.locked_until:
            minutes_left = int((user.locked_until - datetime.utcnow()).total_seconds() / 60) + 1
            new_csrf = generate_csrf_token()
            response = templates.TemplateResponse("login.html", {
                "request": request, 
                "error": f"Account locked. Try again in {minutes_left} minute(s)",
                "csrf_token": new_csrf
            })
            response.set_cookie(
                key="csrf_token",
                value=new_csrf,
                httponly=True,
                secure=True,
                samesite="strict",
                max_age=3600
            )
            return response
        else:
            user.locked_until = None
            user.failed_login_attempts = 0
            db.commit()
    
    if not user or not verify_password(password, user.password_hash):
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
        
        if user:
            user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
            if user.failed_login_attempts >= 5:
                user.locked_until = datetime.utcnow() + timedelta(minutes=15)
                db.commit()
                new_csrf = generate_csrf_token()
                response = templates.TemplateResponse("login.html", {
                    "request": request, 
                    "error": "Too many failed attempts. Account locked for 15 minutes",
                    "csrf_token": new_csrf
                })
                response.set_cookie(
                    key="csrf_token",
                    value=new_csrf,
                    httponly=True,
                    secure=True,
                    samesite="strict",
                    max_age=3600
                )
                return response
        
        db.commit()
        new_csrf = generate_csrf_token()
        response = templates.TemplateResponse("login.html", {
            "request": request, 
            "error": "Invalid username or password",
            "csrf_token": new_csrf
        })
        response.set_cookie(
            key="csrf_token",
            value=new_csrf,
            httponly=True,
            secure=True,
            samesite="strict",
            max_age=3600
        )
        return response
    
    if not user.is_active:
        new_csrf = generate_csrf_token()
        response = templates.TemplateResponse("login.html", {
            "request": request, 
            "error": "Account is disabled",
            "csrf_token": new_csrf
        })
        response.set_cookie(
            key="csrf_token",
            value=new_csrf,
            httponly=True,
            secure=True,
            samesite="strict",
            max_age=3600
        )
        return response
    
    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login = datetime.utcnow()
    db.commit()
    
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
    response.delete_cookie("csrf_token")
    return response


@router.get("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie("access_token")
    response.delete_cookie("csrf_token")
    return response

@router.get("/change-password", response_class=HTMLResponse)
def change_password_page(
    request: Request,
    db: Session = Depends(get_db)
):
    from app.auth import get_current_user_from_cookie, get_csrf_token
    
    user = get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    
    csrf_token = get_csrf_token(request)
    response = templates.TemplateResponse("change_password.html", {
        "request": request,
        "user": user,
        "csrf_token": csrf_token,
        "error": None,
        "success": None
    })
    response.set_cookie(
        key="csrf_token",
        value=csrf_token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=3600
    )
    return response


@router.post("/change-password", response_class=HTMLResponse)
def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db)
):
    from app.auth import (
        get_current_user_from_cookie, verify_password, get_password_hash,
        validate_password, validate_csrf_token, generate_csrf_token
    )
    from app.models import AuditLog
    
    user = get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    
    def render_error(error_msg):
        new_csrf = generate_csrf_token()
        response = templates.TemplateResponse("change_password.html", {
            "request": request,
            "user": user,
            "csrf_token": new_csrf,
            "error": error_msg,
            "success": None
        })
        response.set_cookie(
            key="csrf_token",
            value=new_csrf,
            httponly=True,
            secure=True,
            samesite="strict",
            max_age=3600
        )
        return response
    
    # Validate CSRF
    if not validate_csrf_token(request, csrf_token):
        return render_error("Security validation failed. Please try again.")
    
    # Check current password
    if not verify_password(current_password, user.password_hash):
        return render_error("Current password is incorrect.")
    
    # Check passwords match
    if new_password != confirm_password:
        return render_error("New passwords do not match.")
    
    # Validate password requirements
    is_valid, error_msg = validate_password(new_password)
    if not is_valid:
        return render_error(error_msg)
    
    # Update password
    old_hash = user.password_hash[:20] + "..."  # Truncated for audit log
    user.password_hash = get_password_hash(new_password)
    
    # Log the change
    audit = AuditLog(
        user_id=user.id,
        action_type="PASSWORD_CHANGE",
        entity_type="User",
        entity_id=str(user.id),
        old_value=None,
        new_value="Password changed",
        ip_address=request.client.host
    )
    db.add(audit)
    db.commit()
    
    new_csrf = generate_csrf_token()
    response = templates.TemplateResponse("change_password.html", {
        "request": request,
        "user": user,
        "csrf_token": new_csrf,
        "error": None,
        "success": "Password changed successfully!"
    })
    response.set_cookie(
        key="csrf_token",
        value=new_csrf,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=3600
    )
    return response