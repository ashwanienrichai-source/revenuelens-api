from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import cohort

app = FastAPI(
    title="RevenueLens API",
    description="Analytics engine for RevenueLens SaaS platform",
    version="1.0.0"
)

# ── CORS — allow Next.js frontend ────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "https://revenuelens.vercel.app",
        "https://*.vercel.app",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ──────────────────────────────────────────────────────────
app.include_router(cohort.router, prefix="/api/cohort", tags=["Cohort Analytics"])

@app.get("/")
def root():
    return {"status": "ok", "service": "RevenueLens API", "version": "1.0.0"}

@app.get("/health")
def health():
    return {"status": "healthy"}
