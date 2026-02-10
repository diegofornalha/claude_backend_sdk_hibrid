import os
import json
import time
import logging
import base64
import requests
import re
import asyncio
from typing import List, Dict, Optional, Any, Union
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Depends, BackgroundTasks, Body, Header, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
# SQLite removido - usando Turso/libSQL
import jwt
import hashlib
import random
import string
from datetime import datetime, timedelta
from decimal import Decimal
from dotenv import load_dotenv
# numpy removido - não utilizado diretamente (usado em embeddings)
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
# from fastapi_mcp import FastApiMCP  # Apenas necessário para MCP server

# Importar routers
from routes.chat_routes import router as chat_router  # WebSocket chat habilitado!
from routes.admin_config_routes import router as admin_config_router, user_config_router  # Admin config + User config
from routes.webhook_routes import router as webhook_router  # Webhooks para automação CRM
from routes.lead_conversion_routes import router as lead_conversion_router  # Conversão lead → mentorado
from routes.config_routes import router as config_router  # White Label config
from routes.user_routes import router as user_router  # User management + Evolution
from routes.sdk_routes import router as sdk_router  # SDK endpoints para claude-front-sdk-angular

# Importar ConfigManager para gerenciamento dinâmico de agentes/ferramentas
from core.config_manager import init_config_manager

# Importar sistema de roles e permissões
from core.roles import require_role, get_user_role

# Load environment variables
load_dotenv(override=True)
print("DB Name:", os.getenv('DB_NAME'))

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Initialize rate limiter
limiter = Limiter(key_func=get_remote_address)


# ==============================================================================
# LIFECYCLE - AgentFS Initialization (Per-User Isolation)
# ==============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle with per-user AgentFS isolation"""
    from core.agentfs_manager import get_agentfs_manager, close_all_agentfs

    # Startup
    logger.info("🚀 Starting application...")
    manager = None
    try:
        # Inicializar o manager (não abre conexões ainda - lazy initialization)
        manager = await get_agentfs_manager()
        existing_users = manager.list_user_dbs()
        logger.info(f"✅ AgentFS Manager ready! Found {len(existing_users)} user DBs")
        if existing_users:
            logger.info(f"   Users with AgentFS data: {existing_users}")

        # Iniciar cleanup de conexões inativas
        manager.start_cleanup_task()
    except Exception as e:
        logger.warning(f"⚠️ AgentFS Manager initialization failed: {e}")
        logger.info("Continuing without AgentFS...")

    yield

    # Shutdown
    logger.info("🛑 Shutting down...")
    try:
        # Parar cleanup task primeiro
        if manager:
            manager.stop_cleanup_task()
        await close_all_agentfs()
        logger.info("✅ All AgentFS connections closed")
    except Exception as e:
        logger.warning(f"⚠️ Error closing AgentFS connections: {e}")
    logger.info("✅ Application closed")


# Initialize FastAPI app with lifecycle
app = FastAPI(
    title="crm API",
    description="Environmental waste monitoring API powered by Claude Opus 4.5 with RAG + AgentFS",
    version="2.1.0",  # Versão 2.1 - migrado para AgentFS
    lifespan=lifespan,
    docs_url=None if os.getenv("ENVIRONMENT") == "production" else "/docs",
    redoc_url=None if os.getenv("ENVIRONMENT") == "production" else "/redoc"
)

# Add rate limiter state
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS configuration - Restrict to known origins in production
# Quando allow_credentials=True, não pode usar "*" - precisa especificar origens
DEFAULT_ORIGINS = "https://mvp.agentesintegrados.com,https://agentesintegrados.com,http://localhost:4200,http://localhost:4201,http://localhost:4242,http://localhost:8234"
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", DEFAULT_ORIGINS).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    max_age=3600,
)

# Static files - Gráficos e mapas salvos localmente (substitui S3)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Initialize MCP Server - exposes API endpoints as MCP tools
# Comentado: MCP é inicializado em mcp_server.py separadamente
# mcp = FastApiMCP(
#     app,
#     name="crm-api",
#     description="Nanda - Sistema de diagnostico de resíduos com IA para o Brasil"
# )
# mcp.mount()
# logger.info("MCP Server mounted at /mcp")

# Background task to clean up old local files
import threading

def cleanup_local_files():
    """Delete old files from static/charts/ and static/maps/ every hour"""
    while True:
        try:
            # Wait 1 hour
            time.sleep(3600)  # 3600 seconds = 1 hour

            from tools.visualization_tools import cleanup_old_files
            cleanup_old_files(max_age_hours=24)

        except Exception as e:
            logger.error(f"Error cleaning up local files: {e}")

# Start cleanup task in background thread
cleanup_thread = threading.Thread(target=cleanup_local_files, daemon=True)
cleanup_thread.start()
logger.info("Started local files cleanup task (runs every 1 hour, removes files older than 24h)")

# AWS Bedrock/S3 REMOVIDO - Agora usa Claude Agent SDK + storage local
logger.info("Using Claude Opus 4.5 for vision + local storage (no AWS required)")

# JWT configuration
JWT_SECRET = os.getenv('JWT_SECRET', 'development_secret_do_not_use_in_production')
JWT_EXPIRATION_HOURS = int(os.getenv('JWT_EXPIRATION_HOURS', '24'))
ACCESS_TOKEN_EXPIRE_HOURS = int(os.getenv('ACCESS_TOKEN_EXPIRE_HOURS', '6'))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv('REFRESH_TOKEN_EXPIRE_DAYS', '7'))

# Database configuration - Turso/libSQL (local ou cloud)
from core.turso_database import get_db_connection

# WhatsApp OTP
from core.whatsapp_otp import send_whatsapp_otp, normalize_phone_number

# Embeddings configuration (TODO: substituir Titan por alternativa open-source)
embedding_enabled = False  # Embeddings temporariamente desabilitados

# Define Pydantic models for request/response validation
class PhoneOTPRequest(BaseModel):
    """Request para login/registro via WhatsApp OTP"""
    phone_number: str

class OTPVerify(BaseModel):
    """Verificação de OTP por phone_number"""
    phone_number: str
    otp: str

class ResendOTPRequest(BaseModel):
    """Reenvio de OTP por phone_number"""
    phone_number: str

class ReportCreate(BaseModel):
    user_id: int
    latitude: float
    longitude: float
    description: str
    image_data: Optional[str] = None
    device_info: Optional[Dict[str, str]] = None

class RefreshRequest(BaseModel):
    refresh_token: str

class LogoutRequest(BaseModel):
    refresh_token: str

class UpdateUserProfile(BaseModel):
    username: Optional[str] = None
    email: Optional[str] = None
    phone_number: Optional[str] = None
    profile_image_url: Optional[str] = None

class TokenData(BaseModel):
    token: str
    user: Dict[str, Any]

class UpdateReportStatus(BaseModel):
    status: str  # submitted, analyzing, analyzed, resolved, rejected

class UpdateHotspotStatus(BaseModel):
    status: str  # active, monitoring, resolved

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login")

# Helper functions
def hash_password(password, salt=None):
    """Hash a password with a salt and return base64 encoded string"""
    if not salt:
        salt = os.urandom(32)  # Generate a new salt if not provided
    
    # Hash the password with the salt
    key = hashlib.pbkdf2_hmac(
        'sha256',
        password.encode('utf-8'),
        salt,
        100000  # Number of iterations
    )
    
    # Combine salt and key, then base64 encode for storage in text column
    storage = salt + key
    return base64.b64encode(storage).decode('ascii')

def verify_password(stored_password, provided_password):
    """Verify a password against a stored hash"""
    # Decode the base64 stored password
    decoded = base64.b64decode(stored_password.encode('ascii'))
    
    salt = decoded[:32]  # Get the salt from the stored password
    stored_key = decoded[32:]
    
    # Hash the provided password with the same salt
    key = hashlib.pbkdf2_hmac(
        'sha256',
        provided_password.encode('utf-8'),
        salt,
        100000  # Same number of iterations as in hash_password
    )
    
    # Compare the generated key with the stored key
    return key == stored_key

def generate_token(user_id):
    """Generate a JWT token for the user (deprecated, use generate_access_token)"""
    expiration = datetime.now() + timedelta(hours=JWT_EXPIRATION_HOURS)

    payload = {
        'user_id': user_id,
        'exp': expiration
    }

    # Encode JWT token
    return jwt.encode(payload, JWT_SECRET, algorithm='HS256')

def generate_access_token(user_id):
    """Generate a JWT access token for the user (6 hours)"""
    from datetime import timezone
    expiration = datetime.now(timezone.utc) + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)

    payload = {
        'user_id': user_id,
        'exp': expiration,
        'type': 'access'
    }

    # Encode JWT token
    return jwt.encode(payload, JWT_SECRET, algorithm='HS256')

def generate_refresh_token(user_id, cursor):
    """Generate a UUID refresh token and save to database (7 days)"""
    import uuid
    from datetime import timezone

    refresh_token = str(uuid.uuid4())
    expires_at = datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)

    cursor.execute("""
        INSERT INTO refresh_tokens (user_id, refresh_token, expires_at)
        VALUES (%s, %s, %s)
    """, (user_id, refresh_token, expires_at))

    return refresh_token

def verify_refresh_token(refresh_token, cursor):
    """Verify refresh token and return user_id if valid"""
    from datetime import timezone

    cursor.execute("""
        SELECT user_id, expires_at, revoked
        FROM refresh_tokens
        WHERE refresh_token = %s
    """, (refresh_token,))

    result = cursor.fetchone()
    if not result:
        return None

    user_id, expires_at, revoked = result

    # Check if revoked or expired
    if revoked or datetime.now(timezone.utc) > expires_at:
        return None

    return user_id

def verify_token(token):
    """Verify a JWT token and return the user ID if valid"""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
        return payload['user_id']
    except jwt.ExpiredSignatureError:
        return None  # Token has expired
    except jwt.InvalidTokenError:
        return None  # Invalid token

# ============== Chat Persistence Functions ==============

def save_chat_session(session_id: str, user_id: int, title: str = None) -> bool:
    """Create or update a chat session"""
    connection = get_db_connection()
    if not connection:
        logger.error("Failed to get database connection for chat session")
        return False

    cursor = connection.cursor(dictionary=True)
    try:
        # Check if session exists
        cursor.execute(
            "SELECT session_id FROM chat_sessions WHERE session_id = %s",
            (session_id,)
        )
        existing = cursor.fetchone()

        if existing:
            # Update existing session
            cursor.execute(
                "UPDATE chat_sessions SET updated_at = CURRENT_TIMESTAMP WHERE session_id = %s",
                (session_id,)
            )
        else:
            # Create new session
            cursor.execute(
                """INSERT INTO chat_sessions (session_id, user_id, title)
                   VALUES (%s, %s, %s)""",
                (session_id, user_id, title or "Nova conversa")
            )

        connection.commit()
        return True
    except Exception as e:
        logger.error(f"Error saving chat session: {e}")
        connection.rollback()
        return False
    finally:
        cursor.close()
        connection.close()

def save_chat_message(session_id: str, user_id: int, role: str, content: str,
                      image_url: str = None, map_url: str = None) -> bool:
    """Save a chat message to the database"""
    connection = get_db_connection()
    if not connection:
        logger.error("Failed to get database connection for chat message")
        return False

    cursor = connection.cursor()
    try:
        cursor.execute(
            """INSERT INTO chat_messages (session_id, user_id, role, content, image_url, map_url)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (session_id, user_id, role, content, image_url, map_url)
        )
        connection.commit()
        logger.info(f"Saved {role} message for session {session_id}")
        return True
    except Exception as e:
        logger.error(f"Error saving chat message: {e}")
        connection.rollback()
        return False
    finally:
        cursor.close()
        connection.close()

def get_chat_sessions(user_id: int, page: int = 1, per_page: int = 20) -> Dict:
    """Get chat sessions for a user"""
    connection = get_db_connection()
    if not connection:
        return {"error": "Database connection failed"}

    cursor = connection.cursor(dictionary=True)
    try:
        offset = (page - 1) * per_page

        # Get total count
        cursor.execute(
            "SELECT COUNT(*) as total FROM chat_sessions WHERE user_id = %s",
            (user_id,)
        )
        total = cursor.fetchone()['total']

        # Get sessions
        cursor.execute(
            """SELECT session_id, title, created_at, updated_at,
                      (SELECT COUNT(*) FROM chat_messages WHERE chat_messages.session_id = chat_sessions.session_id) as message_count
               FROM chat_sessions
               WHERE user_id = %s
               ORDER BY updated_at DESC
               LIMIT %s OFFSET %s""",
            (user_id, per_page, offset)
        )
        sessions = cursor.fetchall()

        # Convert datetime to string
        for session in sessions:
            session['created_at'] = session['created_at'].isoformat() if session['created_at'] else None
            session['updated_at'] = session['updated_at'].isoformat() if session['updated_at'] else None

        return {
            "sessions": sessions,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": (total + per_page - 1) // per_page
        }
    except Exception as e:
        logger.error(f"Error getting chat sessions: {e}")
        return {"error": str(e)}
    finally:
        cursor.close()
        connection.close()

def get_chat_messages(session_id: str, user_id: Optional[int], page: int = 1, per_page: int = 50) -> Dict:
    """Get messages for a chat session. If user_id is None (admin), skip ownership check."""
    connection = get_db_connection()
    if not connection:
        return {"error": "Database connection failed"}

    cursor = connection.cursor(dictionary=True)
    try:
        # Verify session exists (and belongs to user if not admin)
        if user_id is not None:
            cursor.execute(
                "SELECT session_id FROM chat_sessions WHERE session_id = %s AND user_id = %s",
                (session_id, user_id)
            )
        else:
            # Admin - apenas verificar se sessão existe
            cursor.execute(
                "SELECT session_id FROM chat_sessions WHERE session_id = %s",
                (session_id,)
            )
        if not cursor.fetchone():
            return {"error": "Session not found or access denied"}

        offset = (page - 1) * per_page

        # Get total count
        cursor.execute(
            "SELECT COUNT(*) as total FROM chat_messages WHERE session_id = %s",
            (session_id,)
        )
        total = cursor.fetchone()['total']

        # Get messages
        cursor.execute(
            """SELECT message_id, role, content, image_url, map_url, created_at
               FROM chat_messages
               WHERE session_id = %s
               ORDER BY message_id ASC
               LIMIT %s OFFSET %s""",
            (session_id, per_page, offset)
        )
        messages = cursor.fetchall()

        # Convert datetime to string
        for msg in messages:
            msg['created_at'] = msg['created_at'].isoformat() if msg['created_at'] else None

        return {
            "messages": messages,
            "session_id": session_id,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": (total + per_page - 1) // per_page
        }
    except Exception as e:
        logger.error(f"Error getting chat messages: {e}")
        return {"error": str(e)}
    finally:
        cursor.close()
        connection.close()

def update_session_title(session_id: str, user_id: int, title: str) -> bool:
    """Update the title of a chat session"""
    connection = get_db_connection()
    if not connection:
        return False

    cursor = connection.cursor()
    try:
        cursor.execute(
            "UPDATE chat_sessions SET title = %s WHERE session_id = %s AND user_id = %s",
            (title, session_id, user_id)
        )
        connection.commit()
        return cursor.rowcount > 0
    except Exception as e:
        logger.error(f"Error updating session title: {e}")
        return False
    finally:
        cursor.close()
        connection.close()

def delete_chat_session(session_id: str, user_id: int) -> bool:
    """Delete a chat session and all its messages"""
    connection = get_db_connection()
    if not connection:
        return False

    cursor = connection.cursor()
    try:
        cursor.execute(
            "DELETE FROM chat_sessions WHERE session_id = %s AND user_id = %s",
            (session_id, user_id)
        )
        connection.commit()
        return cursor.rowcount > 0
    except Exception as e:
        logger.error(f"Error deleting chat session: {e}")
        return False
    finally:
        cursor.close()
        connection.close()

# ============== End Chat Persistence Functions ==============

def check_and_create_hotspots(cursor, connection, report, report_id, analysis_result):
    """
    Check for nearby reports and create/update hotspots if criteria are met.
    This function works for both waste and non-waste reports.
    
    Args:
        cursor: Database cursor
        connection: Database connection 
        report: Report data dictionary
        report_id: ID of the current report
        analysis_result: Analysis results dictionary
    
    Returns:
        Dictionary with hotspot creation results
    """
    try:
        # Find nearby reports (within 500 meters)
        cursor.execute(
            """
            SELECT report_id, latitude, longitude
            FROM reports
            WHERE (
                6371 * acos(
                    cos(radians(%s)) * cos(radians(latitude)) * 
                    cos(radians(longitude) - radians(%s)) + 
                    sin(radians(%s)) * sin(radians(latitude))
                )
            ) < 0.5  -- Reports within 500 meters
            AND report_id != %s
            AND status = 'analyzed'  -- Only include analyzed reports in hotspots
            """,
            (report['latitude'], report['longitude'], report['latitude'], report_id)
        )
        
        nearby_reports = cursor.fetchall()
        nearby_count = len(nearby_reports)
        
        logger.info(f"Found {nearby_count} nearby reports for report {report_id}")
        
        # If there are nearby reports, create or update a hotspot
        if nearby_count >= 2:  # Minimum 3 reports to form a hotspot (including this one)
            # Check if a hotspot already exists in this area
            cursor.execute(
                """
                SELECT hotspot_id
                FROM hotspots
                WHERE (
                    6371 * acos(
                        cos(radians(%s)) * cos(radians(center_latitude)) * 
                        cos(radians(center_longitude) - radians(%s)) + 
                        sin(radians(%s)) * sin(radians(center_latitude))
                    )
                ) < 0.5  -- Within 500 meters
                """,
                (report['latitude'], report['longitude'], report['latitude'])
            )
            
            hotspot = cursor.fetchone()
            
            if hotspot:
                # Update existing hotspot
                hotspot_id = hotspot['hotspot_id']
                cursor.execute(
                    """
                    UPDATE hotspots
                    SET last_reported = %s, total_reports = %s
                    WHERE hotspot_id = %s
                    """,
                    (datetime.now().date(), nearby_count + 1, hotspot_id)
                )
                logger.info(f"Updated existing hotspot {hotspot_id}")
            else:
                # Create new hotspot
                cursor.execute(
                    """
                    INSERT INTO hotspots (
                        name, center_latitude, center_longitude, radius_meters,
                        location_id, first_reported, last_reported, total_reports,
                        average_severity, status
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        f"Hotspot near {report.get('address_text', 'Unknown')}",
                        report['latitude'],
                        report['longitude'],
                        500,  # 500 meter radius
                        report.get('location_id'),
                        datetime.now().date(),
                        datetime.now().date(),
                        nearby_count + 1,  # Include this report
                        analysis_result.get('severity_score', 1),
                        'active'
                    )
                )
                
                hotspot_id = cursor.lastrowid
                logger.info(f"Created new hotspot {hotspot_id}")
            
            # Associate current report with hotspot if not already linked
            cursor.execute(
                """
                SELECT * FROM hotspot_reports 
                WHERE hotspot_id = %s AND report_id = %s
                """, 
                (hotspot_id, report_id)
            )
            
            if not cursor.fetchone():
                cursor.execute(
                    """
                    INSERT INTO hotspot_reports (hotspot_id, report_id)
                    VALUES (%s, %s)
                    """,
                    (hotspot_id, report_id)
                )
                logger.info(f"Associated report {report_id} with hotspot {hotspot_id}")
            
            # Associate all nearby reports with the hotspot if not already linked
            for nearby_report in nearby_reports:
                nearby_id = nearby_report['report_id']
                
                cursor.execute(
                    """
                    SELECT * FROM hotspot_reports 
                    WHERE hotspot_id = %s AND report_id = %s
                    """, 
                    (hotspot_id, nearby_id)
                )
                
                if not cursor.fetchone():
                    cursor.execute(
                        """
                        INSERT INTO hotspot_reports (hotspot_id, report_id)
                        VALUES (%s, %s)
                        """,
                        (hotspot_id, nearby_id)
                    )
            
            # Update average severity based on all reports in the hotspot
            cursor.execute(
                """
                SELECT AVG(ar.severity_score) as avg_severity
                FROM hotspot_reports hr
                JOIN analysis_results ar ON hr.report_id = ar.report_id
                WHERE hr.hotspot_id = %s
                """,
                (hotspot_id,)
            )
            
            avg_result = cursor.fetchone()
            if avg_result and avg_result['avg_severity'] is not None:
                cursor.execute(
                    """
                    UPDATE hotspots
                    SET average_severity = %s
                    WHERE hotspot_id = %s
                    """,
                    (avg_result['avg_severity'], hotspot_id)
                )
            
            connection.commit()
            
            return {
                "hotspot_created": hotspot_id,
                "total_reports": nearby_count + 1,
                "action": "updated" if hotspot else "created"
            }
        else:
            return {
                "hotspot_created": None,
                "total_reports": nearby_count + 1,
                "action": "insufficient_reports"
            }
    
    except Exception as e:
        logger.error(f"Error in hotspot detection: {e}")
        return {
            "hotspot_created": None,
            "error": str(e),
            "action": "error"
        }

async def get_user_from_token(token: str = Depends(oauth2_scheme)):
    """Extract user ID from token in request"""
    user_id = verify_token(token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return user_id

def generate_otp():
    """Generate a 6-digit OTP"""
    return ''.join(random.choices(string.digits, k=6))

# send_email removido — autenticação agora via WhatsApp OTP

def save_image_locally(image_data, filename):
    """
    Save base64 encoded image to local storage.

    Args:
        image_data: Base64 encoded image data (pode incluir prefixo data:image/...)
        filename: Filename to use

    Returns:
        Local URL if successful, None otherwise
    """
    try:
        # Remove data URL prefix if present (e.g., "data:image/jpeg;base64,")
        if image_data.startswith('data:'):
            # Split on comma and take the base64 part
            image_data = image_data.split(',', 1)[1]

        # Decode the base64 data
        image_binary = base64.b64decode(image_data)

        # Create directory structure
        date_path = datetime.now().strftime('%Y/%m/%d')
        reports_dir = os.path.join("static", "reports", date_path)
        os.makedirs(reports_dir, exist_ok=True)

        # Save file locally
        filepath = os.path.join(reports_dir, filename)
        with open(filepath, 'wb') as f:
            f.write(image_binary)

        # Return local URL
        return f"/static/reports/{date_path}/{filename}"

    except Exception as e:
        logger.error(f"Local file save error: {e}")
        return None

# Waste Analysis usando Claude Opus 4.5 Vision (substitui Bedrock AgentCore)
def analyze_waste_image(payload):
    """
    crm AI Agent for analyzing waste and environmental pollution
    NOW USES: Claude Opus 4.5 Vision (via Claude Agent SDK)
    REMOVED: Amazon Bedrock/Nova dependency
    """
    from tools.vision_tools import analyze_waste_image_direct

    try:
        image_url = payload.get("image_url")
        location = payload.get("location", {})
        description = payload.get("description", "")
        image_base64 = payload.get("image_base64", "")
        latitude = location.get('lat', 0)
        longitude = location.get('lng', 0)

        # Usar Claude Opus 4.5 Vision para análise
        result = analyze_waste_image_direct(
            image_base64=image_base64,
            latitude=latitude,
            longitude=longitude,
            description=description
        )

        # Converter resultado para formato esperado
        if result.get("is_waste"):
            analysis = {
                "waste_type": result.get("waste_type", "Unknown"),
                "severity_score": result.get("severity_score", 5),
                "priority_level": result.get("priority_level", "medium").lower(),
                "environmental_impact": result.get("environmental_impact", ""),
                "estimated_volume": result.get("volume_estimate", "Unknown"),
                "safety_concerns": result.get("recommended_action", ""),
                "analysis_notes": result.get("description", ""),
                "waste_detection_confidence": int(result.get("confidence", 0.8) * 100),
                "short_description": f"{result.get('waste_type', 'Waste')} detected",
                "full_description": result.get("description", "")
            }
        else:
            analysis = {
                "waste_type": "Not Garbage",
                "severity_score": 1,
                "priority_level": "low",
                "environmental_impact": "None - not waste material",
                "estimated_volume": "0",
                "safety_concerns": "None",
                "analysis_notes": result.get("description", "No waste detected"),
                "waste_detection_confidence": int(result.get("confidence", 0.8) * 100),
                "short_description": "Not garbage",
                "full_description": result.get("description", "Image does not contain waste")
            }

        return {
            "success": True,
            "analysis": analysis,
            "model_used": "Claude Opus 4.5 Vision",
            "processed_at": datetime.now().isoformat()
        }

    except Exception as e:
        logger.error(f"Claude vision analysis failed: {e}")
        return {
            "success": False,
            "error": str(e),
            "fallback_analysis": {
                "waste_type": "Unknown",
                "confidence_score": 0,
                "analysis_notes": "Analysis failed, manual review required"
            }
        }

# chat_agent REMOVIDO - substituído por /api/chat/ws (WebSocket com Claude Agent SDK)
# A função antiga usava Bedrock AgentCore, agora tudo usa Claude Agent SDK via WebSocket
# REMOVIDO: def chat_agent(payload):
#     """
#     DEPRECATED: Old chat agent using Bedrock AgentCore
#     REPLACED BY: /api/chat/ws (WebSocket endpoint with Claude Agent SDK + RAG)
#     """
#     try:
#         prompt = payload.get("prompt", "")
#         session_id = payload.get("session_id", f"chat_{datetime.now().timestamp()}")
#
#         if not prompt:
#             return {
#                 "success": False,
#                 "error": "No prompt provided",
#                 "response": "Please provide a question or prompt."
#             }
#
#         logger.info(f"AgentCore chat request (session {session_id}): {prompt[:100]}")
#
#         # Load schema information
#         from schema_based_chat import PUBLIC_SCHEMA
#
#         # Build system prompt with tools and schema
#         system_prompt = f"""You are crm AI Assistant, helping users understand waste management data in Timor-Leste.
#
# You have access to database tools to answer questions about waste reports, statistics, hotspots, and trends.
#
# {PUBLIC_SCHEMA}
#
# ## HOW TO ANSWER QUESTIONS:
# 1. Analyze the user's question
# 2. Generate appropriate SQL SELECT queries to fetch data
# 3. Present results in clear, formatted markdown
#
# ## EXAMPLES:
# User: "How many reports are there?"
# SQL: SELECT COUNT(*) as total FROM reports
#
# User: "What are the top waste types?"
# SQL: SELECT wt.name, COUNT(*) as count FROM analysis_results ar JOIN waste_types wt ON ar.waste_type_id = wt.waste_type_id GROUP BY wt.name ORDER BY count DESC LIMIT 5
#
# User: "Which areas have most garbage?"
# SQL: SELECT name, total_reports, average_severity FROM hotspots ORDER BY total_reports DESC LIMIT 10
#
# User: "Show waste trends this month"
# SQL: SELECT DATE(created_at) as date, COUNT(*) as reports FROM reports WHERE created_at >= DATE_SUB(NOW(), INTERVAL 30 DAY) GROUP BY DATE(created_at) ORDER BY date DESC
#
# IMPORTANT RULES:
# - NEVER query: users, user_verifications, api_keys (private data)
# - Only SELECT queries (no INSERT/UPDATE/DELETE)
# - Always use LIMIT (max 100)
# - Format results with markdown tables/lists
# - Be conversational and helpful
#
# User question: {prompt}"""
#
#         # REMOVIDO: Old Bedrock Runtime code
#         # Este código foi substituído por Claude Agent SDK via WebSocket (/api/chat/ws)
#         # Simple direct response using Bedrock Runtime
#         # response = bedrock_runtime.invoke_model(
#         #     modelId=BEDROCK_MODEL_ID,
#         #     contentType="application/json",
#         #     accept="application/json",
#         #     body=json.dumps({
#         #         "inferenceConfig": {
#         #             "max_new_tokens": 2000,
#         #             "temperature": 0.7,
#         #             "top_p": 0.9
#         #         },
#         #         "messages": [
#         #             {
#         #                 "role": "user",
#         #                 "content": [{"text": system_prompt}]
#         #             }
#         #         ]
#         #     })
#         # )
#
#         # Parse response
#         # result = json.loads(response['body'].read())
#
#         # Extract text from Nova Pro response
#         # if 'output' in result and 'message' in result['output']:
#         #     message = result['output']['message']
#         #     if 'content' in message and len(message['content']) > 0:
#         #         chat_response = message['content'][0].get('text', 'No response generated')
#         #     else:
#         #         chat_response = "No response generated"
#         # else:
#         #     chat_response = "Failed to generate response"
#
#         # return {
#         #     "success": True,
#         #     "response": chat_response,
#         #     "session_id": session_id,
#         #     "model_used": BEDROCK_MODEL_ID,
#         #     "processed_at": datetime.now().isoformat()
#         # }
#
#         # Retornar erro direcionando para novo endpoint
#         return {
#             "success": False,
#             "error": "This endpoint is deprecated. Use WebSocket /api/chat/ws instead.",
#             "response": "Please use the new WebSocket chat endpoint."
#         }
#
#     except Exception as e:
#         logger.error(f"Deprecated chat endpoint accessed: {e}")
#         return {
#             "success": False,
#             "error": str(e),
#             "response": "This endpoint is deprecated. Use WebSocket /api/chat/ws instead."
#         }

async def process_report_with_agent_async(report_id, image_url, latitude, longitude, description):
    """Process report using AgentCore for analysis - truly async"""
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        # Download image and convert to base64 for AgentCore
        # Run in thread pool to avoid blocking
        import concurrent.futures
        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor() as executor:
            response = await loop.run_in_executor(executor, requests.get, image_url)
        image_base64 = base64.b64encode(response.content).decode('utf-8')

        # Call AgentCore agent for analysis
        agent_payload = {
            "image_url": image_url,
            "image_base64": image_base64,
            "location": {"lat": latitude, "lng": longitude},
            "description": description
        }

        # Use AgentCore for analysis - run in thread pool to avoid blocking
        with concurrent.futures.ThreadPoolExecutor() as executor:
            analysis_result = await loop.run_in_executor(
                executor, analyze_waste_image, agent_payload
            )

        cursor.close()
        connection.close()

        return analysis_result, image_base64

    except Exception as e:
        logger.error(f"AgentCore async processing failed for report {report_id}: {e}")
        return None, None


async def analyze_image_with_claude(image_url, latitude=0.0, longitude=0.0, description=""):
    """
    Analyze a waste image using Claude Vision API

    Args:
        image_url: Path to the image (local path starting with /static/)
        latitude: Latitude coordinate
        longitude: Longitude coordinate
        description: User-provided description

    Returns:
        Tuple of (analysis_result dict, image_data base64 string)
    """
    try:
        logger.info(f"Analyzing image with Claude Vision API: {image_url}")

        # Convert relative path to absolute local path
        if image_url.startswith('/static/'):
            # Get absolute path from relative /static/ path
            base_dir = os.path.dirname(os.path.abspath(__file__))
            local_path = os.path.join(base_dir, image_url.lstrip('/'))
        else:
            local_path = image_url

        if not os.path.exists(local_path):
            logger.error(f"Image file not found: {local_path}")
            return None, None

        logger.info(f"Reading local image: {local_path}")

        # Read image and convert to base64
        with open(local_path, 'rb') as f:
            image_data = base64.b64encode(f.read()).decode('utf-8')

        logger.info(f"Image loaded, size: {len(image_data)} chars base64")

        # Import and call the vision tool
        from tools.vision_tools import analyze_waste_image_direct

        # Run analysis in thread pool to avoid blocking
        import concurrent.futures
        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor() as executor:
            result = await loop.run_in_executor(
                executor,
                analyze_waste_image_direct,
                "",  # image_base64 (empty, using path)
                local_path,  # image_path
                latitude,
                longitude,
                description
            )

        if result and not result.get('error'):
            # Convert to expected format
            analysis_result = {
                "waste_type": result.get("waste_type", "Unknown"),
                "severity_score": result.get("severity_score", 5),
                "priority_level": result.get("priority_level", "medium").lower(),
                "environmental_impact": result.get("environmental_impact", ""),
                "estimated_volume": result.get("volume_estimate", "Unknown"),
                "safety_concerns": result.get("recommended_action", ""),
                "analysis_notes": result.get("description", ""),
                "waste_detection_confidence": int(result.get("confidence", 0.8) * 100),
                "short_description": f"{result.get('waste_type', 'Waste')} detected",
                "full_description": result.get("description", "")
            }
            logger.info(f"Analysis complete: {analysis_result.get('waste_type')}")
            return analysis_result, image_data
        else:
            logger.error(f"Analysis failed: {result.get('error', 'Unknown error')}")
            return None, None

    except Exception as e:
        logger.error(f"Error in analyze_image_with_claude: {e}")
        return None, None


def extract_volume_number(volume_str):
    """Extract numeric value from volume string like '5 cubic meters' -> 5.0"""
    try:
        if not volume_str or volume_str.lower() in ['unknown', 'n/a', 'not specified']:
            return 0.0
        
        # Convert to string if not already
        volume_str = str(volume_str)
        
        # Extract numbers from the string using regex
        import re
        numbers = re.findall(r'\d+\.?\d*', volume_str)
        
        if numbers:
            return float(numbers[0])
        else:
            return 0.0
    except Exception as e:
        logger.warning(f"Failed to extract volume from '{volume_str}': {e}")
        return 0.0
# Process a waste report
async def process_report(report_id, background_tasks: BackgroundTasks):
    """
    Process a waste report by analyzing its image and updating the database
    
    Args:
        report_id: ID of the report to process
        background_tasks: FastAPI background tasks for async processing
    
    Returns:
        Dictionary with processing results
    """
    try:
        # Get database connection
        connection = get_db_connection()
        if not connection:
            return {"success": False, "message": "Failed to connect to database"}
        
        cursor = connection.cursor(dictionary=True)
        
        # Update report status to analyzing
        cursor.execute(
            "UPDATE reports SET status = 'analyzing' WHERE report_id = %s",
            (report_id,)
        )
        connection.commit()
        
        # Get report data
        cursor.execute(
            """
            SELECT r.*, u.username
            FROM reports r
            LEFT JOIN users u ON r.user_id = u.user_id
            WHERE r.report_id = %s
            """,
            (report_id,)
        )
        
        report = cursor.fetchone()
        if not report:
            cursor.close()
            connection.close()
            return {"success": False, "message": f"Report {report_id} not found"}
        
        # If no image, we can't analyze - return clear error
        if not report['image_url']:
            cursor.execute(
                "UPDATE reports SET status = 'submitted' WHERE report_id = %s",
                (report_id,)
            )
            connection.commit()
            cursor.close()
            connection.close()
            return {"success": False, "message": "No image available for analysis"}
        
        # Log the image URL we're about to analyze
        logger.info(f"Processing report {report_id} with image URL: {report['image_url']}")

        # Analyze image with Nova Pro via AgentCore
        analysis_result, image_data = await analyze_image_with_claude(
            report['image_url'],
            report['latitude'],
            report['longitude'],
            report.get('description', '')
        )
        
        if not analysis_result:
            cursor.execute(
                "UPDATE reports SET status = 'submitted' WHERE report_id = %s",
                (report_id,)
            )
            connection.commit()
            cursor.close()
            connection.close()
            return {"success": False, "message": "Image analysis failed"}
        
        # If the image doesn't contain waste, update status to analyzed with "Not Garbage"
        if analysis_result['waste_type'] == 'Not Garbage':
            # Update the report with "Not Garbage" description and set status to analyzed
            cursor.execute(
                "UPDATE reports SET description = %s, status = %s WHERE report_id = %s",
                ("Not garbage.", "analyzed", report_id)
            )
            connection.commit()
            
            # Get or create "Not Garbage" waste type
            cursor.execute(
                "SELECT waste_type_id FROM waste_types WHERE name = %s",
                ("Not Garbage",)
            )
            waste_type_result = cursor.fetchone()
            
            waste_type_id = None
            if waste_type_result:
                waste_type_id = waste_type_result['waste_type_id']
            else:
                # Create "Not Garbage" waste type if it doesn't exist
                cursor.execute(
                    """
                    INSERT INTO waste_types (name, description, hazard_level, recyclable)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (
                        "Not Garbage",
                        "Images that do not contain waste materials",
                        'low',
                        False
                    )
                )
                connection.commit()
                waste_type_id = cursor.lastrowid
            
            # TODO: Re-implement embeddings when Claude SDK supports it
            image_embedding = None  # create_image_content_embedding removed (was AWS Bedrock)
            location_embedding = None  # create_location_embedding removed (was AWS Bedrock)
            
            # Insert analysis results for non-garbage
            cursor.execute(
                """
                INSERT INTO analysis_results (
                    report_id, analyzed_date, waste_type_id, confidence_score,
                    estimated_volume, severity_score, priority_level,
                    analysis_notes, full_description, processed_by,
                    image_embedding, location_embedding
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    report_id,
                    datetime.now(),
                    waste_type_id,
                    analysis_result.get("waste_detection_confidence", 90.0),
                    0.0,  # Zero volume for non-garbage
                    1,    # Lowest severity
                    "low", # Lowest priority
                    "This image does not contain waste material.",
                    analysis_result.get("full_description", "This image does not contain waste material."),
                    'Nova AI',
                    json.dumps(image_embedding) if image_embedding else None,
                    json.dumps(location_embedding) if location_embedding else None
                )
            )
            connection.commit()
            
            # Log the activity
            cursor.execute(
                """
                INSERT INTO system_logs (agent, action, details, related_id, related_table)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    'api_server',
                    'report_analyzed',
                    f"Report {report_id} analyzed: Not Garbage",
                    report_id,
                    'reports'
                )
            )
            connection.commit()
            
            # Check for hotspots (reports nearby) - for Not Garbage reports too
            logger.info(f"Checking for hotspots near report {report_id} (Not Garbage)")
            hotspot_result = check_and_create_hotspots(cursor, connection, report, report_id, analysis_result)
            
            cursor.close()
            connection.close()
            
            return {
                "success": True,
                "message": f"Report {report_id} analyzed successfully: Not Garbage",
                "analysis": analysis_result,
                "hotspot": hotspot_result
            }
        
        # If image contains garbage, continue with normal analysis flow
        # Set the AI-generated short description
        short_description = analysis_result.get("short_description", "")
        
        # Make sure it's 8 words or less
        if short_description and len(short_description.split()) > 8:
            short_description = " ".join(short_description.split()[:8])
        
        # Fallback if no description is available
        if not short_description:
            short_description = f"{analysis_result['waste_type']} waste"
        
        # Update the report with the short description
        cursor.execute(
            "UPDATE reports SET description = %s, status = %s WHERE report_id = %s",
            (short_description, "analyzed", report_id)
        )
        connection.commit()
        
        # Get waste type ID
        cursor.execute(
            "SELECT waste_type_id FROM waste_types WHERE name = %s",
            (analysis_result['waste_type'],)
        )
        waste_type_result = cursor.fetchone()
        
        waste_type_id = None
        if waste_type_result:
            waste_type_id = waste_type_result['waste_type_id']
        else:
            # If waste type doesn't exist, create it
            cursor.execute(
                """
                INSERT INTO waste_types (name, description, hazard_level, recyclable)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    analysis_result['waste_type'],
                    f"Auto-generated waste type for {analysis_result['waste_type']}",
                    'medium',  # Default hazard level
                    False      # Default not recyclable
                )
            )
            connection.commit()
            waste_type_id = cursor.lastrowid
        
        # TODO: Re-implement embeddings when Claude SDK supports it
        image_embedding = None  # create_image_content_embedding removed (was AWS Bedrock)
        location_embedding = create_location_embedding(report['latitude'], report['longitude'])
        
        # Insert analysis results
        cursor.execute(
            """
            INSERT INTO analysis_results (
                report_id, analyzed_date, waste_type_id, confidence_score,
                estimated_volume, severity_score, priority_level,
                analysis_notes, full_description, processed_by,
                image_embedding, location_embedding
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                report_id,
                datetime.now(),
                waste_type_id,
                analysis_result.get("waste_detection_confidence", 90.0),
                extract_volume_number(analysis_result.get('estimated_volume', '0')),
                analysis_result['severity_score'],
                analysis_result['priority_level'],
                analysis_result.get('analysis_notes', ''),
                analysis_result.get('full_description', 'No detailed description available.'),
                'Nova AI',
                json.dumps(image_embedding) if image_embedding else None,
                json.dumps(location_embedding) if location_embedding else None
            )
        )
        connection.commit()
        
        # Check for hotspots (reports nearby) - for actual waste reports
        logger.info(f"Checking for hotspots near report {report_id} (Actual Waste)")
        hotspot_result = check_and_create_hotspots(cursor, connection, report, report_id, analysis_result)
        
        # Log the activity
        cursor.execute(
            """
            INSERT INTO system_logs (agent, action, details, related_id, related_table)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                'api_server',
                'report_analyzed',
                f"Report {report_id} analyzed",
                report_id,
                'reports'
            )
        )
        connection.commit()
        
        cursor.close()
        connection.close()
        
        return {
            "success": True,
            "message": f"Report {report_id} analyzed successfully",
            "analysis": analysis_result
        }
        
    except Exception as e:
        logger.error(f"Error processing report {report_id}: {e}")
        return {"success": False, "message": f"Error processing report: {str(e)}"}

# API Routes

# Health check endpoint
@app.get("/health", response_model=dict)
async def health_check():
    """
    Health check com verificação de dependências

    Retorna:
    - status: ok/degraded/error
    - database: status da conexão Turso
    - version: versão da API
    """
    try:
        from core.turso_database import db

        # Check database connection com query real
        connection = get_db_connection()
        db_status = "unknown"
        db_mode = "unknown"
        db_tables = 0

        if connection:
            try:
                cursor = connection.cursor(dictionary=True)
                cursor.execute("SELECT 1 as health_check")
                result = cursor.fetchone()

                # Contar tabelas
                cursor.execute("""
                    SELECT COUNT(*) as count FROM sqlite_master
                    WHERE type='table' AND name NOT LIKE 'sqlite_%'
                """)
                count_result = cursor.fetchone()
                db_tables = count_result.get('count', 0) if count_result else 0

                cursor.close()
                connection.close()

                db_status = "healthy"
                db_mode = db._mode
            except Exception as db_error:
                logger.error(f"Database health check failed: {db_error}")
                db_status = "unhealthy"
                if connection:
                    connection.close()
        else:
            db_status = "disconnected"

        # Determinar status geral
        overall_status = "ok" if db_status == "healthy" else "degraded" if db_status == "unhealthy" else "error"

        # Return service status
        return {
            "status": overall_status,
            "service": "crm API",
            "version": "2.0.0",
            "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "database": {
                "status": db_status,
                "mode": db_mode,
                "tables": db_tables
            }
        }
    except Exception as e:
        logger.error(f"Health check error: {e}")
        return {
            "status": "error",
            "message": str(e),
            "service": "crm API",
            "version": "2.0.0"
        }

# Include routers
app.include_router(chat_router)  # WebSocket chat com RAG habilitado!
app.include_router(admin_config_router)  # Admin config de agentes/ferramentas
app.include_router(user_config_router)  # User config (LLM para qualquer usuário autenticado)
app.include_router(webhook_router)  # Webhooks para automação CRM
app.include_router(lead_conversion_router)  # Conversão lead → mentorado
app.include_router(config_router)  # White Label config (público + admin)
app.include_router(user_router)  # User management + Evolution flywheel
app.include_router(sdk_router)  # SDK endpoints para claude-front-sdk-angular (sem prefixo)

# Inicializar ConfigManager para gerenciamento dinâmico
init_config_manager(get_db_connection)
logger.info("ConfigManager initialized - dynamic agent/tool management enabled")

# Authentication routes
@app.get("/api/auth/check-existing", response_model=dict)
async def check_existing_user(phone_number: str = None, username: str = None):
    """Check if phone_number or username already exists"""
    try:
        if not phone_number and not username:
            raise HTTPException(status_code=400, detail="phone_number ou username é obrigatório")

        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        conditions = []
        params = []

        if phone_number:
            normalized = normalize_phone_number(phone_number)
            conditions.append("phone_number = %s")
            params.append(normalized)
        if username:
            conditions.append("username = %s")
            params.append(username)

        where_clause = " OR ".join(conditions)

        cursor.execute(
            f"SELECT username, phone_number FROM users WHERE {where_clause}",
            params
        )
        existing_user = cursor.fetchone()

        cursor.close()
        connection.close()

        if existing_user:
            return {
                "status": "exists",
                "message": "Conta encontrada",
                "suggestion": "Tente fazer login ao invés de registrar",
                "existing_username": existing_user['username'],
                "existing_phone": existing_user['phone_number']
            }
        else:
            return {
                "status": "available",
                "message": "Número disponível para registro"
            }

    except HTTPException as e:
        raise e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Check existing user error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/auth/register", response_model=dict)
@limiter.limit("5/minute")
async def register(data: PhoneOTPRequest, request: Request):
    """Registro via WhatsApp OTP — envia OTP para o número informado"""
    try:
        phone = normalize_phone_number(data.phone_number)
        logger.info(f"Registration attempt for phone: {phone}")

        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        # Verificar se já existe usuário com este número
        cursor.execute(
            "SELECT user_id FROM users WHERE phone_number = %s",
            (phone,)
        )
        if cursor.fetchone():
            cursor.close()
            connection.close()
            raise HTTPException(status_code=409, detail="Número já cadastrado. Faça login.")

        # Gerar OTP
        otp = generate_otp()
        expires_at = datetime.now() + timedelta(minutes=10)

        # Limpar registros pendentes anteriores deste número
        cursor.execute(
            "DELETE FROM pending_registrations WHERE phone_number = %s",
            (phone,)
        )

        # Salvar registro pendente
        cursor.execute(
            """
            INSERT INTO pending_registrations (phone_number, otp, expires_at, attempts, role)
            VALUES (%s, %s, %s, 0, 'mentorado')
            """,
            (phone, otp, expires_at)
        )
        connection.commit()

        # Enviar OTP via WhatsApp
        sent = send_whatsapp_otp(phone, otp)

        cursor.close()
        connection.close()

        logger.info(f"Registration OTP for {phone}: {otp}")

        return {
            "status": "success",
            "message": "Código de verificação enviado via WhatsApp",
            "phone_number": phone,
            "otp_sent": sent,
            "expires_at": expires_at.strftime('%Y-%m-%d %H:%M:%S')
        }

    except HTTPException as e:
        raise e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Registration error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Add a force cleanup endpoint
@app.delete("/api/auth/force-cleanup", response_model=dict)
async def force_cleanup_all_registrations():
    """DANGER: Force cleanup all pending registrations - USE WITH CAUTION"""
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        
        # Delete ALL pending registrations
        cursor.execute("DELETE FROM pending_registrations")
        deleted_count = cursor.rowcount
        connection.commit()
        cursor.close()
        connection.close()
        
        logger.info(f"Force cleaned up {deleted_count} pending registrations")
        return {
            "status": "success",
            "message": f"Force cleaned up {deleted_count} pending registrations"
        }
        
    except Exception as e:
        logger.error(f"Force cleanup error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
@app.post("/api/auth/login", response_model=dict)
@limiter.limit("60/minute")
async def login(data: PhoneOTPRequest, request: Request):
    """Login via WhatsApp OTP — envia OTP para o número cadastrado"""
    try:
        phone = normalize_phone_number(data.phone_number)
        logger.info(f"Login attempt for phone: {phone}")

        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        # Verificar se usuário existe
        cursor.execute(
            "SELECT user_id, username FROM users WHERE phone_number = %s",
            (phone,)
        )
        user = cursor.fetchone()

        if not user:
            cursor.close()
            connection.close()
            raise HTTPException(status_code=404, detail="Número não cadastrado. Registre-se primeiro.")

        # Gerar OTP
        otp = generate_otp()
        expires_at = datetime.now() + timedelta(minutes=10)

        # Verificar se já existe OTP pendente
        cursor.execute(
            "SELECT verification_id FROM user_verifications WHERE user_id = %s AND is_verified = FALSE",
            (user['user_id'],)
        )
        existing = cursor.fetchone()

        if existing:
            cursor.execute(
                """
                UPDATE user_verifications
                SET otp = %s, phone_number = %s, created_at = %s, expires_at = %s, attempts = 0
                WHERE verification_id = %s
                """,
                (otp, phone, datetime.now(), expires_at, existing['verification_id'])
            )
        else:
            cursor.execute(
                """
                INSERT INTO user_verifications
                (user_id, phone_number, otp, created_at, expires_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (user['user_id'], phone, otp, datetime.now(), expires_at)
            )

        connection.commit()

        # Enviar OTP via WhatsApp
        sent = send_whatsapp_otp(phone, otp)

        cursor.close()
        connection.close()

        logger.info(f"Login OTP for {phone}: {otp}")

        return {
            "status": "success",
            "message": "Código de verificação enviado via WhatsApp",
            "phone_number": phone,
            "otp_sent": sent,
            "expires_at": expires_at.strftime('%Y-%m-%d %H:%M:%S')
        }

    except HTTPException as e:
        raise e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Login error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/auth/refresh", response_model=TokenData)
@limiter.limit("60/hour")  # Rate limit refresh to prevent abuse
async def refresh_access_token(refresh_data: RefreshRequest, request: Request):
    """Generate new access token using refresh token"""
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        # Verify refresh token
        user_id = verify_refresh_token(refresh_data.refresh_token, cursor)

        if not user_id:
            cursor.close()
            connection.close()
            raise HTTPException(status_code=401, detail="Refresh token inválido ou expirado")

        # Get user data
        cursor.execute(
            """
            SELECT user_id, username, email, phone_number, profile_image_url,
                   registration_date, account_status, verification_status, role, admin_level
            FROM users
            WHERE user_id = %s AND account_status = 'active'
            """,
            (user_id,)
        )

        user = cursor.fetchone()

        if not user:
            cursor.close()
            connection.close()
            raise HTTPException(status_code=401, detail="Usuário não encontrado")

        cursor.close()
        connection.close()

        # Convert datetime objects to strings
        for key, value in user.items():
            if isinstance(value, datetime):
                user[key] = value.strftime('%Y-%m-%d %H:%M:%S')

        # Generate new access token
        new_access_token = generate_access_token(user_id)

        return {
            "token": new_access_token,
            "user": user
        }

    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Refresh token error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/auth/logout", response_model=dict)
async def logout(logout_data: LogoutRequest, request: Request, current_user_id: int = Depends(get_user_from_token)):
    """Revoke refresh token on logout"""
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        # Revoke refresh token
        from datetime import timezone
        cursor.execute(
            """
            UPDATE refresh_tokens
            SET revoked = TRUE, revoked_at = %s
            WHERE refresh_token = %s AND user_id = %s
            """,
            (datetime.now(timezone.utc), logout_data.refresh_token, current_user_id)
        )

        connection.commit()
        cursor.close()
        connection.close()

        return {
            "status": "success",
            "message": "Logout realizado com sucesso"
        }

    except Exception as e:
        logger.error(f"Logout error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/auth/verify-otp", response_model=dict)
async def verify_otp(verification: OTPVerify):
    """Verifica OTP — funciona tanto para login quanto para registro"""
    try:
        phone = normalize_phone_number(verification.phone_number)
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        # === Tentar como LOGIN (user_verifications) ===
        cursor.execute(
            """
            SELECT v.*, u.user_id, u.username
            FROM user_verifications v
            JOIN users u ON v.user_id = u.user_id
            WHERE v.phone_number = %s AND v.is_verified = FALSE
            ORDER BY v.created_at DESC
            LIMIT 1
            """,
            (phone,)
        )
        login_record = cursor.fetchone()

        if login_record:
            # Verificar expiração
            now = datetime.now()
            if login_record['expires_at'] < now:
                cursor.close()
                connection.close()
                raise HTTPException(status_code=400, detail="OTP expirado")

            # Incrementar tentativas
            cursor.execute(
                "UPDATE user_verifications SET attempts = attempts + 1 WHERE verification_id = %s",
                (login_record['verification_id'],)
            )
            connection.commit()

            # Verificar OTP
            if login_record['otp'] != verification.otp:
                if login_record['attempts'] >= 3:
                    cursor.execute(
                        "UPDATE user_verifications SET expires_at = %s WHERE verification_id = %s",
                        (now - timedelta(minutes=1), login_record['verification_id'])
                    )
                    connection.commit()
                    cursor.close()
                    connection.close()
                    raise HTTPException(status_code=400, detail="Muitas tentativas. OTP expirado.")

                cursor.close()
                connection.close()
                raise HTTPException(
                    status_code=400,
                    detail=f"OTP inválido. Tentativas restantes: {3 - login_record['attempts']}"
                )

            # OTP válido — marcar como verificado
            cursor.execute(
                "UPDATE user_verifications SET is_verified = TRUE WHERE verification_id = %s",
                (login_record['verification_id'],)
            )

            # Atualizar last_login
            cursor.execute(
                "UPDATE users SET last_login = %s, verification_status = TRUE WHERE user_id = %s",
                (datetime.now(), login_record['user_id'])
            )
            connection.commit()

            # Gerar tokens
            access_token = generate_access_token(login_record['user_id'])
            refresh_token = generate_refresh_token(login_record['user_id'], cursor)
            connection.commit()

            # Buscar dados do usuário
            cursor.execute(
                """
                SELECT user_id, username, email, phone_number, registration_date,
                       last_login, account_status, profile_image_url, verification_status, role
                FROM users WHERE user_id = %s
                """,
                (login_record['user_id'],)
            )
            user = cursor.fetchone()
            cursor.close()
            connection.close()

            if user:
                for key, value in user.items():
                    if isinstance(value, datetime):
                        user[key] = value.strftime('%Y-%m-%d %H:%M:%S')

            return {
                "status": "success",
                "message": "Login realizado com sucesso",
                "token": access_token,
                "refresh_token": refresh_token,
                "user": user
            }

        # === Tentar como REGISTRO (pending_registrations) ===
        cursor.execute(
            """
            SELECT * FROM pending_registrations
            WHERE phone_number = %s
            ORDER BY expires_at DESC
            LIMIT 1
            """,
            (phone,)
        )
        pending = cursor.fetchone()

        if not pending:
            cursor.close()
            connection.close()
            raise HTTPException(status_code=404, detail="Nenhuma verificação pendente encontrada")

        # Verificar expiração
        now = datetime.now()
        if pending['expires_at'] < now:
            cursor.execute(
                "DELETE FROM pending_registrations WHERE registration_id = %s",
                (pending['registration_id'],)
            )
            connection.commit()
            cursor.close()
            connection.close()
            raise HTTPException(status_code=400, detail="OTP expirado. Registre-se novamente.")

        # Incrementar tentativas
        cursor.execute(
            "UPDATE pending_registrations SET attempts = attempts + 1 WHERE registration_id = %s",
            (pending['registration_id'],)
        )
        connection.commit()

        # Verificar OTP
        if pending['otp'] != verification.otp:
            if pending['attempts'] >= 3:
                cursor.execute(
                    "DELETE FROM pending_registrations WHERE registration_id = %s",
                    (pending['registration_id'],)
                )
                connection.commit()
                cursor.close()
                connection.close()
                raise HTTPException(status_code=400, detail="Muitas tentativas. Registre-se novamente.")

            cursor.close()
            connection.close()
            raise HTTPException(
                status_code=400,
                detail=f"OTP inválido. Tentativas restantes: {3 - pending['attempts']}"
            )

        # OTP válido — criar usuário
        role = pending.get('role', 'mentorado')
        phone_suffix = phone[-4:]

        cursor.execute(
            """
            INSERT INTO users (username, email, password_hash, phone_number, registration_date, account_status, verification_status, role)
            VALUES (%s, %s, %s, %s, %s, 'active', 1, %s)
            """,
            (f"user{phone_suffix}", "", "", phone, datetime.now(), role)
        )
        connection.commit()

        user_id = cursor.lastrowid

        # Atualizar username com ID
        username = f"user{phone_suffix}#{user_id:02d}"
        cursor.execute(
            "UPDATE users SET username = %s WHERE user_id = %s",
            (username, user_id)
        )

        # Remover registro pendente
        cursor.execute(
            "DELETE FROM pending_registrations WHERE registration_id = %s",
            (pending['registration_id'],)
        )
        connection.commit()

        # Gerar tokens
        access_token = generate_access_token(user_id)
        refresh_token = generate_refresh_token(user_id, cursor)
        connection.commit()

        cursor.close()
        connection.close()

        logger.info(f"User registered via OTP: {username} (ID: {user_id})")

        return {
            "status": "success",
            "message": "Conta criada com sucesso!",
            "token": access_token,
            "refresh_token": refresh_token,
            "user": {
                "user_id": user_id,
                "username": username,
                "phone_number": phone,
                "role": role
            }
        }

    except HTTPException as e:
        raise e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Verify OTP error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/auth/resend-otp", response_model=dict)
@limiter.limit("5/minute")
async def resend_otp(data: ResendOTPRequest, request: Request):
    """Reenvia OTP via WhatsApp (funciona para login e registro)"""
    try:
        phone = normalize_phone_number(data.phone_number)
        otp = generate_otp()
        expires_at = datetime.now() + timedelta(minutes=10)

        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        # Tentar como login (user_verifications)
        cursor.execute(
            """
            SELECT v.verification_id, u.user_id
            FROM user_verifications v
            JOIN users u ON v.user_id = u.user_id
            WHERE v.phone_number = %s AND v.is_verified = FALSE
            ORDER BY v.created_at DESC LIMIT 1
            """,
            (phone,)
        )
        login_record = cursor.fetchone()

        if login_record:
            cursor.execute(
                """
                UPDATE user_verifications
                SET otp = %s, created_at = %s, expires_at = %s, attempts = 0
                WHERE verification_id = %s
                """,
                (otp, datetime.now(), expires_at, login_record['verification_id'])
            )
            connection.commit()
        else:
            # Tentar como registro (pending_registrations)
            cursor.execute(
                "SELECT registration_id FROM pending_registrations WHERE phone_number = %s",
                (phone,)
            )
            pending = cursor.fetchone()

            if pending:
                cursor.execute(
                    """
                    UPDATE pending_registrations
                    SET otp = %s, expires_at = %s, attempts = 0
                    WHERE registration_id = %s
                    """,
                    (otp, expires_at, pending['registration_id'])
                )
                connection.commit()
            else:
                cursor.close()
                connection.close()
                raise HTTPException(status_code=404, detail="Nenhuma verificação pendente para este número")

        # Enviar OTP via WhatsApp
        sent = send_whatsapp_otp(phone, otp)

        cursor.close()
        connection.close()

        logger.info(f"Resent OTP for {phone}: {otp}")

        return {
            "status": "success",
            "message": "Novo código enviado via WhatsApp",
            "phone_number": phone,
            "otp_sent": sent,
            "expires_at": expires_at.strftime('%Y-%m-%d %H:%M:%S')
        }

    except HTTPException as e:
        raise e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Resend OTP error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.patch("/api/users/{user_id}", response_model=dict)
async def update_user(user_id: int, update_data: UpdateUserProfile, current_user_id: int = Depends(get_user_from_token)):
    try:
        # Check if the requesting user is authorized to update this profile
        if int(current_user_id) != user_id:
            raise HTTPException(status_code=403, detail="Access denied. You can only update your own profile")

        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        # Check if there are any fields to update
        update_fields = {}

        # Verificar username duplicado
        if update_data.username is not None:
            cursor.execute(
                "SELECT user_id FROM users WHERE username = %s AND user_id != %s",
                (update_data.username, user_id)
            )
            if cursor.fetchone():
                cursor.close()
                connection.close()
                raise HTTPException(status_code=409, detail="Nome de usuário já está em uso")
            update_fields["username"] = update_data.username

        # Verificar email duplicado
        if update_data.email is not None:
            cursor.execute(
                "SELECT user_id FROM users WHERE email = %s AND user_id != %s",
                (update_data.email, user_id)
            )
            if cursor.fetchone():
                cursor.close()
                connection.close()
                raise HTTPException(status_code=409, detail="Email já está em uso")
            update_fields["email"] = update_data.email

        if update_data.phone_number is not None:
            update_fields["phone_number"] = update_data.phone_number
        if update_data.profile_image_url is not None:
            update_fields["profile_image_url"] = update_data.profile_image_url

        if not update_fields:
            cursor.close()
            connection.close()
            raise HTTPException(status_code=400, detail="No valid fields to update")
            
        # Construct the SQL SET clause for the fields to update
        set_clause = ", ".join([f"{field} = %s" for field in update_fields.keys()])
        values = list(update_fields.values())
        values.append(user_id)  # Add user_id for the WHERE clause

        # Update the user profile
        cursor.execute(
            f"UPDATE users SET {set_clause} WHERE user_id = %s",
            values
        )
        connection.commit()
        
        # Get the updated user data
        cursor.execute(
            """
            SELECT user_id, username, email, phone_number, registration_date, 
                   last_login, account_status, profile_image_url, verification_status
            FROM users 
            WHERE user_id = %s
            """,
            (user_id,)
        )
        
        updated_user = cursor.fetchone()
        cursor.close()
        connection.close()
        
        # Convert datetime objects to strings
        if updated_user:
            for key, value in updated_user.items():
                if isinstance(value, datetime):
                    updated_user[key] = value.strftime('%Y-%m-%d %H:%M:%S')
        
        return {
            "status": "success",
            "message": "User profile updated successfully",
            "user": updated_user
        }
        
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Update user profile error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/users/{user_id}", response_model=dict)
async def get_user(user_id: int, current_user_id: int = Depends(get_user_from_token)):
    try:
        # Check if the requesting user is authorized
        if int(current_user_id) != user_id:
            raise HTTPException(status_code=403, detail="Access denied. You can only view your own profile")
        
        # Get user details
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        
        cursor.execute(
            """
            SELECT user_id, username, email, phone_number, registration_date, 
                   last_login, account_status, profile_image_url, verification_status
            FROM users WHERE user_id = %s
            """,
            (user_id,)
        )
        
        user = cursor.fetchone()
        cursor.close()
        connection.close()
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Convert datetime objects to strings
        for key, value in user.items():
            if isinstance(value, datetime):
                user[key] = value.strftime('%Y-%m-%d %H:%M:%S')
        
        return {
            "status": "success",
            "user": user
        }
        
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Get user error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/users/{user_id}", response_model=dict)
async def delete_user_account(
    user_id: int,
    current_user_id: int = Depends(get_user_from_token)
):
    """
    Deleta conta do usuário (LGPD - Direito de Exclusão)

    Autenticação via JWT — sem senha no sistema passwordless.
    Remove TODOS os dados relacionados.
    """
    try:
        # Verificar autorização (só pode deletar própria conta)
        if int(current_user_id) != user_id:
            raise HTTPException(
                status_code=403,
                detail="Acesso negado. Você só pode deletar sua própria conta"
            )

        connection = get_db_connection()
        if not connection:
            raise HTTPException(status_code=500, detail="Database connection failed")

        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            "SELECT username, email, phone_number FROM users WHERE user_id = %s",
            (user_id,)
        )
        user = cursor.fetchone()

        if not user:
            cursor.close()
            connection.close()
            raise HTTPException(status_code=404, detail="User not found")

        # 3. Log de auditoria (antes de deletar)
        logger.warning(
            f"LGPD DELETE: user_id={user_id}, username={user['username']}, "
            f"email={user['email']} - Account deletion requested"
        )

        # 4. Deletar dados relacionados (CASCADE vai deletar automaticamente):
        # - refresh_tokens (CASCADE)
        # - chat_sessions -> chat_messages (CASCADE)
        # - clients -> assessments -> scores/summaries (CASCADE)

        # Deletar user (triggers all CASCADEs)
        cursor.execute("DELETE FROM users WHERE user_id = %s", (user_id,))

        deleted_count = cursor.rowcount
        connection.commit()
        cursor.close()
        connection.close()

        if deleted_count > 0:
            logger.info(f"LGPD DELETE: User {user_id} account deleted successfully")
            return {
                "success": True,
                "message": "Conta deletada com sucesso. Todos os seus dados foram removidos.",
                "deleted_user_id": user_id
            }
        else:
            raise HTTPException(status_code=404, detail="User not found")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Delete user error: {e}")
        if connection:
            connection.rollback()
            connection.close()
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/user/delete-account", response_model=dict)
async def delete_own_account(
    current_user_id: int = Depends(get_user_from_token)
):
    """
    Endpoint simplificado para usuário deletar própria conta (LGPD)

    Extrai user_id do token JWT automaticamente.
    NÃO requer senha (confirmação já foi feita no frontend).
    """
    try:
        connection = get_db_connection()
        if not connection:
            raise HTTPException(status_code=500, detail="Database connection failed")

        cursor = connection.cursor(dictionary=True)

        # Buscar dados do usuário para log
        cursor.execute(
            "SELECT username, email FROM users WHERE user_id = %s",
            (current_user_id,)
        )
        user = cursor.fetchone()

        if not user:
            cursor.close()
            connection.close()
            raise HTTPException(status_code=404, detail="User not found")

        # Log de auditoria LGPD
        logger.warning(
            f"LGPD DELETE: user_id={current_user_id}, username={user['username']}, "
            f"email={user['email']} - Self-service account deletion"
        )

        # Deletar user (CASCADE deleta todos os dados relacionados)
        cursor.execute("DELETE FROM users WHERE user_id = %s", (current_user_id,))

        deleted_count = cursor.rowcount
        connection.commit()
        cursor.close()
        connection.close()

        if deleted_count > 0:
            logger.info(f"LGPD DELETE: User {current_user_id} deleted successfully")
            return {
                "status": "success",
                "message": "Conta excluída com sucesso. Seus dados foram removidos conforme LGPD."
            }
        else:
            raise HTTPException(status_code=404, detail="User not found")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Delete account error: {e}")
        if connection:
            connection.rollback()
            connection.close()
        raise HTTPException(status_code=500, detail=str(e))


# Report submission and processing
@app.get("/api/dashboard/statistics", response_model=dict)
async def get_dashboard_statistics(user_id: int = Depends(get_user_from_token)):
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        
        # Get user's report counts
        cursor.execute(
            """
            SELECT COUNT(*) as total_reports,
                COUNT(CASE WHEN status = 'analyzed' THEN 1 END) as analyzed_reports,
                COUNT(CASE WHEN status = 'submitted' OR status = 'analyzing' THEN 1 END) as pending_reports,
                COUNT(CASE WHEN status = 'resolved' THEN 1 END) as resolved_reports
            FROM reports
            WHERE user_id = %s
            """,
            (user_id,)
        )
        
        user_stats = cursor.fetchone()
        
        # Get waste type distribution for this user
        cursor.execute(
            """
            SELECT a.waste_type as name, COUNT(*) as count
            FROM reports r
            JOIN analysis_results a ON r.report_id = a.report_id
            WHERE r.user_id = %s AND a.waste_type IS NOT NULL
            GROUP BY a.waste_type
            ORDER BY count DESC
            """,
            (user_id,)
        )

        waste_distribution = cursor.fetchall()

        # Get severity distribution
        cursor.execute(
            """
            SELECT a.severity as severity_score, COUNT(*) as count
            FROM reports r
            JOIN analysis_results a ON r.report_id = a.report_id
            WHERE r.user_id = %s AND a.severity IS NOT NULL
            GROUP BY a.severity
            ORDER BY a.severity
            """,
            (user_id,)
        )

        severity_distribution = cursor.fetchall()

        # Get priority level distribution (based on severity)
        cursor.execute(
            """
            SELECT
                CASE
                    WHEN a.severity >= 8 THEN 'critical'
                    WHEN a.severity >= 6 THEN 'high'
                    WHEN a.severity >= 4 THEN 'medium'
                    ELSE 'low'
                END as priority_level,
                COUNT(*) as count
            FROM reports r
            JOIN analysis_results a ON r.report_id = a.report_id
            WHERE r.user_id = %s AND a.severity IS NOT NULL
            GROUP BY priority_level
            ORDER BY
                CASE priority_level
                    WHEN 'critical' THEN 1
                    WHEN 'high' THEN 2
                    WHEN 'medium' THEN 3
                    WHEN 'low' THEN 4
                END
            """,
            (user_id,)
        )

        priority_distribution = cursor.fetchall()
        
        # Get user's reports by month
        cursor.execute(
            """
            SELECT
                DATE_FORMAT(created_at, '%Y-%m') as month,
                COUNT(*) as count
            FROM reports
            WHERE user_id = %s
            AND created_at >= DATE_SUB(CURDATE(), INTERVAL 6 MONTH)
            GROUP BY DATE_FORMAT(created_at, '%Y-%m')
            ORDER BY month
            """,
            (user_id,)
        )

        monthly_reports = cursor.fetchall()

        # Get recent reports
        cursor.execute(
            """
            SELECT r.report_id, r.created_at as report_date, r.description, r.status,
                   r.latitude, r.longitude, r.image_url,
                   a.severity as severity_score,
                   CASE
                       WHEN a.severity >= 8 THEN 'critical'
                       WHEN a.severity >= 6 THEN 'high'
                       WHEN a.severity >= 4 THEN 'medium'
                       ELSE 'low'
                   END as priority_level,
                   a.waste_type
            FROM reports r
            LEFT JOIN analysis_results a ON r.report_id = a.report_id
            WHERE r.user_id = %s
            ORDER BY r.created_at DESC
            LIMIT 5
            """,
            (user_id,)
        )

        recent_reports = cursor.fetchall()

        # Convert datetime objects to strings in all results
        for report in recent_reports:
            if 'report_date' in report and report['report_date']:
                report['report_date'] = report['report_date'].strftime('%Y-%m-%d %H:%M:%S')
        
        # Get community statistics (user ranking, total contributors and registered users)
        cursor.execute(
            """
            SELECT COUNT(DISTINCT user_id) as total_contributors
            FROM reports
            WHERE user_id IS NOT NULL
            """)

        community_result = cursor.fetchone()
        total_contributors = community_result['total_contributors'] if community_result else 0

        # Get total registered users
        cursor.execute("SELECT COUNT(*) as total_users FROM users WHERE verification_status = 1")
        users_result = cursor.fetchone()
        total_registered_users = users_result['total_users'] if users_result else 0
        
        # Get user's ranking based on total reports
        cursor.execute(
            """
            SELECT ranking 
            FROM (
                SELECT user_id, 
                       COUNT(*) as report_count,
                       RANK() OVER (ORDER BY COUNT(*) DESC) as ranking
                FROM reports 
                WHERE user_id IS NOT NULL
                GROUP BY user_id
            ) ranked_users 
            WHERE user_id = %s
            """,
            (user_id,)
        )
        
        ranking_result = cursor.fetchone()
        user_rank = ranking_result['ranking'] if ranking_result else None
        
        # Create community stats object
        community_stats = {
            'total_registered_users': total_registered_users,
            'total_contributors': total_contributors,
            'user_rank': user_rank
        }
        
        cursor.close()
        connection.close()
        
        return {
            "status": "success",
            "user_stats": user_stats,
            "waste_distribution": waste_distribution,
            "severity_distribution": severity_distribution,
            "priority_distribution": priority_distribution,
            "monthly_reports": monthly_reports,
            "recent_reports": recent_reports,
            "community_stats": community_stats
        }

    except Exception as e:
        logger.error(f"Dashboard statistics error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/hotspots", response_model=dict)
async def get_hotspots(
    status: str = None,
    user_id: int = Depends(get_user_from_token)
):
    """Lista hotspots de resíduos"""
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        query = """
            SELECT hotspot_id, name, center_latitude, center_longitude,
                   radius_meters, total_reports, average_severity, status,
                   created_at, last_reported
            FROM hotspots
        """
        params = []

        if status:
            query += " WHERE status = %s"
            params.append(status)

        query += " ORDER BY total_reports DESC"

        cursor.execute(query, params)
        hotspots = cursor.fetchall()

        # Convert datetime and decimal objects
        for h in hotspots:
            if h.get('created_at'):
                h['created_at'] = h['created_at'].strftime('%Y-%m-%d %H:%M:%S')
            if h.get('last_reported'):
                h['last_reported'] = h['last_reported'].strftime('%Y-%m-%d %H:%M:%S')
            if h.get('center_latitude'):
                h['center_latitude'] = float(h['center_latitude'])
            if h.get('center_longitude'):
                h['center_longitude'] = float(h['center_longitude'])
            if h.get('average_severity'):
                h['average_severity'] = float(h['average_severity'])

        cursor.close()
        connection.close()

        return {"status": "success", "hotspots": hotspots, "count": len(hotspots)}

    except Exception as e:
        logger.error(f"Get hotspots error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/hotspots/{hotspot_id}/reports", response_model=dict)
async def get_hotspot_reports(
    hotspot_id: int,
    user_id: int = Depends(get_user_from_token)
):
    """Lista relatórios associados a um hotspot"""
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        # Get hotspot info
        cursor.execute(
            "SELECT * FROM hotspots WHERE hotspot_id = %s",
            (hotspot_id,)
        )
        hotspot = cursor.fetchone()

        if not hotspot:
            raise HTTPException(status_code=404, detail="Hotspot not found")

        # Get reports near the hotspot
        cursor.execute(
            """
            SELECT r.report_id, r.latitude, r.longitude, r.description,
                   r.status, r.severity, r.image_url, r.created_at,
                   a.waste_type
            FROM reports r
            LEFT JOIN analysis_results a ON r.report_id = a.report_id
            WHERE ST_Distance_Sphere(
                POINT(r.longitude, r.latitude),
                POINT(%s, %s)
            ) <= %s
            ORDER BY r.created_at DESC
            """,
            (hotspot['center_longitude'], hotspot['center_latitude'], hotspot['radius_meters'])
        )
        reports = cursor.fetchall()

        # Convert datetime objects
        for r in reports:
            if r.get('created_at'):
                r['created_at'] = r['created_at'].strftime('%Y-%m-%d %H:%M:%S')
            if r.get('latitude'):
                r['latitude'] = float(r['latitude'])
            if r.get('longitude'):
                r['longitude'] = float(r['longitude'])

        cursor.close()
        connection.close()

        return {"status": "success", "hotspot_id": hotspot_id, "reports": reports}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get hotspot reports error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# Status Update Endpoints (DT-001 fix)
# ============================================================

VALID_REPORT_STATUSES = ['submitted', 'analyzing', 'analyzed', 'resolved', 'rejected']
VALID_HOTSPOT_STATUSES = ['active', 'monitoring', 'resolved']


@app.patch("/api/reports/{report_id}/status", response_model=dict)
async def update_report_status(
    report_id: int,
    status_data: UpdateReportStatus,
    user_id: int = Depends(get_user_from_token)
):
    """Atualiza o status de um relatório"""
    try:
        # Validate status
        if status_data.status not in VALID_REPORT_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=f"Status inválido. Valores permitidos: {', '.join(VALID_REPORT_STATUSES)}"
            )

        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        # Check if report exists
        cursor.execute("SELECT report_id, status FROM reports WHERE report_id = %s", (report_id,))
        report = cursor.fetchone()

        if not report:
            cursor.close()
            connection.close()
            raise HTTPException(status_code=404, detail="Relatório não encontrado")

        old_status = report['status']

        # Update status
        cursor.execute(
            "UPDATE reports SET status = %s WHERE report_id = %s",
            (status_data.status, report_id)
        )
        connection.commit()

        cursor.close()
        connection.close()

        logger.info(f"Report {report_id} status changed: {old_status} -> {status_data.status} by user {user_id}")

        return {
            "status": "success",
            "message": f"Status atualizado para '{status_data.status}'",
            "report_id": report_id,
            "old_status": old_status,
            "new_status": status_data.status
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Update report status error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/hotspots/{hotspot_id}/status", response_model=dict)
async def update_hotspot_status(
    hotspot_id: int,
    status_data: UpdateHotspotStatus,
    user_id: int = Depends(get_user_from_token)
):
    """Atualiza o status de um hotspot"""
    try:
        # Validate status
        if status_data.status not in VALID_HOTSPOT_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=f"Status inválido. Valores permitidos: {', '.join(VALID_HOTSPOT_STATUSES)}"
            )

        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        # Check if hotspot exists
        cursor.execute("SELECT hotspot_id, status FROM hotspots WHERE hotspot_id = %s", (hotspot_id,))
        hotspot = cursor.fetchone()

        if not hotspot:
            cursor.close()
            connection.close()
            raise HTTPException(status_code=404, detail="Hotspot não encontrado")

        old_status = hotspot['status']

        # Update status
        cursor.execute(
            "UPDATE hotspots SET status = %s WHERE hotspot_id = %s",
            (status_data.status, hotspot_id)
        )
        connection.commit()

        cursor.close()
        connection.close()

        logger.info(f"Hotspot {hotspot_id} status changed: {old_status} -> {status_data.status} by user {user_id}")

        return {
            "status": "success",
            "message": f"Status atualizado para '{status_data.status}'",
            "hotspot_id": hotspot_id,
            "old_status": old_status,
            "new_status": status_data.status
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Update hotspot status error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/process-queue", response_model=dict)
async def process_queue(background_tasks: BackgroundTasks, user_id: int = Depends(get_user_from_token)):
    """Process the queue of unanalyzed reports"""
    try:
        # Get database connection
        connection = get_db_connection()
        if not connection:
            raise HTTPException(status_code=500, detail="Failed to connect to database")
        
        cursor = connection.cursor(dictionary=True)
        
        # Get unprocessed reports from the queue
        cursor.execute(
            """
            SELECT q.queue_id, q.report_id, q.image_url
            FROM image_processing_queue q
            WHERE q.status = 'pending'
            ORDER BY q.queued_at ASC
            LIMIT 10
            """
        )
        
        queue_items = cursor.fetchall()
        cursor.close()
        connection.close()
        
        if not queue_items:
            return {"status": "success", "message": "No items in the queue", "processed_count": 0}
        
        # Process each queue item in the background
        processed_count = 0
        for item in queue_items:
            # Update queue item status to processing
            connection = get_db_connection()
            cursor = connection.cursor()
            
            cursor.execute(
                """
                UPDATE image_processing_queue
                SET status = 'processing', processed_at = %s
                WHERE queue_id = %s
                """,
                (datetime.now(), item['queue_id'])
            )
            connection.commit()
            cursor.close()
            connection.close()
            
            # Add report to the background processing queue
            background_tasks.add_task(process_report, item['report_id'], background_tasks)
            processed_count += 1
        
        return {
            "status": "success",
            "message": f"{processed_count} reports added to processing queue",
            "processed_count": processed_count
        }
       
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Error processing queue: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# REMOVIDO: Vector search helper functions - Amazon Titan Embeddings
# Estas funções foram desabilitadas pois embedding_enabled = False
# Aguardando implementação de alternativa open-source para embeddings vetoriais
# def invoke_titan_embed_text(text: str) -> Optional[List[float]]:
#     """Create embedding for text using Amazon Titan Embed Image (multimodal)"""
#     if not embedding_enabled or not text:
#         return None
#
#     try:
#         # Prepare the request payload for Titan Multimodal Embed with text input and 1024 dimensions
#         payload = {
#             "inputText": text,
#             "embeddingConfig": {
#                 "outputEmbeddingLength": 1024
#             }
#         }
#
#         # Use boto3 bedrock_runtime to invoke Titan Embed Image model (supports text too)
#         response = bedrock_runtime.invoke_model(
#             modelId="amazon.titan-embed-image-v1",
#             body=json.dumps(payload)
#         )
#
#         result = json.loads(response['body'].read())
#         embedding = result.get('embedding', [])
#         return embedding if embedding else None
#
#     except Exception as e:
#         logger.error(f"Error creating text embedding with Titan: {e}")
#         return None
#
# def invoke_titan_embed_image(image_data: str) -> Optional[List[float]]:
#     """Create embedding for image using Amazon Titan Embed Image"""
#     if not embedding_enabled or not image_data:
#         return None
#
#     try:
#         # Prepare the request payload for Titan Image Embed with 1024 dimensions
#         payload = {
#             "inputImage": image_data,  # base64 encoded image
#             "embeddingConfig": {
#                 "outputEmbeddingLength": 1024
#             }
#         }
#
#         # Use boto3 bedrock_runtime to invoke Titan Embed Image model
#         response = bedrock_runtime.invoke_model(
#             modelId="amazon.titan-embed-image-v1",
#             body=json.dumps(payload)
#         )
#
#         result = json.loads(response['body'].read())
#         embedding = result.get('embedding', [])
#         return embedding if embedding else None
#
#     except Exception as e:
#         logger.error(f"Error creating image embedding with Titan: {e}")
#         return None
#
# def create_location_embedding(latitude: float, longitude: float) -> Optional[List[float]]:
#     """Create embedding for geographic location using Titan Text Embed"""
#     if not embedding_enabled:
#         return None
#
#     try:
#         # Create a location description string
#         location_text = f"Geographic location at latitude {latitude:.6f} longitude {longitude:.6f}"
#
#         # Add contextual information about Timor-Leste regions
#         region_context = ""
#         if -8.3 <= latitude <= -8.1 and 125.5 <= longitude <= 125.7:
#             region_context = " in Dili capital city urban area Timor-Leste"
#         elif -8.5 <= latitude <= -8.0 and 125.0 <= longitude <= 127.0:
#             region_context = " in northern Timor-Leste coastal region"
#         elif -9.0 <= latitude <= -8.5 and 125.0 <= longitude <= 127.0:
#             region_context = " in southern Timor-Leste mountainous region"
#         else:
#             region_context = " in Timor-Leste"
#
#         location_text += region_context
#
#         # Generate embedding using Titan Text Embed
#         return invoke_titan_embed_text(location_text)
#     except Exception as e:
#         logger.error(f"Error creating location embedding: {e}")
#         return None
#
# def create_image_content_embedding(analysis_result: dict, image_data: str = None) -> Optional[List[float]]:
#     """Create embedding from image using Titan Embed Image or text analysis"""
#     if not embedding_enabled or not analysis_result:
#         return None
#
#     try:
#         # First try to create image embedding if we have image data
#         if image_data:
#             image_embedding = invoke_titan_embed_image(image_data)
#             if image_embedding:
#                 return image_embedding
#
#         # Fallback to text embedding from analysis results
#         content_parts = []
#
#         if analysis_result.get('waste_type'):
#             content_parts.append(f"Waste type: {analysis_result['waste_type']}")
#
#         if analysis_result.get('full_description'):
#             content_parts.append(f"Description: {analysis_result['full_description']}")
#
#         if analysis_result.get('analysis_notes'):
#             content_parts.append(f"Analysis: {analysis_result['analysis_notes']}")
#
#         if analysis_result.get('environmental_impact'):
#             content_parts.append(f"Environmental impact: {analysis_result['environmental_impact']}")
#
#         if analysis_result.get('safety_concerns'):
#             content_parts.append(f"Safety concerns: {analysis_result['safety_concerns']}")
#
#         # Combine all parts
#         content_text = " ".join(content_parts)
#
#         if not content_text:
#             return None
#
#         # Generate text embedding using Titan
#         return invoke_titan_embed_text(content_text)
#
#     except Exception as e:
#         logger.error(f"Error creating image content embedding: {e}")
#         return None


@app.get("/api/test/nova", response_model=dict)
async def test_nova_api(image_url: str, user_id: int = Depends(get_user_from_token)):
    try:
        # Simple test endpoint to check if the AgentCore/Nova API integration is working
        analysis_result, _ = await analyze_image_with_claude(image_url, 0.0, 0.0, "Test image")
        
        if not analysis_result:
            raise HTTPException(status_code=500, detail="Failed to analyze image")
            
        return {
            "status": "success",
            "analysis": analysis_result
        }
        
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Test Nova API error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== CHAT API WITH AGENTCORE ====================

# Pydantic models for chat
class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    session_id: Optional[str] = None

# Database tool functions for AgentCore
def get_waste_statistics() -> dict:
    """Get overall waste statistics from the database"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # Get total reports
        cursor.execute("SELECT COUNT(*) as total FROM reports")
        total_reports = cursor.fetchone()['total']

        # Get reports by status
        cursor.execute("""
            SELECT status, COUNT(*) as count
            FROM reports
            GROUP BY status
        """)
        status_counts = cursor.fetchall()

        # Get reports by waste type
        cursor.execute("""
            SELECT wt.name, COUNT(*) as count
            FROM analysis_results ar
            JOIN waste_types wt ON ar.waste_type_id = wt.waste_type_id
            GROUP BY wt.name
            ORDER BY count DESC
            LIMIT 10
        """)
        waste_type_counts = cursor.fetchall()

        cursor.close()
        conn.close()

        return {
            "total_reports": total_reports,
            "status_breakdown": status_counts,
            "top_waste_types": waste_type_counts
        }
    except Exception as e:
        logger.error(f"Error getting waste statistics: {e}")
        return {"error": str(e)}

def search_reports_by_location(district: str = None, limit: int = 10) -> dict:
    """Search waste reports by location"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        if district:
            cursor.execute("""
                SELECT r.report_id, r.latitude, r.longitude, r.report_date,
                       r.description, r.status, r.address_text,
                       ar.severity_score, ar.priority_level, wt.name as waste_type
                FROM reports r
                LEFT JOIN analysis_results ar ON r.report_id = ar.report_id
                LEFT JOIN waste_types wt ON ar.waste_type_id = wt.waste_type_id
                WHERE r.address_text LIKE %s
                ORDER BY r.report_date DESC
                LIMIT %s
            """, (f'%{district}%', limit))
        else:
            cursor.execute("""
                SELECT r.report_id, r.latitude, r.longitude, r.report_date,
                       r.description, r.status, r.address_text,
                       ar.severity_score, ar.priority_level, wt.name as waste_type
                FROM reports r
                LEFT JOIN analysis_results ar ON r.report_id = ar.report_id
                LEFT JOIN waste_types wt ON ar.waste_type_id = wt.waste_type_id
                ORDER BY r.report_date DESC
                LIMIT %s
            """, (limit,))

        reports = cursor.fetchall()
        cursor.close()
        conn.close()

        # Convert Decimal and date/datetime objects to JSON-serializable types
        for report in reports:
            if 'latitude' in report and report['latitude'] is not None:
                report['latitude'] = float(report['latitude'])
            if 'longitude' in report and report['longitude'] is not None:
                report['longitude'] = float(report['longitude'])
            if 'severity_score' in report and report['severity_score'] is not None:
                report['severity_score'] = float(report['severity_score'])
            if 'report_date' in report and report['report_date'] is not None:
                report['report_date'] = report['report_date'].isoformat()

        return {"reports": reports, "count": len(reports)}
    except Exception as e:
        logger.error(f"Error searching reports: {e}")
        return {"error": str(e)}

def get_hotspot_information(limit: int = 10) -> dict:
    """Get information about waste hotspots"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT h.hotspot_id, h.name, h.center_latitude, h.center_longitude,
                   h.total_reports, h.average_severity, h.status, h.first_reported, h.last_reported
            FROM hotspots h
            WHERE h.status = 'active'
            ORDER BY h.average_severity DESC, h.total_reports DESC
            LIMIT %s
        """, (limit,))

        hotspots = cursor.fetchall()
        cursor.close()
        conn.close()

        # Convert Decimal and date objects to JSON-serializable types
        for hotspot in hotspots:
            if 'center_latitude' in hotspot and hotspot['center_latitude'] is not None:
                hotspot['center_latitude'] = float(hotspot['center_latitude'])
            if 'center_longitude' in hotspot and hotspot['center_longitude'] is not None:
                hotspot['center_longitude'] = float(hotspot['center_longitude'])
            if 'average_severity' in hotspot and hotspot['average_severity'] is not None:
                hotspot['average_severity'] = float(hotspot['average_severity'])
            if 'first_reported' in hotspot and hotspot['first_reported'] is not None:
                hotspot['first_reported'] = hotspot['first_reported'].isoformat()
            if 'last_reported' in hotspot and hotspot['last_reported'] is not None:
                hotspot['last_reported'] = hotspot['last_reported'].isoformat()

        return {"hotspots": hotspots, "count": len(hotspots)}
    except Exception as e:
        logger.error(f"Error getting hotspots: {e}")
        return {"error": str(e)}

def get_waste_types_info() -> dict:
    """Get information about waste types and categories"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT waste_type_id, name, description, hazard_level, recyclable
            FROM waste_types
            ORDER BY name
        """)

        waste_types = cursor.fetchall()
        cursor.close()
        conn.close()

        return {"waste_types": waste_types, "count": len(waste_types)}
    except Exception as e:
        logger.error(f"Error getting waste types: {e}")
        return {"error": str(e)}

def execute_sql_query(sql_query: str) -> dict:
    """Execute a READ-ONLY SQL query and return results"""
    try:
        # Security: Only allow SELECT statements
        query_upper = sql_query.strip().upper()
        if not query_upper.startswith('SELECT'):
            return {"error": "Only SELECT queries are allowed for security reasons"}

        # Block dangerous keywords
        dangerous_keywords = ['DROP', 'DELETE', 'UPDATE', 'INSERT', 'ALTER', 'CREATE', 'TRUNCATE', 'EXEC', 'EXECUTE']
        for keyword in dangerous_keywords:
            if keyword in query_upper:
                return {"error": f"Query contains forbidden keyword: {keyword}"}

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # Execute the query with a limit to prevent large result sets
        if 'LIMIT' not in query_upper:
            sql_query = sql_query.rstrip(';') + ' LIMIT 100'

        cursor.execute(sql_query)
        results = cursor.fetchall()
        cursor.close()
        conn.close()

        # Convert non-serializable types
        for row in results:
            for key, value in row.items():
                if hasattr(value, 'isoformat'):  # datetime/date
                    row[key] = value.isoformat()
                elif isinstance(value, type(Decimal('0'))):  # Decimal
                    row[key] = float(value)

        return {
            "success": True,
            "rows": results,
            "count": len(results),
            "query": sql_query
        }
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Error executing SQL query: {error_msg}")

        # Provide helpful error messages for common issues
        helpful_hint = ""
        if "Unknown column" in error_msg and "hotspot_id" in error_msg:
            helpful_hint = " HINT: reports table does not have hotspot_id. Use the hotspot_reports junction table to join reports and hotspots."
        elif "Unknown column" in error_msg:
            helpful_hint = " HINT: Check the column names in the schema. Make sure you're using the correct table aliases."
        elif "table" in error_msg.lower() and "doesn't exist" in error_msg.lower():
            helpful_hint = " HINT: Check the table name spelling and ensure you're only using public tables."

        return {
            "error": error_msg + helpful_hint,
            "success": False,
            "query_attempted": sql_query
        }

@app.post("/api/chat", deprecated=True)
@limiter.limit("30/minute")  # Rate limit: 30 requests per minute per IP
async def chat_with_agentcore(_chat_request: ChatRequest, request: Request, user_id: int = Depends(get_user_from_token)):
    """
    DEPRECATED: Use WebSocket endpoint /api/chat/ws instead

    This endpoint has been completely removed and replaced with Claude Agent SDK.
    The new WebSocket endpoint offers:
    - Real-time streaming responses
    - RAG (Retrieval Augmented Generation) with vector embeddings
    - Better performance with connection pooling
    - Tool execution indicators
    - Claude Opus 4.5 powered responses

    To use the new chat system:
    1. Connect to WebSocket: ws://your-host/api/chat/ws?token=YOUR_JWT
    2. Send messages in format: {"message": "your message", "conversation_id": "optional"}
    3. Receive streaming responses with tool indicators

    Legacy chat endpoint using AgentCore with database tools - Requires JWT
    """
    # REMOVIDO: Todo o código Bedrock AgentCore foi substituído por Claude Agent SDK
    # Este endpoint agora apenas retorna erro direcionando para o WebSocket
    raise HTTPException(
        status_code=410,  # 410 Gone - resource is no longer available
        detail={
            "error": "This endpoint has been permanently removed",
            "message": "Please use the WebSocket endpoint instead: /api/chat/ws",
            "migration_guide": {
                "websocket_url": "/api/chat/ws",
                "authentication": "Pass JWT token as query parameter: ?token=YOUR_JWT",
                "message_format": {"message": "string", "conversation_id": "optional_string"},
                "features": [
                    "Real-time streaming",
                    "RAG with vector search",
                    "Claude Opus 4.5 vision",
                    "Tool execution indicators"
                ]
            }
        }
    )

    # CÓDIGO ANTIGO REMOVIDO (linhas 3650-4066):
    # - Bedrock AgentCore tool configuration
    # - bedrock_runtime.converse() calls
    # - Nova Pro model usage
    # - Tool execution loop
    # Este código foi substituído por routes/chat_routes.py usando Claude Agent SDK

    # Original function body removed - see git history for reference
    # The entire 400+ lines of Bedrock code has been replaced with WebSocket implementation

# ============== Chat History Endpoints ==============

@app.get("/api/chat/sessions")
async def list_chat_sessions(
    page: int = 1,
    per_page: int = 20,
    target_user_id: Optional[int] = None,
    user_id: int = Depends(get_user_from_token)
):
    """Get chat sessions for a user. Admins can view other users' sessions."""
    # Determinar qual user_id usar para buscar sessões
    effective_user_id = user_id

    if target_user_id and target_user_id != user_id:
        # Verificar se é admin
        user_role = get_user_role(user_id)
        if user_role != "admin":
            raise HTTPException(status_code=403, detail="Apenas admins podem ver sessões de outros usuários")
        effective_user_id = target_user_id

    result = get_chat_sessions(effective_user_id, page, per_page)
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])

    return {"success": True, "data": result}

@app.get("/api/chat/sessions/{session_id}/messages")
async def list_session_messages(
    session_id: str,
    page: int = 1,
    per_page: int = 50,
    user_id: int = Depends(get_user_from_token)
):
    """Get messages for a specific chat session. Admins can view any session."""
    # Primeiro, verificar se é admin - admins podem ver qualquer sessão
    user_role = get_user_role(user_id)
    effective_user_id = user_id if user_role != "admin" else None  # None = sem filtro de user

    result = get_chat_messages(session_id, effective_user_id, page, per_page)
    if "error" in result:
        if "not found" in result["error"].lower():
            raise HTTPException(status_code=404, detail=result["error"])
        raise HTTPException(status_code=500, detail=result["error"])

    return {"success": True, "data": result}

class UpdateSessionTitle(BaseModel):
    title: str

@app.patch("/api/chat/sessions/{session_id}")
async def update_chat_session_title(
    session_id: str,
    update_data: UpdateSessionTitle,
    user_id: int = Depends(get_user_from_token)
):
    """Update the title of a chat session"""
    success = update_session_title(session_id, user_id, update_data.title)
    if not success:
        raise HTTPException(status_code=404, detail="Session not found or update failed")

    return {"success": True, "message": "Session title updated"}

@app.delete("/api/chat/sessions/{session_id}")
async def delete_chat_session_endpoint(
    session_id: str,
    user_id: int = Depends(get_user_from_token)
):
    """Delete a chat session and all its messages"""
    success = delete_chat_session(session_id, user_id)
    if not success:
        raise HTTPException(status_code=404, detail="Session not found or delete failed")

    return {"success": True, "message": "Session deleted"}

# ============== End Chat History Endpoints ==============

def handle_chat_fallback(user_message: str) -> str:
    """Fallback handler when AgentCore is not available"""
    user_message_lower = user_message.lower()

    # Simple keyword-based responses
    if any(word in user_message_lower for word in ['statistic', 'total', 'how many', 'count']):
        stats = get_waste_statistics()
        if 'error' not in stats:
            return f"""**📊 crm Statistics**

**Total Reports:** {stats['total_reports']}

**Reports by Status:**
{chr(10).join([f"- {s['status']}: {s['count']}" for s in stats['status_breakdown']])}

**Top Waste Types:**
{chr(10).join([f"- {w['name']}: {w['count']} reports" for w in stats['top_waste_types'][:5]])}"""

    elif any(word in user_message_lower for word in ['hotspot', 'problem area', 'worst']):
        hotspots = get_hotspot_information(5)
        if 'error' not in hotspots and hotspots['count'] > 0:
            return f"""**🔥 Active Waste Hotspots**

Found {hotspots['count']} active hotspots:

{chr(10).join([f"- **{h['name']}**: {h['total_reports']} reports (Severity: {h['average_severity']:.1f})" for h in hotspots['hotspots']])}"""

    elif any(word in user_message_lower for word in ['waste type', 'category', 'categories']):
        waste_types = get_waste_types_info()
        if 'error' not in waste_types:
            return f"""**🗑️ Waste Categories**

{chr(10).join([f"- **{w['name']}** ({w['hazard_level']} hazard): {w['description']}" for w in waste_types['waste_types'][:8]])}"""

    else:
        return """👋 Hello! I'm crm AI Assistant.

I can help you with:
- 📊 **Statistics**: Ask about total reports and trends
- 🗺️ **Locations**: Search reports by district
- 🔥 **Hotspots**: Find problem areas
- 🗑️ **Waste Types**: Learn about waste categories

What would you like to know?"""

@app.exception_handler(404)
async def custom_404_handler(request: requests, _exc: HTTPException):
    return JSONResponse(
        status_code=404,
        content={
            "project": "crm",
            "message": "🌱 This path doesn't exist in our crm ecosystem",
            "Contact": "https://www.agentesintegrados.com",
            "Visit": "www.nandamac.cloud"
        }
    )

# ============== Background Jobs ==============

def cleanup_expired_tokens():
    """Remove expired and revoked refresh tokens from database (runs daily at 3 AM)"""
    try:
        connection = get_db_connection()
        if not connection:
            logger.error("Failed to get database connection for token cleanup")
            return

        cursor = connection.cursor()

        cursor.execute("""
            DELETE FROM refresh_tokens
            WHERE expires_at < NOW() OR revoked = TRUE
        """)
        connection.commit()
        deleted = cursor.rowcount

        cursor.close()
        connection.close()

        if ENVIRONMENT != "production":
            logger.info(f"[Cleanup] Removed {deleted} expired/revoked refresh tokens")

    except Exception as e:
        logger.error(f"Token cleanup error: {e}")

# =====================================================
# ENDPOINTS DE PERFIL PROFISSIONAL (CLIENTS)
# =====================================================

@app.post("/api/clients", response_model=dict)
async def create_client_profile(
    data: dict,
    user_id: int = Depends(get_user_from_token)
):
    """Cria perfil profissional do mentorado"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Erro ao conectar ao banco")

    try:
        cursor = conn.cursor(dictionary=True)

        # Verificar se já existe perfil
        cursor.execute("SELECT client_id FROM clients WHERE user_id = %s", (user_id,))
        existing = cursor.fetchone()

        if existing:
            cursor.close()
            conn.close()
            raise HTTPException(status_code=400, detail="Perfil já existe. Use PATCH para atualizar.")

        # Criar perfil
        cursor.execute("""
            INSERT INTO clients (
                user_id, profession, specialty, years_experience,
                current_revenue, desired_revenue, main_challenge,
                has_secretary, team_size
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            user_id,
            data.get('profession'),
            data.get('specialty'),
            data.get('years_experience', 0),
            data.get('current_revenue', 0),
            data.get('desired_revenue', 0),
            data.get('main_challenge'),
            data.get('has_secretary', False),
            data.get('team_size', 1)
        ))

        conn.commit()
        client_id = cursor.lastrowid

        # Retornar perfil criado
        cursor.execute("SELECT * FROM clients WHERE client_id = %s", (client_id,))
        client = cursor.fetchone()

        cursor.close()
        conn.close()

        return {"client": client}

    except HTTPException:
        if conn:
            conn.close()
        raise
    except Exception as e:
        logger.error(f"Erro ao criar perfil: {e}")
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/clients/me", response_model=dict)
async def get_my_client_profile(
    user_id: int = Depends(get_user_from_token)
):
    """Obtém perfil profissional do usuário autenticado"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Erro ao conectar ao banco")

    try:
        cursor = conn.cursor(dictionary=True)

        # Buscar perfil de users (não existe tabela clients separada)
        cursor.execute("""
            SELECT user_id, username, email, phone_number, profession, specialty,
                   current_revenue, desired_revenue, profile_image_url,
                   registration_date, current_stage_key
            FROM users WHERE user_id = %s
        """, (user_id,))
        client = cursor.fetchone()

        cursor.close()
        conn.close()

        if not client:
            raise HTTPException(status_code=404, detail="Perfil não encontrado")

        return {"client": client}

    except HTTPException:
        if conn:
            conn.close()
        raise
    except Exception as e:
        logger.error(f"Erro ao buscar perfil: {e}")
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/clients/me", response_model=dict)
async def update_my_client_profile(
    data: dict,
    user_id: int = Depends(get_user_from_token)
):
    """Atualiza perfil profissional do usuário autenticado"""
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Erro ao conectar ao banco")

    try:
        cursor = conn.cursor(dictionary=True)

        # Verificar se user existe
        cursor.execute("SELECT user_id FROM users WHERE user_id = %s", (user_id,))
        existing = cursor.fetchone()

        if not existing:
            cursor.close()
            conn.close()
            raise HTTPException(status_code=404, detail="Usuário não encontrado.")

        # Construir query de update dinamicamente (campos que existem em users)
        update_fields = []
        values = []

        if 'profession' in data:
            update_fields.append("profession = %s")
            values.append(data['profession'])
        if 'specialty' in data:
            update_fields.append("specialty = %s")
            values.append(data['specialty'])
        if 'current_revenue' in data:
            update_fields.append("current_revenue = %s")
            values.append(data['current_revenue'])
        if 'desired_revenue' in data:
            update_fields.append("desired_revenue = %s")
            values.append(data['desired_revenue'])
        if 'phone_number' in data:
            update_fields.append("phone_number = %s")
            values.append(data['phone_number'])

        if not update_fields:
            cursor.close()
            conn.close()
            raise HTTPException(status_code=400, detail="Nenhum campo para atualizar")

        values.append(user_id)

        query = f"UPDATE users SET {', '.join(update_fields)} WHERE user_id = %s"
        cursor.execute(query, values)
        conn.commit()

        # Retornar perfil atualizado
        cursor.execute("""
            SELECT user_id, username, email, phone_number, profession, specialty,
                   current_revenue, desired_revenue, profile_image_url,
                   registration_date, current_stage_key
            FROM users WHERE user_id = %s
        """, (user_id,))
        client = cursor.fetchone()

        cursor.close()
        conn.close()

        return {"client": client}

    except HTTPException:
        if conn:
            conn.close()
        raise
    except Exception as e:
        logger.error(f"Erro ao atualizar perfil: {e}")
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=str(e))


# =====================================================
# ENDPOINTS DE DIAGNÓSTICO E AVALIAÇÕES
# =====================================================

@app.get("/api/diagnosis/questions", response_model=dict)
async def list_diagnosis_questions():
    """
    Lista todas as perguntas do diagnóstico organizadas por área (público)
    """
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Erro ao conectar ao banco")

    try:
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                da.area_id, da.area_key, da.area_name, da.area_order, da.description, da.icon,
                dq.question_id, dq.question_text, dq.question_order, dq.help_text
            FROM diagnosis_areas da
            LEFT JOIN diagnosis_questions dq ON da.area_id = dq.area_id
            ORDER BY da.area_order, dq.question_order
        """)

        results = cursor.fetchall()
        cursor.close()
        conn.close()

        # Organizar por área
        areas = {}
        for row in results:
            area_id = row['area_id']
            if area_id not in areas:
                areas[area_id] = {
                    'area_id': area_id,
                    'area_key': row['area_key'],
                    'area_name': row['area_name'],
                    'area_order': row['area_order'],
                    'description': row['description'],
                    'icon': row['icon'],
                    'questions': []
                }

            if row['question_id']:  # Tem pergunta
                areas[area_id]['questions'].append({
                    'question_id': row['question_id'],
                    'question_text': row['question_text'],
                    'question_order': row['question_order'],
                    'help_text': row['help_text']
                })

        return {
            "success": True,
            "data": list(areas.values())
        }

    except Exception as e:
        logger.error(f"Erro ao listar perguntas: {e}")
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/assessments", response_model=dict)
async def create_assessment(
    user_id: int = Depends(get_user_from_token)
):
    """
    Inicia uma nova avaliação/diagnóstico para o usuário
    """
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Erro ao conectar ao banco")

    try:
        cursor = conn.cursor(dictionary=True)

        # Verificar se client existe, se não criar
        cursor.execute("SELECT client_id FROM clients WHERE user_id = %s", (user_id,))
        client = cursor.fetchone()

        if not client:
            cursor.execute("""
                INSERT INTO clients (user_id) VALUES (%s)
            """, (user_id,))
            conn.commit()
            client_id = cursor.lastrowid
        else:
            client_id = client['client_id']

        # Criar nova avaliação
        cursor.execute("""
            INSERT INTO assessments (client_id, status)
            VALUES (%s, 'in_progress')
        """, (client_id,))
        conn.commit()
        assessment_id = cursor.lastrowid

        cursor.close()
        conn.close()

        return {
            "assessment": {
                "assessment_id": assessment_id,
                "client_id": client_id,
                "status": "in_progress",
                "started_at": datetime.now().isoformat()
            }
        }

    except Exception as e:
        logger.error(f"Erro ao criar avaliação: {e}")
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/assessments", response_model=dict)
async def list_my_assessments(
    user_id: int = Depends(get_user_from_token)
):
    """
    Lista todas as avaliações do usuário
    """
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Erro ao conectar ao banco")

    try:
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                a.assessment_id, a.status, a.started_at, a.completed_at,
                a.overall_score, a.profile_type
            FROM assessments a
            WHERE a.user_id = %s
            ORDER BY a.started_at DESC
        """, (user_id,))

        assessments = cursor.fetchall()
        cursor.close()
        conn.close()

        return {
            "success": True,
            "data": assessments,
            "total": len(assessments)
        }

    except Exception as e:
        logger.error(f"Erro ao listar avaliações: {e}")
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/assessments/{assessment_id}/answers", response_model=dict)
async def save_assessment_answers(
    assessment_id: int,
    answers: List[Dict[str, Any]] = Body(...),  # [{"question_id": 1, "score": 8, "answer_text": "..."}]
    user_id: int = Depends(get_user_from_token)
):
    """
    Salva respostas às perguntas do diagnóstico
    """
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Erro ao conectar ao banco")

    try:
        cursor = conn.cursor(dictionary=True)

        # Verificar se a avaliação pertence ao usuário
        cursor.execute("""
            SELECT a.assessment_id
            FROM assessments a
            -- REMOVED: clients table
            WHERE a.assessment_id = %s AND u.user_id = %s
        """, (assessment_id, user_id))

        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="Avaliação não encontrada")

        # Salvar cada resposta
        for answer in answers:
            cursor.execute("""
                INSERT INTO assessment_answers (assessment_id, question_id, score, answer_text)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE score = VALUES(score), answer_text = VALUES(answer_text)
            """, (
                assessment_id,
                answer['question_id'],
                answer['score'],
                answer.get('answer_text')
            ))

        conn.commit()
        cursor.close()
        conn.close()

        return {
            "success": True,
            "message": f"{len(answers)} respostas salvas com sucesso"
        }

    except HTTPException:
        if conn:
            conn.close()
        raise
    except Exception as e:
        logger.error(f"Erro ao salvar respostas: {e}")
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/assessments/{assessment_id}/complete", response_model=dict)
async def complete_assessment(
    assessment_id: int,
    user_id: int = Depends(get_user_from_token)
):
    """
    Finaliza avaliação e gera diagnóstico (calcula scores)
    """
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Erro ao conectar ao banco")

    try:
        cursor = conn.cursor(dictionary=True)

        # Verificar se pertence ao usuário
        cursor.execute("""
            SELECT a.assessment_id, u.user_id
            FROM assessments a
            -- REMOVED: clients table
            WHERE a.assessment_id = %s AND u.user_id = %s
        """, (assessment_id, user_id))

        assessment = cursor.fetchone()
        if not assessment:
            raise HTTPException(status_code=404, detail="Avaliação não encontrada")

        # Calcular scores por área
        cursor.execute("""
            SELECT
                dq.area_id,
                AVG(aa.score) as avg_score
            FROM assessment_answers aa
            JOIN diagnosis_questions dq ON aa.question_id = dq.question_id
            WHERE aa.assessment_id = %s
            GROUP BY dq.area_id
        """, (assessment_id,))

        area_scores = cursor.fetchall()

        # Salvar scores por área
        for area in area_scores:
            cursor.execute("""
                INSERT INTO assessment_area_scores (assessment_id, area_id, score)
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE score = VALUES(score)
            """, (assessment_id, area['area_id'], area['avg_score']))

        # Calcular score geral
        overall_score = sum(a['avg_score'] for a in area_scores) / len(area_scores) if area_scores else 0

        # Determinar perfil
        if overall_score >= 8:
            profile_type = "High Ticket"
        elif overall_score >= 5:
            profile_type = "Em Crescimento"
        else:
            profile_type = "Iniciante"

        # Área mais forte e mais fraca
        strongest = max(area_scores, key=lambda x: x['avg_score']) if area_scores else None
        weakest = min(area_scores, key=lambda x: x['avg_score']) if area_scores else None

        # Salvar resumo
        cursor.execute("""
            INSERT INTO assessment_summaries (
                assessment_id, overall_score, profile_type,
                strongest_area_id, weakest_area_id
            ) VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                overall_score = VALUES(overall_score),
                profile_type = VALUES(profile_type),
                strongest_area_id = VALUES(strongest_area_id),
                weakest_area_id = VALUES(weakest_area_id)
        """, (
            assessment_id,
            overall_score,
            profile_type,
            strongest['area_id'] if strongest else None,
            weakest['area_id'] if weakest else None
        ))

        # Marcar avaliação como completa
        cursor.execute("""
            UPDATE assessments
            SET status = 'completed', completed_at = NOW()
            WHERE assessment_id = %s
        """, (assessment_id,))

        conn.commit()
        cursor.close()
        conn.close()

        return {
            "success": True,
            "message": "Diagnóstico gerado com sucesso",
            "overall_score": float(overall_score),
            "profile_type": profile_type
        }

    except HTTPException:
        if conn:
            conn.close()
        raise
    except Exception as e:
        logger.error(f"Erro ao completar avaliação: {e}")
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/assessments/{assessment_id}/result", response_model=dict)
async def get_assessment_result(
    assessment_id: int,
    user_id: int = Depends(get_user_from_token)
):
    """
    Obtém resultado completo do diagnóstico.
    Admin e mentor podem ver qualquer diagnóstico.
    Mentorado só pode ver o próprio.
    """
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Erro ao conectar ao banco")

    try:
        cursor = conn.cursor(dictionary=True)

        # Verificar role do usuário
        user_role = get_user_role(user_id)

        # Admin e mentor podem ver qualquer diagnóstico
        if user_role in ['admin', 'mentor']:
            cursor.execute("""
                SELECT
                    a.assessment_id, a.status, a.started_at, a.completed_at,
                    a.overall_score, a.profile_type, a.main_insights, a.action_plan,
                    a.strongest_area, a.weakest_area,
                    u.username as client_name
                FROM assessments a
                JOIN users u ON a.user_id = u.user_id
                WHERE a.assessment_id = %s
            """, (assessment_id,))
        else:
            # Mentorado só pode ver o próprio diagnóstico
            cursor.execute("""
                SELECT
                    a.assessment_id, a.status, a.started_at, a.completed_at,
                    a.overall_score, a.profile_type, a.main_insights, a.action_plan,
                    a.strongest_area, a.weakest_area,
                    u.username as client_name
                FROM assessments a
                JOIN users u ON a.user_id = u.user_id
                WHERE a.assessment_id = %s AND a.user_id = %s
            """, (assessment_id, user_id))

        summary = cursor.fetchone()
        if not summary:
            raise HTTPException(status_code=404, detail="Diagnóstico não encontrado")

        # Obter scores por área (usando area_key)
        cursor.execute("""
            SELECT
                da.area_key, da.area_name, da.area_icon as icon,
                aas.score, aas.strengths, aas.improvements, aas.recommendations
            FROM assessment_area_scores aas
            JOIN diagnosis_areas da ON aas.area_key = da.area_key
            WHERE aas.assessment_id = %s
            ORDER BY da.order_index
        """, (assessment_id,))

        area_scores = cursor.fetchall()

        cursor.close()
        conn.close()

        return {
            "success": True,
            "data": {
                "summary": summary,
                "area_scores": area_scores
            }
        }

    except HTTPException:
        if conn:
            conn.close()
        raise
    except Exception as e:
        logger.error(f"Erro ao obter resultado: {e}")
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=str(e))


# =====================================================
# ENDPOINTS DE ADMIN (Nanda)
# =====================================================

@app.get("/api/admin/mentors", response_model=dict)
async def list_all_mentors(user_id: int = Depends(get_user_from_token)):
    """
    Lista todos os mentores do sistema (apenas admin)
    """
    # Verificar role
    user_role = get_user_role(user_id)
    if user_role != 'admin':
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas admins.")

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Erro ao conectar ao banco")

    try:
        cursor = conn.cursor(dictionary=True)
        # WORKAROUND: mentor_id removido, sem LEFT JOIN
        cursor.execute("""
            SELECT
                user_id, username, email, phone_number,
                registration_date, account_status,
                0 as total_mentorados
            FROM users
            WHERE role = 'mentor'
            ORDER BY registration_date DESC
        """)
        mentors = cursor.fetchall()
        cursor.close()
        conn.close()

        return {"success": True, "data": mentors, "total": len(mentors)}

    except Exception as e:
        logger.error(f"Erro ao listar mentores: {e}")
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/admin/mentors", response_model=dict)
async def create_or_promote_mentor(
    phone_number: str = Body(...),
    username: str = Body(None),
    user_id: int = Depends(get_user_from_token)
):
    """
    Cria novo mentor ou promove usuário existente (apenas admin).
    Busca por phone_number. Sem senha — sistema passwordless.
    """
    user_role = get_user_role(user_id)
    if user_role != 'admin':
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas admins.")

    phone = normalize_phone_number(phone_number)

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Erro ao conectar ao banco")

    try:
        cursor = conn.cursor(dictionary=True)

        # Verificar se usuário existe por phone_number
        cursor.execute("SELECT user_id, role FROM users WHERE phone_number = %s", (phone,))
        existing_user = cursor.fetchone()

        if existing_user:
            # Promover usuário existente a mentor
            cursor.execute(
                "UPDATE users SET role = 'mentor' WHERE user_id = %s",
                (existing_user['user_id'],)
            )
            conn.commit()
            new_mentor_id = existing_user['user_id']
            message = "Usuário promovido a mentor"
        else:
            # Criar novo mentor (sem senha)
            if not username:
                phone_suffix = phone[-4:]
                username = f"mentor{phone_suffix}"

            cursor.execute("""
                INSERT INTO users (username, email, password_hash, phone_number, account_status, role, verification_status)
                VALUES (%s, %s, %s, %s, 'active', 'mentor', 1)
            """, (username, "", "", phone))
            conn.commit()
            new_mentor_id = cursor.lastrowid

            # Atualizar username com ID
            final_username = f"{username}#{new_mentor_id:02d}"
            cursor.execute(
                "UPDATE users SET username = %s WHERE user_id = %s",
                (final_username, new_mentor_id)
            )
            conn.commit()
            message = "Novo mentor criado"

        cursor.close()
        conn.close()

        return {
            "success": True,
            "message": message,
            "mentor_id": new_mentor_id
        }

    except HTTPException:
        if conn:
            conn.close()
        raise
    except Exception as e:
        logger.error(f"Erro ao criar/promover mentor: {e}")
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/admin/mentors/{mentor_id}", response_model=dict)
async def delete_mentor(
    mentor_id: int,
    user_id: int = Depends(get_user_from_token)
):
    """
    Remove um mentor do sistema (apenas admin)
    Rebaixa o mentor para mentorado ou remove completamente
    """
    # Verificar role
    user_role = get_user_role(user_id)
    if user_role != 'admin':
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas admins.")

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Erro ao conectar ao banco")

    try:
        cursor = conn.cursor(dictionary=True)

        # Verificar se usuário existe e é mentor
        cursor.execute("SELECT user_id, username, role FROM users WHERE user_id = %s", (mentor_id,))
        user = cursor.fetchone()

        if not user:
            cursor.close()
            conn.close()
            raise HTTPException(status_code=404, detail="Usuário não encontrado")

        if user['role'] != 'mentor':
            cursor.close()
            conn.close()
            raise HTTPException(status_code=400, detail="Usuário não é um mentor")

        # Desvincular mentorados deste mentor
        cursor.execute("UPDATE users SET mentor_id = NULL WHERE mentor_id = %s", (mentor_id,))

        # Rebaixar para mentorado (em vez de deletar)
        cursor.execute("UPDATE users SET role = 'mentorado' WHERE user_id = %s", (mentor_id,))
        conn.commit()

        cursor.close()
        conn.close()

        return {
            "success": True,
            "message": f"Mentor '{user['username']}' rebaixado para mentorado com sucesso"
        }

    except HTTPException:
        if conn:
            conn.close()
        raise
    except Exception as e:
        logger.error(f"Erro ao remover mentor: {e}")
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/admin/mentorados", response_model=dict)
async def list_all_mentorados(
    page: int = 1,
    per_page: int = 20,
    user_id: int = Depends(get_user_from_token)
):
    """
    Lista todos os mentorados do sistema (apenas admin)
    """
    # Verificar role
    user_role = get_user_role(user_id)
    if user_role != 'admin':
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas admins.")

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Erro ao conectar ao banco")

    try:
        cursor = conn.cursor(dictionary=True)

        offset = (page - 1) * per_page

        # WORKAROUND: libsql-client 0.3.1 tem bug com LEFT JOIN
        # Fazendo query sem JOIN
        cursor.execute("""
            SELECT
                user_id as mentorado_id,
                username as mentorado_nome,
                email as mentorado_email,
                profession,
                specialty,
                current_revenue,
                desired_revenue
            FROM users
            WHERE role = 'mentorado'
            ORDER BY user_id DESC
            LIMIT %s OFFSET %s
        """, (per_page, offset))
        mentorados = cursor.fetchall()

        cursor.execute("SELECT COUNT(*) as total FROM users WHERE role = 'mentorado'")
        total_result = cursor.fetchone()
        total = total_result.get('total', 0) if total_result else 0

        cursor.close()
        conn.close()

        return {
            "success": True,
            "data": mentorados,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": (total + per_page - 1) // per_page
        }

    except Exception as e:
        logger.error(f"Erro ao listar mentorados: {e}")
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/admin/leads", response_model=dict)
async def list_all_leads(
    page: int = Query(1),
    per_page: int = Query(20),
    state: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    profession: Optional[str] = Query(None),
    user_id: int = Depends(get_user_from_token)
):
    """
    Lista todos os leads do CRM (apenas admin)
    Leads são potenciais alunos que ainda não compraram
    """
    # Debug
    logger.info(f"API /leads: search={search}, state={state}, per_page={per_page}")

    # Verificar role
    user_role = get_user_role(user_id)
    if user_role != 'admin':
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas admins.")

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Erro ao conectar ao banco")

    try:
        cursor = conn.cursor(dictionary=True)
        offset = (page - 1) * per_page

        # Query para leads (role = 'lead' OU admin_level = 5) com dados do CRM
        base_query = """
            SELECT
                u.user_id as lead_id,
                u.username as nome,
                u.email,
                u.phone_number as telefone,
                u.profession as profissao,
                u.registration_date as created_at,
                ls.current_state as estado_crm,
                ls.owner_team as time_responsavel,
                ls.state_updated_at as ultima_atualizacao,
                ls.notes as notas
            FROM users u
            LEFT JOIN crm_lead_state ls ON u.user_id = ls.lead_id
            WHERE u.role = 'lead' OR u.account_status = 'lead' OR u.admin_level = 5
        """

        params = []

        # Filtrar por estado
        if state:
            base_query += " AND ls.current_state = %s"
            params.append(state)

        # Filtrar por busca (nome ou email)
        if search:
            base_query += " AND (u.username LIKE %s OR u.email LIKE %s)"
            search_term = f"%{search}%"
            params.extend([search_term, search_term])

        # Filtrar por profissão
        if profession:
            base_query += " AND u.profession LIKE %s"
            params.append(f"%{profession}%")

        # Filtrar deletados
        base_query += " AND u.deleted_at IS NULL"

        base_query += " ORDER BY u.registration_date DESC LIMIT %s OFFSET %s"
        params.extend([per_page, offset])

        cursor.execute(base_query, tuple(params))
        leads = cursor.fetchall()

        # Parse notes JSON para cada lead
        for lead in leads:
            if lead.get('notas'):
                try:
                    import json
                    lead['notas_parsed'] = json.loads(lead['notas'])
                except:
                    pass

        # Contar total com os mesmos filtros
        count_query = """
            SELECT COUNT(*) as total FROM users u
            LEFT JOIN crm_lead_state ls ON u.user_id = ls.lead_id
            WHERE (u.role = 'lead' OR u.account_status = 'lead')
        """

        count_params = []

        if state:
            count_query += " AND ls.current_state = %s"
            count_params.append(state)

        if search:
            count_search_term = f"%{search}%"
            count_query += " AND (u.username LIKE %s OR u.email LIKE %s)"
            count_params.extend([count_search_term, count_search_term])

        if profession:
            count_query += " AND u.profession LIKE %s"
            count_params.append(f"%{profession}%")

        count_query += " AND u.deleted_at IS NULL"

        cursor.execute(count_query, tuple(count_params))
        total_result = cursor.fetchone()
        total = total_result.get('total', 0) if total_result else 0

        cursor.close()
        conn.close()

        return {
            "success": True,
            "data": leads,
            "total": total,
            "page": page,
            "per_page": per_page,
            "total_pages": (total + per_page - 1) // per_page
        }

    except Exception as e:
        logger.error(f"Erro ao listar leads: {e}")
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/admin/leads/{lead_id}", response_model=dict)
async def get_lead_details(
    lead_id: int,
    user_id: int = Depends(get_user_from_token)
):
    """
    Obtém detalhes completos de um lead incluindo eventos e dados do CRM
    """
    user_role = get_user_role(user_id)
    if user_role != 'admin':
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas admins.")

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Erro ao conectar ao banco")

    try:
        cursor = conn.cursor(dictionary=True)

        # Dados do lead
        cursor.execute("""
            SELECT
                u.user_id, u.username as nome, u.email, u.phone_number as telefone,
                u.profession as profissao, u.registration_date as created_at,
                ls.current_state, ls.owner_team, ls.state_updated_at, ls.notes
            FROM users u
            LEFT JOIN crm_lead_state ls ON u.user_id = ls.lead_id
            WHERE u.user_id = %s
        """, (lead_id,))
        lead = cursor.fetchone()

        if not lead:
            cursor.close()
            conn.close()
            raise HTTPException(status_code=404, detail="Lead não encontrado")

        # Parse notes
        if lead.get('notes'):
            try:
                import json
                lead['notes_parsed'] = json.loads(lead['notes'])
            except:
                pass

        # Eventos do lead
        cursor.execute("""
            SELECT event_id, event_type, created_at, created_by, event_data
            FROM crm_lead_events
            WHERE lead_id = %s
            ORDER BY created_at DESC
            LIMIT 20
        """, (lead_id,))
        events = cursor.fetchall()

        for event in events:
            if event.get('event_data'):
                try:
                    import json
                    event['event_data'] = json.loads(event['event_data'])
                except:
                    pass

        lead['events'] = events

        cursor.close()
        conn.close()

        return {"success": True, "data": lead}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao obter detalhes do lead: {e}")
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/admin/leads/{lead_id}/events", response_model=dict)
async def get_lead_events(
    lead_id: int,
    user_id: int = Depends(get_user_from_token)
):
    """
    Obtém eventos/timeline de um lead
    """
    user_role = get_user_role(user_id)
    if user_role != 'admin':
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas admins.")

    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT event_id, event_type, created_at, created_by, event_data
            FROM crm_lead_events
            WHERE lead_id = %s
            ORDER BY created_at DESC
            LIMIT 50
        """, (lead_id,))
        events = cursor.fetchall()

        for event in events:
            if event.get('event_data'):
                try:
                    import json
                    event['event_data'] = json.loads(event['event_data'])
                except:
                    pass

        cursor.close()
        conn.close()
        return {"events": events}

    except Exception as e:
        logger.error(f"Erro ao obter eventos do lead: {e}")
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/admin/leads/{lead_id}/events", response_model=dict)
async def add_lead_event(
    lead_id: int,
    event_type: str = Body(...),
    event_data: dict = Body(None),
    user_id: int = Depends(get_user_from_token)
):
    """
    Adiciona evento na timeline de um lead
    """
    user_role = get_user_role(user_id)
    if user_role != 'admin':
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas admins.")

    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        import json

        cursor.execute("""
            INSERT INTO crm_lead_events (lead_id, event_type, event_data, created_by)
            VALUES (%s, %s, %s, %s)
        """, (lead_id, event_type, json.dumps(event_data) if event_data else None, user_id))

        conn.commit()
        event_id = cursor.lastrowid

        cursor.close()
        conn.close()
        return {"success": True, "event_id": event_id}

    except Exception as e:
        logger.error(f"Erro ao adicionar evento: {e}")
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=str(e))


@app.patch("/api/admin/leads/{lead_id}/state", response_model=dict)
async def update_lead_state(
    lead_id: int,
    state: str = Body(..., embed=True),
    user_id: int = Depends(get_user_from_token)
):
    """
    Atualiza o estado de um lead no funil de vendas
    """
    user_role = get_user_role(user_id)
    if user_role != 'admin':
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas admins.")

    valid_states = ['novo', 'diagnostico_pendente', 'diagnostico_agendado',
                    'em_atendimento', 'proposta_enviada', 'produto_vendido', 'perdido']
    if state not in valid_states:
        raise HTTPException(status_code=400, detail=f"Estado invalido. Valores aceitos: {valid_states}")

    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)

        # Buscar estado atual
        cursor.execute("SELECT current_state FROM crm_lead_state WHERE lead_id = %s", (lead_id,))
        row = cursor.fetchone()
        old_state = row['current_state'] if row else 'novo'

        # Atualizar estado
        cursor.execute("""
            UPDATE crm_lead_state
            SET current_state = %s, updated_at = NOW()
            WHERE lead_id = %s
        """, (state, lead_id))

        if cursor.rowcount == 0:
            # Criar registro se nao existe
            cursor.execute("""
                INSERT INTO crm_lead_state (lead_id, current_state)
                VALUES (%s, %s)
            """, (lead_id, state))

        # Registrar evento de mudanca de estado
        import json
        event_data = json.dumps({"old_state": old_state, "new_state": state})
        cursor.execute("""
            INSERT INTO crm_lead_events (lead_id, event_type, event_data, created_by)
            VALUES (%s, 'estado_alterado', %s, %s)
        """, (lead_id, event_data, user_id))

        conn.commit()
        cursor.close()
        conn.close()
        return {"success": True, "old_state": old_state, "new_state": state}

    except Exception as e:
        logger.error(f"Erro ao atualizar estado do lead: {e}")
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/admin/leads/{lead_id}/convert", response_model=dict)
async def convert_lead_to_mentorado(
    lead_id: int,
    user_id: int = Depends(get_user_from_token)
):
    """
    Converte um lead para mentorado (upgrade de nivel)
    """
    user_role = get_user_role(user_id)
    if user_role != 'admin':
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas admins.")

    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)

        # Verificar se e lead (admin_level = 5)
        cursor.execute("SELECT admin_level, nome FROM users WHERE user_id = %s", (lead_id,))
        user = cursor.fetchone()

        if not user:
            raise HTTPException(status_code=404, detail="Lead nao encontrado")

        if user['admin_level'] != 5:
            raise HTTPException(status_code=400, detail="Usuario nao e um lead")

        # Atualizar para mentorado (admin_level = 4)
        cursor.execute("""
            UPDATE users SET admin_level = 4 WHERE user_id = %s
        """, (lead_id,))

        # Atualizar estado no CRM
        cursor.execute("""
            UPDATE crm_lead_state
            SET current_state = 'produto_vendido', updated_at = NOW()
            WHERE lead_id = %s
        """, (lead_id,))

        # Registrar evento
        import json
        event_data = json.dumps({"converted_by": user_id, "old_role": "lead", "new_role": "mentorado"})
        cursor.execute("""
            INSERT INTO crm_lead_events (lead_id, event_type, event_data, created_by)
            VALUES (%s, 'convertido', %s, %s)
        """, (lead_id, event_data, user_id))

        conn.commit()
        cursor.close()
        conn.close()

        return {"success": True, "user_id": lead_id, "message": f"Lead {user['nome']} convertido para mentorado"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao converter lead: {e}")
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/admin/assign-mentor", response_model=dict)
async def assign_mentor_to_mentorado(
    mentorado_id: int = Body(...),
    mentor_id: int = Body(...),
    user_id: int = Depends(get_user_from_token)
):
    """
    Vincula um mentorado a um mentor (apenas admin)
    """
    # Verificar role
    user_role = get_user_role(user_id)
    if user_role != 'admin':
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas admins.")

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Erro ao conectar ao banco")

    try:
        cursor = conn.cursor(dictionary=True)

        # Verificar se mentor existe
        cursor.execute("SELECT role FROM users WHERE user_id = %s", (mentor_id,))
        mentor = cursor.fetchone()
        if not mentor or mentor['role'] != 'mentor':
            raise HTTPException(status_code=404, detail="Mentor não encontrado")

        # Verificar se mentorado existe
        cursor.execute("SELECT role FROM users WHERE user_id = %s", (mentorado_id,))
        mentorado = cursor.fetchone()
        if not mentorado or mentorado['role'] != 'mentorado':
            raise HTTPException(status_code=404, detail="Mentorado não encontrado")

        # Atualizar vinculação
        cursor.execute(
            "UPDATE users SET mentor_id = %s WHERE user_id = %s",
            (mentor_id, mentorado_id)
        )
        conn.commit()

        cursor.close()
        conn.close()

        return {
            "success": True,
            "message": "Mentorado vinculado ao mentor com sucesso"
        }

    except HTTPException:
        if conn:
            conn.close()
        raise
    except Exception as e:
        logger.error(f"Erro ao vincular mentorado: {e}")
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=str(e))


# generate_temp_password e reset-user-password removidos — sistema passwordless

@app.patch("/api/admin/users/{target_user_id}/level", response_model=dict)
async def admin_update_user_level(
    target_user_id: int,
    data: dict,
    user_id: int = Depends(get_user_from_token)
):
    """
    Atualiza o admin_level de um usuário (apenas admin)
    Níveis: 0=Proprietário, 1=Admin, 2=Mentor Senior, 3=Mentor, 4=Mentorado, 5=Lead
    """
    # Verificar role do solicitante
    user_role = get_user_role(user_id)
    if user_role != 'admin':
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas admins.")

    new_level = data.get('admin_level')
    if new_level is None or not isinstance(new_level, int) or new_level < 0:
        raise HTTPException(status_code=400, detail="admin_level inválido. Use valores >= 0.")

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Erro ao conectar ao banco")

    try:
        cursor = conn.cursor(dictionary=True)

        # Verificar se usuário alvo existe
        cursor.execute(
            "SELECT user_id, username, email, admin_level FROM users WHERE user_id = ?",
            (target_user_id,)
        )
        target_user = cursor.fetchone()

        if not target_user:
            raise HTTPException(status_code=404, detail="Usuário não encontrado")

        old_level = target_user.get('admin_level')

        # Determinar o role baseado no admin_level (dinâmico)
        if new_level <= 1:
            new_role = 'admin'
        elif new_level <= 3:
            new_role = 'mentor'
        elif new_level == 4:
            new_role = 'mentorado'
        elif new_level == 5:
            new_role = 'lead'
        else:  # Níveis extras (6+) - usar role genérico
            new_role = 'user'

        # Atualizar admin_level e role no banco
        cursor.execute(
            "UPDATE users SET admin_level = ?, role = ?, account_status = ? WHERE user_id = ?",
            (new_level, new_role, new_role, target_user_id)
        )
        conn.commit()

        # Log no console
        logger.info(f"Admin {user_id} alterou admin_level de {target_user_id} ({target_user['email']}) de {old_level} para {new_level} (role: {new_role})")

        cursor.close()
        conn.close()

        return {
            "success": True,
            "message": f"Nível do usuário {target_user['email']} alterado para {new_level}"
        }

    except HTTPException:
        if conn:
            conn.close()
        raise
    except Exception as e:
        import traceback
        logger.error(f"Erro ao atualizar nível: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=str(e))


# ==============================================================================
# ENDPOINTS DE NÍVEIS - Gestão Unificada de Usuários por Nível
# ==============================================================================

# Fallback de níveis (usado se tabela admin_levels estiver vazia)
LEVEL_LABELS_FALLBACK = {
    0: "Proprietário",
    1: "Admin",
    2: "Mentor Senior",
    3: "Mentor",
    4: "Mentorado",
    5: "Lead"
}

def get_levels_from_db(conn) -> list:
    """Retorna níveis configurados no banco de dados."""
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT level, name, description, permissions, can_manage_levels, is_active
            FROM admin_levels
            WHERE tenant_id = 'default' AND is_active = 1
            ORDER BY level
        """)
        levels = cursor.fetchall()
        cursor.close()
        return levels if levels else []
    except Exception as e:
        logger.warning(f"Erro ao buscar níveis do banco: {e}")
        return []


@app.get("/api/admin/levels/config", response_model=dict)
async def get_levels_config(
    user_id: int = Depends(get_user_from_token)
):
    """
    Retorna configuração de níveis do banco de dados.
    Usado pelo frontend para renderizar dinamicamente os níveis.
    """
    user_role = get_user_role(user_id)
    if user_role != 'admin':
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas admins.")

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Erro ao conectar ao banco")

    try:
        levels = get_levels_from_db(conn)
        conn.close()

        # Se não há níveis no banco, usar fallback
        if not levels:
            levels = [
                {"level": k, "name": v, "description": None}
                for k, v in LEVEL_LABELS_FALLBACK.items()
            ]

        # Adicionar cores e ícones baseados no nível
        level_colors = {
            0: {"color": "text-purple-700", "bgColor": "bg-purple-100", "icon": "👑"},
            1: {"color": "text-indigo-700", "bgColor": "bg-indigo-100", "icon": "🛡️"},
            2: {"color": "text-blue-700", "bgColor": "bg-blue-100", "icon": "⭐"},
            3: {"color": "text-teal-700", "bgColor": "bg-teal-100", "icon": "✅"},
            4: {"color": "text-emerald-700", "bgColor": "bg-emerald-100", "icon": "👤"},
            5: {"color": "text-amber-700", "bgColor": "bg-amber-100", "icon": "👥"},
        }
        # Cor padrão para níveis extras (6+)
        default_color = {"color": "text-gray-700", "bgColor": "bg-gray-100", "icon": "🔷"}

        configs = []
        for lvl in levels:
            level_num = lvl["level"]
            colors = level_colors.get(level_num, default_color)
            configs.append({
                "level": level_num,
                "label": lvl["name"],
                "description": lvl.get("description") or f"Nível {level_num}",
                "color": colors["color"],
                "bgColor": colors["bgColor"],
                "icon": colors["icon"],
            })

        return {
            "status": "success",
            "levels": configs
        }

    except Exception as e:
        logger.error(f"Erro ao buscar config de níveis: {e}")
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/admin/levels/count", response_model=dict)
async def get_users_count_by_level(
    user_id: int = Depends(get_user_from_token)
):
    """
    Retorna contagem de usuários por nível (dinâmico do banco)
    Usado no dashboard de níveis para exibir cards com contadores
    """
    user_role = get_user_role(user_id)
    if user_role != 'admin':
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas admins.")

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Erro ao conectar ao banco")

    try:
        # Buscar níveis configurados no banco
        db_levels = get_levels_from_db(conn)
        level_map = {lvl["level"]: lvl["name"] for lvl in db_levels}

        # Se não há níveis no banco, usar fallback
        if not level_map:
            level_map = LEVEL_LABELS_FALLBACK.copy()

        cursor = conn.cursor(dictionary=True)

        # Contar usuários por admin_level
        cursor.execute("""
            SELECT
                COALESCE(admin_level,
                    CASE
                        WHEN role = 'admin' THEN 1
                        WHEN role = 'mentor' THEN 3
                        WHEN role = 'mentorado' THEN 4
                        WHEN role = 'lead' THEN 5
                        ELSE 4
                    END
                ) as level,
                COUNT(*) as count
            FROM users
            WHERE account_status != 'deleted'
            GROUP BY level
            ORDER BY level
        """)

        results = cursor.fetchall()
        results_map = {r['level']: r['count'] for r in results}

        # Montar resposta com todos os níveis configurados (mesmo os com 0)
        counts = []
        for level_num in sorted(level_map.keys()):
            counts.append({
                "level": level_num,
                "label": level_map.get(level_num, f"Nível {level_num}"),
                "count": results_map.get(level_num, 0)
            })

        cursor.close()
        conn.close()

        return {
            "status": "success",
            "counts": counts,
            "total": sum(c['count'] for c in counts)
        }

    except Exception as e:
        import traceback
        logger.error(f"Erro ao contar usuários por nível: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/admin/levels/{level}/users", response_model=dict)
async def get_users_by_level(
    level: int,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    search: Optional[str] = Query(None),
    user_id: int = Depends(get_user_from_token)
):
    """
    Lista usuários de um nível específico com paginação e busca
    Agnóstico para qualquer nível (0-5)
    """
    user_role = get_user_role(user_id)
    if user_role != 'admin':
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas admins.")

    if level < 0:
        raise HTTPException(status_code=400, detail="Nível inválido. Use valores >= 0.")

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Erro ao conectar ao banco")

    try:
        cursor = conn.cursor(dictionary=True)
        offset = (page - 1) * per_page

        # Query base - busca por admin_level OU por role correspondente
        base_where = """
            WHERE account_status != 'deleted'
            AND (
                admin_level = ?
                OR (admin_level IS NULL AND (
                    (? = 1 AND role = 'admin')
                    OR (? = 3 AND role = 'mentor')
                    OR (? = 4 AND role = 'mentorado')
                    OR (? = 5 AND role = 'lead')
                ))
            )
        """
        params = [level, level, level, level, level]

        # Adicionar busca se fornecida
        if search:
            base_where += " AND (username LIKE ? OR email LIKE ? OR phone_number LIKE ?)"
            search_pattern = f"%{search}%"
            params.extend([search_pattern, search_pattern, search_pattern])

        # Contar total para paginação
        count_query = f"SELECT COUNT(*) as total FROM users {base_where}"
        cursor.execute(count_query, params)
        total = cursor.fetchone()['total']

        # Buscar usuários com paginação
        query = f"""
            SELECT
                user_id,
                username,
                email,
                phone_number,
                profile_image_url,
                registration_date,
                account_status,
                verification_status,
                role,
                admin_level
            FROM users
            {base_where}
            ORDER BY registration_date DESC
            LIMIT ? OFFSET ?
        """
        params.extend([per_page, offset])
        cursor.execute(query, params)

        users = cursor.fetchall()

        # Formatar datas e adicionar campos extras
        for user in users:
            if user.get('registration_date'):
                if isinstance(user['registration_date'], str):
                    user['registration_date'] = user['registration_date']
                else:
                    user['registration_date'] = user['registration_date'].isoformat()

            # Garantir que admin_level está preenchido
            if user.get('admin_level') is None:
                user['admin_level'] = level

        cursor.close()
        conn.close()

        return {
            "status": "success",
            "level": level,
            "level_label": LEVEL_LABELS_FALLBACK.get(level, f"Nível {level}"),
            "users": users,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "pages": (total + per_page - 1) // per_page
            }
        }

    except Exception as e:
        import traceback
        logger.error(f"Erro ao listar usuários por nível: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/admin/levels/{level}/users/{target_user_id}", response_model=dict)
async def get_user_detail_by_level(
    level: int,
    target_user_id: int,
    user_id: int = Depends(get_user_from_token)
):
    """
    Obtém detalhes de um usuário específico com dados adicionais baseados no nível
    - Níveis 0-3 (Admin/Mentor): dados básicos + permissões
    - Nível 4 (Mentorado): dados + histórico de chats + diagnósticos
    - Nível 5 (Lead): dados + timeline CRM + estado do funil
    """
    user_role = get_user_role(user_id)
    if user_role != 'admin':
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas admins.")

    if level < 0:
        raise HTTPException(status_code=400, detail="Nível inválido. Use valores >= 0.")

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Erro ao conectar ao banco")

    try:
        cursor = conn.cursor(dictionary=True)

        # Buscar usuário
        cursor.execute("""
            SELECT
                user_id, username, email, phone_number, profile_image_url,
                registration_date, account_status, verification_status,
                role, admin_level
            FROM users
            WHERE user_id = ?
        """, (target_user_id,))

        user = cursor.fetchone()

        if not user:
            raise HTTPException(status_code=404, detail="Usuário não encontrado")

        # Formatar data
        if user.get('registration_date'):
            if not isinstance(user['registration_date'], str):
                user['registration_date'] = user['registration_date'].isoformat()

        # Dados extras baseados no nível
        extra_data = {}

        if level == 5:  # Lead - dados de CRM
            # Buscar estado do funil
            try:
                cursor.execute("""
                    SELECT current_state as state, notes
                    FROM crm_lead_state WHERE lead_id = ?
                """, (target_user_id,))
                crm_data = cursor.fetchone()
                if crm_data:
                    extra_data['crm'] = crm_data
            except Exception as e:
                logger.warning(f"Erro ao buscar dados CRM: {e}")
                extra_data['crm'] = None

            # Buscar eventos/timeline
            try:
                cursor.execute("""
                    SELECT event_type, event_data, created_at
                    FROM crm_lead_events
                    WHERE lead_id = ?
                    ORDER BY created_at DESC
                    LIMIT 20
                """, (target_user_id,))
                events = cursor.fetchall()
                for evt in events:
                    if evt.get('created_at') and not isinstance(evt['created_at'], str):
                        evt['created_at'] = evt['created_at'].isoformat()
                    if evt.get('event_data') and isinstance(evt['event_data'], str):
                        try:
                            evt['event_data'] = json.loads(evt['event_data'])
                        except:
                            pass
                extra_data['events'] = events
            except Exception as e:
                logger.warning(f"Erro ao buscar eventos CRM: {e}")
                extra_data['events'] = []

        elif level == 4:  # Mentorado - chats e diagnósticos
            try:
                # Contar sessões de chat
                cursor.execute("""
                    SELECT COUNT(*) as count FROM chat_sessions WHERE user_id = ?
                """, (target_user_id,))
                chat_count = cursor.fetchone()
                extra_data['chat_count'] = chat_count['count'] if chat_count else 0

                # Buscar últimas sessões
                cursor.execute("""
                    SELECT session_id, session_type, created_at, updated_at, status
                    FROM chat_sessions
                    WHERE user_id = ?
                    ORDER BY updated_at DESC
                    LIMIT 5
                """, (target_user_id,))
                sessions = cursor.fetchall()
                for s in sessions:
                    for field in ['created_at', 'updated_at']:
                        if s.get(field) and not isinstance(s[field], str):
                            s[field] = s[field].isoformat()
                extra_data['recent_sessions'] = sessions

                # Contar diagnósticos
                cursor.execute("""
                    SELECT COUNT(*) as count FROM chat_sessions
                    WHERE user_id = ? AND session_type = 'diagnostico'
                """, (target_user_id,))
                diag_count = cursor.fetchone()
                extra_data['diagnostico_count'] = diag_count['count'] if diag_count else 0
            except Exception as e:
                logger.warning(f"Erro ao buscar dados de sessões: {e}")
                extra_data['chat_count'] = 0
                extra_data['recent_sessions'] = []
                extra_data['diagnostico_count'] = 0

        elif level <= 3:  # Admin/Mentor - permissões
            extra_data['permissions'] = {
                'can_manage_users': level <= 1,
                'can_view_all_data': level <= 1,
                'can_manage_mentors': level <= 1,
                'can_view_mentorados': level <= 3,
                'can_chat': True
            }

        cursor.close()
        conn.close()

        return {
            "status": "success",
            "level": level,
            "level_label": LEVEL_LABELS_FALLBACK.get(level, f"Nível {level}"),
            "user": user,
            "extra": extra_data
        }

    except HTTPException:
        if conn:
            conn.close()
        raise
    except Exception as e:
        import traceback
        logger.error(f"Erro ao buscar detalhes do usuário: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/admin/mentorados/{mentorado_id}/details", response_model=dict)
async def get_mentorado_details(
    mentorado_id: int,
    user_id: int = Depends(get_user_from_token)
):
    """
    Obtém detalhes completos de um mentorado incluindo chats e diagnósticos (apenas admin)
    """
    # Verificar role
    user_role = get_user_role(user_id)
    if user_role != 'admin':
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas admins.")

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Erro ao conectar ao banco")

    try:
        cursor = conn.cursor(dictionary=True)

        # Buscar dados do mentorado (sem JOIN - workaround libsql-client bug)
        cursor.execute("""
            SELECT user_id, username, email, registration_date as created_at,
                   profession, specialty, current_revenue, desired_revenue, admin_level
            FROM users
            WHERE user_id = %s AND role = 'mentorado'
        """, (mentorado_id,))
        mentorado = cursor.fetchone()

        if not mentorado:
            cursor.close()
            conn.close()
            raise HTTPException(status_code=404, detail="Mentorado não encontrado")

        # Buscar sessões de chat
        cursor.execute("""
            SELECT cs.session_id, cs.title, cs.created_at,
                   (SELECT COUNT(*) FROM chat_messages WHERE session_id = cs.session_id) as message_count
            FROM chat_sessions cs
            WHERE cs.user_id = %s
            ORDER BY cs.created_at DESC
        """, (mentorado_id,))
        chat_sessions = cursor.fetchall()

        # Buscar assessments (clients removido)
        cursor.execute("""
            SELECT a.assessment_id, a.started_at as created_at, a.status,
                   a.overall_score, a.profile_type
            FROM assessments a
            WHERE a.user_id = %s
            ORDER BY a.started_at DESC
        """, (mentorado_id,))
        assessments = cursor.fetchall()

        cursor.close()
        conn.close()

        return {
            "success": True,
            "mentorado": mentorado,
            "chat_sessions": chat_sessions,
            "assessments": assessments
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao buscar detalhes do mentorado: {e}")
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/admin/mentorados/{mentorado_id}/revert-to-lead", response_model=dict)
async def revert_mentorado_to_lead(
    mentorado_id: int,
    user_id: int = Depends(get_user_from_token)
):
    """
    Reverte um mentorado para lead (apenas admin)
    """
    user_role = get_user_role(user_id)
    if user_role != 'admin':
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas admins.")

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Erro ao conectar ao banco")

    try:
        cursor = conn.cursor(dictionary=True)

        # Verificar se é mentorado
        cursor.execute("SELECT role, username, email FROM users WHERE user_id = %s", (mentorado_id,))
        user = cursor.fetchone()

        if not user:
            raise HTTPException(status_code=404, detail="Usuário não encontrado")

        if user['role'] != 'mentorado':
            raise HTTPException(status_code=400, detail=f"Usuário é {user['role']}, não é mentorado")

        # Reverter para lead
        cursor.execute("""
            UPDATE users
            SET role = 'lead', account_status = 'lead'
            WHERE user_id = %s
        """, (mentorado_id,))

        # Criar/atualizar estado CRM
        cursor.execute("""
            INSERT INTO crm_lead_state (lead_id, current_state, owner_team)
            VALUES (%s, 'novo', 'marketing')
            ON CONFLICT(lead_id) DO UPDATE SET
                current_state = 'novo',
                state_updated_at = datetime('now')
        """, (mentorado_id,))

        conn.commit()
        cursor.close()
        conn.close()

        logger.info(f"✅ Mentorado {mentorado_id} revertido para lead por admin {user_id}")

        return {
            "success": True,
            "message": f"Mentorado '{user['username']}' revertido para lead",
            "user_id": mentorado_id
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao reverter: {e}")
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/admin/chat/{session_id}/messages", response_model=dict)
async def get_chat_messages_admin(
    session_id: str,
    user_id: int = Depends(get_user_from_token)
):
    """
    Obtém mensagens de uma sessão de chat (apenas admin)
    """
    # Verificar role
    user_role = get_user_role(user_id)
    if user_role != 'admin':
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas admins.")

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Erro ao conectar ao banco")

    try:
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT role, content, created_at
            FROM chat_messages
            WHERE session_id = %s
            ORDER BY message_id ASC
        """, (session_id,))
        messages = cursor.fetchall()

        cursor.close()
        conn.close()

        return {
            "success": True,
            "messages": messages
        }

    except Exception as e:
        logger.error(f"Erro ao buscar mensagens: {e}")
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/admin/assessments/{assessment_id}/details", response_model=dict)
async def get_assessment_details_admin(
    assessment_id: int,
    user_id: int = Depends(get_user_from_token)
):
    """
    Obtém detalhes completos de um diagnóstico/assessment (apenas admin)
    """
    # Verificar role
    user_role = get_user_role(user_id)
    if user_role != 'admin':
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas admins.")

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Erro ao conectar ao banco")

    try:
        cursor = conn.cursor(dictionary=True)

        # Buscar assessment (dados estão na tabela assessments diretamente)
        cursor.execute("""
            SELECT a.assessment_id, a.started_at as created_at, a.completed_at, a.status,
                   a.overall_score, a.profile_type, a.main_insights, a.action_plan,
                   a.strongest_area, a.weakest_area
            FROM assessments a
            WHERE a.assessment_id = %s
        """, (assessment_id,))
        assessment = cursor.fetchone()

        if not assessment:
            cursor.close()
            conn.close()
            raise HTTPException(status_code=404, detail="Diagnóstico não encontrado")

        # Buscar nomes das áreas forte/fraca
        strongest_area_name = assessment.get('strongest_area')
        weakest_area_name = assessment.get('weakest_area')

        if strongest_area_name:
            cursor.execute("SELECT area_name FROM diagnosis_areas WHERE area_key = %s", (strongest_area_name,))
            row = cursor.fetchone()
            if row:
                strongest_area_name = row['area_name']

        if weakest_area_name:
            cursor.execute("SELECT area_name FROM diagnosis_areas WHERE area_key = %s", (weakest_area_name,))
            row = cursor.fetchone()
            if row:
                weakest_area_name = row['area_name']

        assessment['strongest_area'] = strongest_area_name
        assessment['weakest_area'] = weakest_area_name

        # Buscar scores por área (usando area_key)
        cursor.execute("""
            SELECT da.area_name, aas.area_key, aas.score,
                   aas.strengths, aas.improvements, aas.recommendations
            FROM assessment_area_scores aas
            JOIN diagnosis_areas da ON aas.area_key = da.area_key
            WHERE aas.assessment_id = %s
            ORDER BY da.order_index
        """, (assessment_id,))
        area_scores = cursor.fetchall()

        cursor.close()
        conn.close()

        return {
            "success": True,
            "assessment_id": assessment['assessment_id'],
            "created_at": assessment['created_at'],
            "completed_at": assessment['completed_at'],
            "status": assessment['status'],
            "overall_score": assessment['overall_score'],
            "profile_type": assessment['profile_type'],
            "strongest_area": assessment['strongest_area'],
            "weakest_area": assessment['weakest_area'],
            "main_insights": assessment['main_insights'],
            "action_plan": assessment['action_plan'],
            "area_scores": area_scores
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao buscar detalhes do assessment: {e}")
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/admin/mentorados/{mentorado_id}", response_model=dict)
async def delete_mentorado(
    mentorado_id: int,
    user_id: int = Depends(get_user_from_token)
):
    """
    Remove um mentorado do sistema (apenas admin)
    """
    # Verificar role
    user_role = get_user_role(user_id)
    if user_role != 'admin':
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas admins.")

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Erro ao conectar ao banco")

    try:
        cursor = conn.cursor(dictionary=True)

        # Verificar se usuário existe e é mentorado
        cursor.execute("SELECT user_id, username, role FROM users WHERE user_id = %s", (mentorado_id,))
        user = cursor.fetchone()

        if not user:
            cursor.close()
            conn.close()
            raise HTTPException(status_code=404, detail="Usuário não encontrado")

        if user['role'] != 'mentorado':
            cursor.close()
            conn.close()
            raise HTTPException(status_code=400, detail="Usuário não é um mentorado")

        # Deletar dados relacionados primeiro (para evitar problemas de FK)
        # Chat
        cursor.execute("DELETE FROM chat_messages WHERE session_id IN (SELECT session_id FROM chat_sessions WHERE user_id = %s)", (mentorado_id,))
        cursor.execute("DELETE FROM chat_sessions WHERE user_id = %s", (mentorado_id,))

        # Assessments (usa client_id, não user_id)
        cursor.execute("DELETE FROM assessment_answers WHERE assessment_id IN (SELECT assessment_id FROM assessments WHERE client_id IN (SELECT client_id FROM clients WHERE user_id = %s))", (mentorado_id,))
        cursor.execute("DELETE FROM assessment_area_scores WHERE assessment_id IN (SELECT assessment_id FROM assessments WHERE client_id IN (SELECT client_id FROM clients WHERE user_id = %s))", (mentorado_id,))
        cursor.execute("DELETE FROM assessment_summaries WHERE assessment_id IN (SELECT assessment_id FROM assessments WHERE client_id IN (SELECT client_id FROM clients WHERE user_id = %s))", (mentorado_id,))
        cursor.execute("DELETE FROM assessments WHERE client_id IN (SELECT client_id FROM clients WHERE user_id = %s)", (mentorado_id,))

        # Client reports e clients
        cursor.execute("DELETE FROM client_reports WHERE client_id IN (SELECT client_id FROM clients WHERE user_id = %s)", (mentorado_id,))
        cursor.execute("DELETE FROM clients WHERE user_id = %s", (mentorado_id,))

        # Tokens
        cursor.execute("DELETE FROM refresh_tokens WHERE user_id = %s", (mentorado_id,))

        # Deletar o usuário
        cursor.execute("DELETE FROM users WHERE user_id = %s", (mentorado_id,))
        conn.commit()

        cursor.close()
        conn.close()

        return {
            "success": True,
            "message": f"Mentorado '{user['username']}' removido com sucesso"
        }

    except HTTPException:
        if conn:
            conn.close()
        raise
    except Exception as e:
        logger.error(f"Erro ao remover mentorado: {e}")
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/admin/users/{target_user_id}", response_model=dict)
async def delete_user_by_admin(
    target_user_id: int,
    user_id: int = Depends(get_user_from_token)
):
    """
    Remove um usuário do sistema (apenas super admin - nível 0)

    Super admin pode deletar qualquer usuário, exceto a si mesmo.
    """
    # Verificar se é super admin (nível 0)
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Erro ao conectar ao banco")

    try:
        cursor = conn.cursor(dictionary=True)

        # Verificar nível do admin que está fazendo a operação
        cursor.execute("SELECT admin_level FROM users WHERE user_id = %s", (user_id,))
        admin = cursor.fetchone()

        if not admin or admin.get('admin_level') != 0:
            cursor.close()
            conn.close()
            raise HTTPException(status_code=403, detail="Apenas Super Admin (nível 0) pode remover usuários")

        # Não pode deletar a si mesmo
        if user_id == target_user_id:
            cursor.close()
            conn.close()
            raise HTTPException(status_code=400, detail="Você não pode remover sua própria conta por aqui")

        # Verificar se usuário alvo existe
        cursor.execute("SELECT user_id, username, email FROM users WHERE user_id = %s", (target_user_id,))
        target_user = cursor.fetchone()

        if not target_user:
            cursor.close()
            conn.close()
            raise HTTPException(status_code=404, detail="Usuário não encontrado")

        # Log de auditoria
        logger.warning(
            f"ADMIN DELETE: admin_user_id={user_id} deleting target_user_id={target_user_id}, "
            f"username={target_user['username']}, email={target_user['email']}"
        )

        # Deletar dados relacionados (ignorar erros de tabelas inexistentes)
        delete_queries = [
            "DELETE FROM chat_messages WHERE session_id IN (SELECT session_id FROM chat_sessions WHERE user_id = %s)",
            "DELETE FROM chat_sessions WHERE user_id = %s",
            "DELETE FROM assessment_answers WHERE assessment_id IN (SELECT assessment_id FROM assessments WHERE client_id IN (SELECT client_id FROM clients WHERE user_id = %s))",
            "DELETE FROM assessment_area_scores WHERE assessment_id IN (SELECT assessment_id FROM assessments WHERE client_id IN (SELECT client_id FROM clients WHERE user_id = %s))",
            "DELETE FROM assessment_summaries WHERE assessment_id IN (SELECT assessment_id FROM assessments WHERE client_id IN (SELECT client_id FROM clients WHERE user_id = %s))",
            "DELETE FROM assessments WHERE client_id IN (SELECT client_id FROM clients WHERE user_id = %s)",
            "DELETE FROM client_reports WHERE client_id IN (SELECT client_id FROM clients WHERE user_id = %s)",
            "DELETE FROM clients WHERE user_id = %s",
            "DELETE FROM refresh_tokens WHERE user_id = %s",
        ]
        for query in delete_queries:
            try:
                cursor.execute(query, (target_user_id,))
            except Exception as e:
                logger.debug(f"Ignorando erro ao deletar (tabela pode não existir): {e}")

        # Deletar o usuário
        cursor.execute("DELETE FROM users WHERE user_id = %s", (target_user_id,))
        conn.commit()

        cursor.close()
        conn.close()

        logger.info(f"ADMIN DELETE: User {target_user_id} ({target_user['username']}) deleted by admin {user_id}")

        return {
            "success": True,
            "message": f"Usuário '{target_user['username']}' removido com sucesso"
        }

    except HTTPException:
        if conn:
            conn.close()
        raise
    except Exception as e:
        logger.error(f"Erro ao remover usuário: {e}")
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/admin/stats", response_model=dict)
async def get_admin_statistics(user_id: int = Depends(get_user_from_token)):
    """
    Estatísticas globais do sistema (apenas admin)
    """
    # Verificar role
    user_role = get_user_role(user_id)
    if user_role != 'admin':
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas admins.")

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Erro ao conectar ao banco")

    try:
        cursor = conn.cursor(dictionary=True)

        # Contagens básicas
        cursor.execute("SELECT COUNT(*) as total FROM users WHERE role = 'mentor'")
        total_mentors = cursor.fetchone()['total']

        cursor.execute("SELECT COUNT(*) as total FROM users WHERE role = 'mentorado'")
        total_mentorados = cursor.fetchone()['total']

        cursor.execute("SELECT COUNT(*) as total FROM assessments WHERE status = 'completed'")
        total_diagnosticos = cursor.fetchone()['total']

        cursor.execute("SELECT AVG(overall_score) as media FROM assessment_summaries")
        media_score = cursor.fetchone()['media'] or 0

        # Diagnósticos este mês (SQLite syntax)
        cursor.execute("""
            SELECT COUNT(*) as total FROM assessments
            WHERE status = 'completed'
            AND strftime('%m', started_at) = strftime('%m', 'now')
            AND strftime('%Y', started_at) = strftime('%Y', 'now')
        """)
        diagnosticos_este_mes = cursor.fetchone()['total']

        # Top mentores - DESCONTINUADO (mentor_id removido)
        # Retornando lista vazia por enquanto
        top_mentores = []

        cursor.close()
        conn.close()

        return {
            "success": True,
            "data": {
                "total_mentors": total_mentors,
                "total_mentorados": total_mentorados,
                "total_diagnosticos": total_diagnosticos,
                "diagnosticos_este_mes": diagnosticos_este_mes,
                "media_score_geral": float(media_score) if media_score else 0,
                "top_mentores": top_mentores
            }
        }

    except Exception as e:
        logger.error(f"Erro ao obter estatísticas: {e}")
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/admin/diagnoses", response_model=dict)
async def list_all_diagnoses(
    page: int = 1,
    limit: int = 20,
    user_id: int = Depends(get_user_from_token)
):
    """
    Lista todos os diagnósticos (apenas admin)
    """
    # Verificar role
    user_role = get_user_role(user_id)
    if user_role != 'admin':
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas admins.")

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Erro ao conectar ao banco")

    try:
        cursor = conn.cursor(dictionary=True)
        offset = (page - 1) * limit

        # Buscar diagnósticos com info do cliente e mentor
        cursor.execute("""
            SELECT
                a.assessment_id,
                a.user_id,
                a.status,
                a.completed_at,
                a.started_at,
                u.username as client_name,
                u.email as client_email,
                s.overall_score,
                s.profile_type
            FROM assessments a
            JOIN users u ON a.user_id = u.user_id
            LEFT JOIN assessment_summaries s ON a.assessment_id = s.assessment_id
            ORDER BY a.started_at DESC
            LIMIT %s OFFSET %s
        """, (limit, offset))

        diagnoses = cursor.fetchall()

        cursor.close()
        conn.close()

        return {
            "success": True,
            "data": diagnoses
        }

    except Exception as e:
        logger.error(f"Erro ao listar diagnósticos: {e}")
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/admin/diagnoses/{assessment_id}", response_model=dict)
async def get_diagnosis_details(
    assessment_id: int,
    user_id: int = Depends(get_user_from_token)
):
    """
    Detalhes de um diagnóstico específico (apenas admin)
    """
    # Verificar role
    user_role = get_user_role(user_id)
    if user_role != 'admin':
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas admins.")

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Erro ao conectar ao banco")

    try:
        cursor = conn.cursor(dictionary=True)

        # Buscar info do assessment
        cursor.execute("""
            SELECT
                a.assessment_id,
                a.user_id,
                a.status,
                a.completed_at,
                u.name as client_name,
                s.overall_score,
                s.profile_type,
                s.main_insights,
                s.action_plan,
                strong.area_name as strongest_area,
                weak.area_name as weakest_area
            FROM assessments a
            JOIN users u ON a.user_id = u.id
            LEFT JOIN assessment_summaries s ON a.assessment_id = s.assessment_id
            LEFT JOIN diagnosis_areas strong ON s.strongest_area_id = strong.area_id
            LEFT JOIN diagnosis_areas weak ON s.weakest_area_id = weak.area_id
            WHERE a.assessment_id = %s
        """, (assessment_id,))

        assessment = cursor.fetchone()
        if not assessment:
            cursor.close()
            conn.close()
            raise HTTPException(status_code=404, detail="Diagnóstico não encontrado")

        # Buscar scores por área
        cursor.execute("""
            SELECT
                da.area_name,
                aas.score,
                aas.strengths,
                aas.improvements,
                aas.recommendations
            FROM assessment_area_scores aas
            JOIN diagnosis_areas da ON aas.area_id = da.area_id
            WHERE aas.assessment_id = %s
            ORDER BY da.area_order
        """, (assessment_id,))

        area_scores = cursor.fetchall()
        assessment['area_scores'] = area_scores

        cursor.close()
        conn.close()

        return {
            "success": True,
            "data": assessment
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao obter detalhes do diagnóstico: {e}")
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=str(e))


# =====================================================
# ENDPOINTS DE AUDITORIA AGENTFS (Isolamento por Usuário)
# =====================================================
#
# ARQUITETURA:
# - Cada usuário tem seu próprio banco em .agentfs/user-{id}.db
# - Admin pode ver auditoria de qualquer usuário
# - Usuário comum só vê sua própria auditoria
#

@app.get("/api/admin/audit/users", response_model=dict)
async def list_audit_users(user_id: int = Depends(get_user_from_token)):
    """
    Lista usuários com dados de auditoria (apenas admin)

    Retorna:
    - Lista de user_ids que têm bancos AgentFS
    """
    from core.agentfs_manager import get_agentfs_manager

    # Verificar role
    user_role = get_user_role(user_id)
    if user_role != 'admin':
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas admins.")

    try:
        manager = await get_agentfs_manager()
        user_ids = manager.list_user_dbs()

        return {
            "success": True,
            "data": {
                "users": user_ids,
                "total": len(user_ids)
            }
        }

    except Exception as e:
        logger.error(f"Erro ao listar usuários de auditoria: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/admin/audit/user/{target_user_id}", response_model=dict)
async def get_user_audit(
    target_user_id: int,
    user_id: int = Depends(get_user_from_token)
):
    """
    Auditoria completa de um usuário específico (apenas admin)

    Retorna:
    - Estatísticas de tools do usuário
    - Resumo de chamadas
    """
    from core.agentfs_client import get_agentfs
    from core.agentfs_manager import get_agentfs_manager

    # Verificar role
    user_role = get_user_role(user_id)
    if user_role != 'admin':
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas admins.")

    try:
        manager = await get_agentfs_manager()

        # Verificar se o DB do usuário existe
        if not manager.db_exists(target_user_id):
            return {
                "success": True,
                "data": {
                    "user_id": target_user_id,
                    "exists": False,
                    "tools": [],
                    "summary": {
                        "total_calls": 0,
                        "total_errors": 0,
                        "total_duration_ms": 0
                    }
                }
            }

        agentfs = await get_agentfs(user_id=target_user_id)
        stats = await agentfs.tool_stats()

        # Calcular métricas agregadas
        total_calls = sum(s.get("calls", 0) for s in stats)
        total_errors = sum(s.get("errors", 0) for s in stats)
        total_duration = sum(s.get("total_duration_ms", 0) for s in stats)

        return {
            "success": True,
            "data": {
                "user_id": target_user_id,
                "exists": True,
                "tools": stats,
                "summary": {
                    "total_calls": total_calls,
                    "total_errors": total_errors,
                    "total_duration_ms": total_duration,
                    "avg_duration_ms": round(total_duration / total_calls, 2) if total_calls > 0 else 0,
                    "error_rate": round(total_errors / total_calls * 100, 2) if total_calls > 0 else 0
                }
            }
        }

    except Exception as e:
        logger.error(f"Erro ao obter auditoria do usuário {target_user_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/admin/audit/user/{target_user_id}/calls", response_model=dict)
async def get_user_audit_calls(
    target_user_id: int,
    limit: int = 100,
    user_id: int = Depends(get_user_from_token)
):
    """
    Lista chamadas recentes de um usuário específico (apenas admin)

    Retorna:
    - Lista de chamadas de tools ordenadas por timestamp
    """
    from core.agentfs_client import get_agentfs
    from core.agentfs_manager import get_agentfs_manager

    # Verificar role
    user_role = get_user_role(user_id)
    if user_role != 'admin':
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas admins.")

    try:
        manager = await get_agentfs_manager()

        if not manager.db_exists(target_user_id):
            return {
                "success": True,
                "data": {
                    "user_id": target_user_id,
                    "calls": [],
                    "total": 0
                }
            }

        agentfs = await get_agentfs(user_id=target_user_id)

        # Listar todas as chamadas
        keys = await agentfs.kv_list("tool_call:")
        calls = []

        for key in keys:
            data = await agentfs.kv_get(key)
            if data:
                calls.append({
                    "key": key,
                    "name": data.get("name"),
                    "status": data.get("status"),
                    "duration_ms": data.get("duration_ms", 0),
                    "started_at": data.get("started_at"),
                    "parameters": data.get("parameters"),
                    "result": data.get("result")
                })

        # Ordenar por timestamp (mais recente primeiro) e limitar
        calls.sort(key=lambda x: x.get("started_at", 0), reverse=True)
        calls = calls[:limit]

        return {
            "success": True,
            "data": {
                "user_id": target_user_id,
                "calls": calls,
                "total": len(calls)
            }
        }

    except Exception as e:
        logger.error(f"Erro ao obter chamadas do usuário {target_user_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/user/audit/my-tools", response_model=dict)
async def get_my_audit(user_id: int = Depends(get_user_from_token)):
    """
    Usuário vê sua própria auditoria de ferramentas

    Retorna:
    - Estatísticas das tools usadas pelo próprio usuário
    """
    from core.agentfs_client import get_agentfs
    from core.agentfs_manager import get_agentfs_manager

    try:
        manager = await get_agentfs_manager()

        if not manager.db_exists(user_id):
            return {
                "success": True,
                "data": {
                    "tools": [],
                    "summary": {
                        "total_calls": 0,
                        "total_errors": 0,
                        "total_duration_ms": 0
                    }
                }
            }

        agentfs = await get_agentfs(user_id=user_id)
        stats = await agentfs.tool_stats()

        # Calcular métricas agregadas
        total_calls = sum(s.get("calls", 0) for s in stats)
        total_errors = sum(s.get("errors", 0) for s in stats)
        total_duration = sum(s.get("total_duration_ms", 0) for s in stats)

        return {
            "success": True,
            "data": {
                "tools": stats,
                "summary": {
                    "total_calls": total_calls,
                    "total_errors": total_errors,
                    "total_duration_ms": total_duration,
                    "avg_duration_ms": round(total_duration / total_calls, 2) if total_calls > 0 else 0
                }
            }
        }

    except Exception as e:
        logger.error(f"Erro ao obter auditoria do usuário {user_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==============================================================================
# ENDPOINTS DE ARTEFATOS DO USUÁRIO (Filesystem AgentFS)
# ==============================================================================

@app.get("/api/user/artifacts", response_model=dict)
async def list_my_artifacts(
    path: str = "/artifacts",
    user_id: int = Depends(get_user_from_token)
):
    """
    Lista artefatos gerados pelo agente para o usuário.

    Os artefatos ficam no filesystem virtual do AgentFS (dentro do SQLite).
    Exemplos: diagnósticos, gráficos, planos de ação.

    Query params:
    - path: Diretório a listar (default: /artifacts)
    """
    from core.agentfs_client import get_agentfs
    from core.agentfs_manager import get_agentfs_manager

    try:
        manager = await get_agentfs_manager()

        if not manager.db_exists(user_id):
            return {
                "success": True,
                "path": path,
                "artifacts": [],
                "message": "Nenhum artefato encontrado"
            }

        agentfs = await get_agentfs(user_id=user_id)

        try:
            files = await agentfs.fs_list(path)
            # Formatar lista de arquivos
            artifacts = []
            for f in files:
                if isinstance(f, str):
                    artifacts.append({"name": f, "path": f"{path}/{f}"})
                elif isinstance(f, dict):
                    artifacts.append(f)
                else:
                    artifacts.append({"name": str(f), "path": f"{path}/{f}"})

            return {
                "success": True,
                "path": path,
                "artifacts": artifacts,
                "total": len(artifacts)
            }
        except Exception as e:
            # Diretório não existe ainda
            return {
                "success": True,
                "path": path,
                "artifacts": [],
                "message": f"Diretório {path} não existe ainda"
            }

    except Exception as e:
        logger.error(f"Erro ao listar artefatos do usuário {user_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/user/artifacts/download")
async def download_artifact(
    path: str,
    user_id: int = Depends(get_user_from_token)
):
    """
    Baixa um artefato específico do filesystem do usuário.

    Query params:
    - path: Caminho do arquivo (ex: /artifacts/diagnostico.pdf)
    """
    from core.agentfs_client import get_agentfs
    from core.agentfs_manager import get_agentfs_manager
    from fastapi.responses import Response
    import mimetypes

    try:
        manager = await get_agentfs_manager()

        if not manager.db_exists(user_id):
            raise HTTPException(status_code=404, detail="Nenhum artefato encontrado")

        agentfs = await get_agentfs(user_id=user_id)

        try:
            # Tentar ler como bytes primeiro
            content = await agentfs.fs_read(path, as_bytes=True)

            # Determinar tipo MIME
            mime_type, _ = mimetypes.guess_type(path)
            if mime_type is None:
                mime_type = "application/octet-stream"

            filename = path.split("/")[-1]

            return Response(
                content=content,
                media_type=mime_type,
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}"'
                }
            )
        except Exception as e:
            raise HTTPException(status_code=404, detail=f"Arquivo não encontrado: {path}")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao baixar artefato {path} do usuário {user_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/user/agent-history", response_model=dict)
async def get_my_agent_history(
    hours: int = 24,
    user_id: int = Depends(get_user_from_token)
):
    """
    Retorna a HISTÓRIA COMPLETA do agente para o usuário.

    Combina os 3 pilares do AgentFS:
    - tool_calls: O que o agente FEZ (ações)
    - kv_store: O que o agente SABIA (contexto)
    - filesystem: O que o agente PRODUZIU (artefatos)

    Query params:
    - hours: Últimas N horas de histórico (default: 24)
    """
    from core.agentfs_client import get_agentfs
    from core.agentfs_manager import get_agentfs_manager

    try:
        manager = await get_agentfs_manager()

        if not manager.db_exists(user_id):
            return {
                "success": True,
                "history": {
                    "actions": [],
                    "context": {},
                    "artifacts": []
                },
                "message": "Nenhum histórico encontrado"
            }

        agentfs = await get_agentfs(user_id=user_id)

        # 1. AÇÕES (tool_calls)
        try:
            recent_calls = await agentfs.tool_get_recent(limit=50, hours=hours)
            actions = []
            for call in recent_calls:
                actions.append({
                    "id": getattr(call, 'id', None),
                    "tool": getattr(call, 'name', 'unknown'),
                    "status": getattr(call, 'status', 'unknown'),
                    "duration_ms": getattr(call, 'duration_ms', 0),
                    "started_at": getattr(call, 'started_at', 0),
                    "error": getattr(call, 'error', None)
                })
        except Exception:
            actions = []

        # 2. CONTEXTO (kv_store - chaves importantes)
        context = {}
        important_keys = [
            "ultima_busca", "preferencias", "contexto_sessao",
            "ultimo_diagnostico", "ultimo_assessment"
        ]
        for key in important_keys:
            try:
                value = await agentfs.kv_get(key)
                if value is not None:
                    context[key] = value
            except Exception:
                pass

        # 3. ARTEFATOS (filesystem)
        artifacts = []
        try:
            files = await agentfs.fs_list("/artifacts")
            for f in files:
                name = f if isinstance(f, str) else str(f)
                artifacts.append({
                    "name": name,
                    "path": f"/artifacts/{name}"
                })
        except Exception:
            pass

        return {
            "success": True,
            "user_id": user_id,
            "period_hours": hours,
            "history": {
                "actions": actions,
                "actions_count": len(actions),
                "context": context,
                "artifacts": artifacts,
                "artifacts_count": len(artifacts)
            },
            "summary": f"{len(actions)} ações, {len(artifacts)} artefatos nas últimas {hours}h"
        }

    except Exception as e:
        logger.error(f"Erro ao obter histórico do usuário {user_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Endpoints antigos (deprecated - mantidos para compatibilidade temporária)
@app.get("/api/admin/audit/tools", response_model=dict, deprecated=True)
async def get_tool_audit_stats_deprecated(user_id: int = Depends(get_user_from_token)):
    """
    DEPRECATED: Use /api/admin/audit/user/{user_id} ao invés.

    Este endpoint agora retorna dados agregados de todos os usuários.
    """
    from core.agentfs_manager import get_agentfs_manager
    from core.agentfs_client import get_agentfs

    user_role = get_user_role(user_id)
    if user_role != 'admin':
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas admins.")

    try:
        manager = await get_agentfs_manager()
        user_ids = manager.list_user_dbs()

        # Agregar stats de todos os usuários
        all_stats = {}
        for uid in user_ids:
            try:
                agentfs = await get_agentfs(user_id=uid)
                stats = await agentfs.tool_stats()
                for s in stats:
                    name = s.get("name", "unknown")
                    if name not in all_stats:
                        all_stats[name] = {
                            "name": name,
                            "calls": 0,
                            "successes": 0,
                            "errors": 0,
                            "total_duration_ms": 0
                        }
                    all_stats[name]["calls"] += s.get("calls", 0)
                    all_stats[name]["successes"] += s.get("successes", 0)
                    all_stats[name]["errors"] += s.get("errors", 0)
                    all_stats[name]["total_duration_ms"] += s.get("total_duration_ms", 0)
            except Exception:
                pass

        stats_list = list(all_stats.values())
        total_calls = sum(s.get("calls", 0) for s in stats_list)
        total_errors = sum(s.get("errors", 0) for s in stats_list)
        total_duration = sum(s.get("total_duration_ms", 0) for s in stats_list)

        return {
            "success": True,
            "data": {
                "tools": stats_list,
                "summary": {
                    "total_calls": total_calls,
                    "total_errors": total_errors,
                    "total_duration_ms": total_duration,
                    "avg_duration_ms": round(total_duration / total_calls, 2) if total_calls > 0 else 0,
                    "error_rate": round(total_errors / total_calls * 100, 2) if total_calls > 0 else 0,
                    "users_count": len(user_ids)
                },
                "_deprecated": "Use /api/admin/audit/user/{user_id} for per-user audit"
            }
        }

    except Exception as e:
        logger.error(f"Erro ao obter estatísticas de auditoria: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/admin/audit/recent", response_model=dict, deprecated=True)
async def get_recent_tool_calls_deprecated(
    limit: int = 100,
    user_id: int = Depends(get_user_from_token)
):
    """
    DEPRECATED: Use /api/admin/audit/user/{user_id}/calls ao invés.
    """
    user_role = get_user_role(user_id)
    if user_role != 'admin':
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas admins.")

    return {
        "success": True,
        "data": {
            "calls": [],
            "total": 0,
            "_deprecated": "Use /api/admin/audit/user/{user_id}/calls for per-user calls"
        }
    }


# =====================================================
# ENDPOINTS DE MENTOR
# =====================================================

@app.get("/api/mentor/mentorados", response_model=dict)
async def list_my_mentorados(
    page: int = 1,
    per_page: int = 20,
    user_id: int = Depends(get_user_from_token)
):
    """
    Lista os mentorados do mentor (mentor ou admin)
    """
    # Verificar role
    user_role = get_user_role(user_id)
    if user_role not in ['admin', 'mentor']:
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas mentores e admins.")

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Erro ao conectar ao banco")

    try:
        cursor = conn.cursor(dictionary=True)
        offset = (page - 1) * per_page

        # Admin vê todos, mentor vê apenas seus
        if user_role == 'admin':
            query = "SELECT * FROM vw_mentorados_mentores LIMIT %s OFFSET %s"
            params = (per_page, offset)
            count_query = "SELECT COUNT(*) as total FROM users WHERE role = 'mentorado'"
            count_params = ()
        else:
            query = "SELECT * FROM vw_mentorados_mentores WHERE mentor_id = %s LIMIT %s OFFSET %s"
            params = (user_id, per_page, offset)
            count_query = "SELECT COUNT(*) as total FROM users WHERE role = 'mentorado' AND mentor_id = %s"
            count_params = (user_id,)

        cursor.execute(query, params)
        mentorados = cursor.fetchall()

        cursor.execute(count_query, count_params)
        total = cursor.fetchone()['total']

        cursor.close()
        conn.close()

        return {
            "success": True,
            "data": mentorados,
            "total": total,
            "page": page,
            "per_page": per_page
        }

    except Exception as e:
        logger.error(f"Erro ao listar mentorados: {e}")
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=str(e))


# REMOVIDO: Funcionalidade de invite codes descontinuada
# @app.get("/api/mentor/invite", response_model=dict)
# async def get_my_invite_code(user_id: int = Depends(get_user_from_token)):
#     """Funcionalidade de códigos de convite foi removida"""
#     raise HTTPException(status_code=410, detail="Funcionalidade descontinuada")


@app.get("/api/mentor/stats", response_model=dict)
async def get_mentor_statistics(user_id: int = Depends(get_user_from_token)):
    """
    Estatísticas do mentor
    """
    # Verificar role
    user_role = get_user_role(user_id)
    if user_role not in ['admin', 'mentor']:
        raise HTTPException(status_code=403, detail="Acesso negado. Apenas mentores e admins.")

    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Erro ao conectar ao banco")

    try:
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT * FROM vw_mentor_stats WHERE mentor_id = %s
        """, (user_id,))

        stats = cursor.fetchone()

        cursor.close()
        conn.close()

        if not stats:
            return {
                "success": True,
                "data": {
                    "total_mentorados": 0,
                    "total_assessments": 0,
                    "media_score_mentorados": 0
                }
            }

        return {"success": True, "data": stats}

    except Exception as e:
        logger.error(f"Erro ao obter estatísticas: {e}")
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=str(e))


# =====================================================
# ENDPOINT PÚBLICO - Listar Mentores Disponíveis
# =====================================================

@app.get("/api/auth/mentors", response_model=dict)
async def list_available_mentors():
    """
    Lista mentores disponíveis para seleção no cadastro (público)
    """
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Erro ao conectar ao banco")

    try:
        cursor = conn.cursor(dictionary=True)

        # WORKAROUND: mentor_id removido
        cursor.execute("""
            SELECT
                user_id, username, email,
                0 as total_mentorados
            FROM users
            WHERE role = 'mentor' AND account_status = 'active'
            ORDER BY username
        """)

        mentors = cursor.fetchall()
        cursor.close()
        conn.close()

        return {"success": True, "data": mentors}

    except Exception as e:
        logger.error(f"Erro ao listar mentores disponíveis: {e}")
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=str(e))


# Schedule daily token cleanup
from apscheduler.schedulers.background import BackgroundScheduler

scheduler = BackgroundScheduler()
scheduler.add_job(cleanup_expired_tokens, 'cron', hour=3, minute=0)


# ==============================================================================
# AgentFS Cleanup Job - Remove dados antigos para controle de storage
# ==============================================================================
def cleanup_agentfs_data():
    """
    Remove dados antigos do AgentFS (roda diariamente às 3:30 AM).

    Política de retenção:
    - tool_calls > 30 dias: DELETE
    - kv_store > 90 dias: DELETE
    - VACUUM após cleanup (recupera espaço em disco)
    """
    import asyncio

    async def _run_cleanup():
        try:
            from core.agentfs_manager import get_agentfs_manager
            manager = await get_agentfs_manager()
            results = await manager.cleanup_all_users(
                tool_calls_days=30,
                kv_days=90,
                vacuum=True
            )
            logger.info(
                f"[AgentFS Cleanup] Completed: "
                f"{results['users_cleaned']}/{results['users_processed']} users, "
                f"{results['tool_calls_deleted']} tool_calls, "
                f"{results['kv_deleted']} kv entries removed"
            )
            if results['errors']:
                logger.warning(f"[AgentFS Cleanup] Errors: {results['errors']}")
        except Exception as e:
            logger.error(f"[AgentFS Cleanup] Failed: {e}")

    # Executar async function no contexto sync do scheduler
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(_run_cleanup())
        else:
            asyncio.run(_run_cleanup())
    except RuntimeError:
        asyncio.run(_run_cleanup())


scheduler.add_job(cleanup_agentfs_data, 'cron', hour=3, minute=30, id='agentfs_cleanup')
scheduler.start()

logger.info("[Scheduler] Token cleanup job scheduled for 3:00 AM daily")
logger.info("[Scheduler] AgentFS cleanup job scheduled for 3:30 AM daily")

# Run the app
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8234)