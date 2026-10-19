from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

database_url = os.environ.get(
    "WORKER_DATABASE_URL",
    "postgresql+psycopg://align_worker:align_worker@localhost:5432/align",
)
engine = create_engine(database_url, pool_pre_ping=True, pool_size=3, max_overflow=2)
SessionFactory = sessionmaker(bind=engine, class_=Session, autoflush=False, expire_on_commit=False)

