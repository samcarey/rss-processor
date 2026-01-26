from sqlalchemy import create_engine
from sqlalchemy.orm import scoped_session, sessionmaker, declarative_base
import os
import yaml

Base = declarative_base()

# Global session
Session = None
engine = None


def get_db_path():
    """Get database path from config"""
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config.yaml')
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)
    return config['storage']['database']


def init_db():
    """Initialize database and create tables"""
    global Session, engine

    db_path = get_db_path()
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    # Configure engine with better connection pool settings
    engine = create_engine(
        f'sqlite:///{db_path}',
        echo=False,
        pool_size=20,
        max_overflow=40,
        pool_timeout=60,
        pool_recycle=3600,
        connect_args={'check_same_thread': False}  # Required for SQLite with threading
    )

    # Enable WAL mode for better concurrency
    from sqlalchemy import event
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    Session = scoped_session(sessionmaker(bind=engine, autocommit=False, autoflush=False))

    # Import models to register them
    from app import models

    # Create all tables
    Base.metadata.create_all(engine)

    return Session


def get_session():
    """Get database session"""
    global Session
    if Session is None:
        init_db()
    return Session()
