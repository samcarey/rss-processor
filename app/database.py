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

    _migrate(engine)

    return Session


def _migrate(engine):
    """Additive column migrations (SQLite: create_all won't alter tables)."""
    from sqlalchemy import text
    wanted = {
        'removed_segments': [
            ("method", "VARCHAR(128)"),
            ("confidence", "FLOAT"),
            ("n_matches", "INTEGER"),
            ("transcript_excerpt", "TEXT"),
        ],
    }
    with engine.connect() as conn:
        for table, cols in wanted.items():
            existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
            for name, ddl in cols:
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
        # windowed-fingerprint table replaced by .npy raw fingerprints on disk
        conn.execute(text("DROP TABLE IF EXISTS audio_fingerprints"))
        conn.commit()


def get_session():
    """Get database session"""
    global Session
    if Session is None:
        init_db()
    return Session()
