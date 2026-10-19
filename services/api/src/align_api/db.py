from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from align_api.config import get_settings

settings = get_settings()
engine = create_engine(settings.database_url, pool_pre_ping=True, pool_size=5, max_overflow=10)
SessionFactory = sessionmaker(bind=engine, class_=Session, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    session = SessionFactory()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

