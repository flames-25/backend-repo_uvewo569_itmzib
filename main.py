import os
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr

from database import db, create_document, get_documents

# Optional imports guarded
try:
    import stripe
except Exception:
    stripe = None

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==========================
# Config
# ==========================
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
CUSTOMER_PORTAL_URL = os.getenv("STRIPE_CUSTOMER_PORTAL_URL", "")

if stripe and STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY


# ==========================
# Schemas for requests
# ==========================
class PlanOut(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    price_cents: int
    interval: str
    features: List[str] = []


class CreateCheckoutSessionIn(BaseModel):
    price_id: str
    customer_email: EmailStr
    success_url: str
    cancel_url: str


class CreatePortalSessionIn(BaseModel):
    customer_id: str
    return_url: str


# ==========================
# Utility
# ==========================

def collection(name: str):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")
    return db[name]


# ==========================
# Health
# ==========================
@app.get("/")
async def root():
    return {"message": "Backend ready", "stripe": bool(STRIPE_SECRET_KEY)}


@app.get("/test")
async def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": [],
    }
    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Set"
            response["database_name"] = getattr(db, "name", "✅ Connected")
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️ Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️ Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"

    response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"
    return response


# ==========================
# Seed sample plans if none
# ==========================

SAMPLE_PLANS = [
    {
        "name": "Starter",
        "description": "For small businesses getting started",
        "price_cents": 1900,
        "interval": "month",
        "stripe_price_id": os.getenv("STRIPE_PRICE_STARTER", "price_XXXX_starter"),
        "features": [
            "Connect 1 Google Business Profile",
            "Basic analytics",
            "Email support",
        ],
    },
    {
        "name": "Growth",
        "description": "For growing teams managing multiple locations",
        "price_cents": 4900,
        "interval": "month",
        "stripe_price_id": os.getenv("STRIPE_PRICE_GROWTH", "price_XXXX_growth"),
        "features": [
            "Connect up to 5 locations",
            "Posts & hours management",
            "Priority support",
        ],
    },
]


@app.on_event("startup")
async def seed_plans():
    try:
        col = collection("plan")
        if col.count_documents({}) == 0:
            for plan in SAMPLE_PLANS:
                create_document("plan", plan)
    except Exception:
        pass


# ==========================
# Plans & billing endpoints
# ==========================

@app.get("/api/plans", response_model=List[PlanOut])
async def get_plans():
    docs = get_documents("plan")
    out: List[PlanOut] = []
    for d in docs:
        out.append(
            PlanOut(
                id=str(d.get("_id")),
                name=d.get("name"),
                description=d.get("description"),
                price_cents=d.get("price_cents", 0),
                interval=d.get("interval", "month"),
                features=d.get("features", []),
            )
        )
    return out


@app.post("/api/stripe/create-checkout-session")
async def create_checkout_session(payload: CreateCheckoutSessionIn):
    if stripe is None or not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=400, detail="Stripe not configured")

    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            payment_method_types=["card"],
            line_items=[{"price": payload.price_id, "quantity": 1}],
            customer_email=payload.customer_email,
            success_url=payload.success_url + "?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=payload.cancel_url,
        )
        return {"id": session.id, "url": session.url}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/stripe/create-portal-session")
async def create_portal_session(payload: CreatePortalSessionIn):
    if stripe is None or not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=400, detail="Stripe not configured")
    try:
        portal_session = stripe.billing_portal.Session.create(
            customer=payload.customer_id,
            return_url=payload.return_url,
        )
        return {"url": portal_session.url}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/stripe/webhook")
async def stripe_webhook(request: Request):
    if stripe is None or not STRIPE_WEBHOOK_SECRET:
        # Accept in dev mode to avoid failures
        return {"received": True, "skipped": True}

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(
            payload=payload, sig_header=sig_header, secret=STRIPE_WEBHOOK_SECRET
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Handle events
    if event["type"] in [
        "checkout.session.completed",
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    ]:
        data = event["data"]["object"]
        try:
            create_document("stripeevent", {"type": event["type"], "data": data})
        except Exception:
            pass

    return {"received": True}


# ==========================
# Google OAuth scaffolding
# ==========================
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "")

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/business.manage",
    "openid",
    "email",
    "profile",
]


@app.get("/api/google/oauth/url")
async def google_oauth_url():
    if not GOOGLE_CLIENT_ID or not GOOGLE_REDIRECT_URI:
        return {
            "ready": False,
            "message": "Google OAuth not configured. Add GOOGLE_CLIENT_ID/SECRET/REDIRECT_URI.",
        }
    import urllib.parse as up

    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(GOOGLE_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
    }
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + up.urlencode(params)
    return {"ready": True, "url": url}


class GoogleOAuthCallbackIn(BaseModel):
    code: str


@app.post("/api/google/oauth/callback")
async def google_oauth_callback(body: GoogleOAuthCallbackIn):
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET or not GOOGLE_REDIRECT_URI:
        raise HTTPException(status_code=400, detail="Google OAuth not configured")

    import requests

    token_url = "https://oauth2.googleapis.com/token"
    data = {
        "code": body.code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "grant_type": "authorization_code",
    }
    r = requests.post(token_url, data=data)
    if r.status_code != 200:
        raise HTTPException(status_code=400, detail="Failed to exchange code")

    tokens = r.json()
    try:
        create_document("googleconnection", tokens)
    except Exception:
        pass
    return {"connected": True, "tokens": {k: (v if k == "scope" else "***") for k, v in tokens.items()}}


@app.get("/api/google/locations")
async def list_google_locations():
    # Placeholder: Requires valid access token and Google Business Profile API
    conns = get_documents("googleconnection")
    if not conns:
        return {"connected": False, "locations": []}
    # In a real implementation, call Google My Business API here
    return {
        "connected": True,
        "locations": [
            {"name": "Demo Location A", "storeCode": "A001"},
            {"name": "Demo Location B", "storeCode": "B002"},
        ],
    }
