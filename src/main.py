from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.database import check_db_connection
from src.routers import documents, exams, questions, review, stats
from src.routers.review import STATIC_DIR


@asynccontextmanager
async def lifespan(app: FastAPI):
  yield


app = FastAPI(
  title="ITA API",
  description=(
    "Banco de questões dos vestibulares e do processo seletivo de "
    "pós-graduação do ITA, extraídas dos PDFs oficiais."
  ),
  version="0.1.0",
  lifespan=lifespan,
)

app.add_middleware(
  CORSMiddleware,
  allow_origins=["*"],
  allow_methods=["GET"],
  allow_headers=["*"],
)

app.include_router(documents.router)
app.include_router(exams.router)
app.include_router(questions.router)
app.include_router(stats.router)
app.include_router(review.router)

# React e htm vendorizados: sem npm, sem build e sem depender de CDN.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", tags=["meta"])
def root():
  return {"message": "ITA API", "docs": "/docs"}


@app.get("/health", tags=["meta"])
async def health():
  db_ok = await check_db_connection()
  status = "ok" if db_ok else "degraded"
  return {"status": status, "database": "ok" if db_ok else "error"}
