from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import cohort

app = FastAPI(
    title="RevenueLens API",
    version="1.0.0"
)

# ── CORS — allow ALL origins (fixes network error from Vercel) ────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(cohort.router, prefix="/api/cohort", tags=["Cohort"])

@app.get("/")
def root():
    return {"status": "ok", "service": "RevenueLens API"}

@app.get("/health")
def health():
    return {"status": "healthy"}
