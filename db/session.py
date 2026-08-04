"""Engine and session factory.

Owner: B (db). Created here by E only so that the admin module is not blocked
waiting for it. B should take this over; the signature is deliberately small
so that replacing it costs nothing.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


def create_session_factory(database_url: str) -> sessionmaker[Session]:
    """Build a session factory for the given database URL.

    ``pool_pre_ping`` because a development container gets stopped and started
    and the pool would otherwise hand out dead connections.
    """
    engine = create_engine(database_url, pool_pre_ping=True, future=True)
    return sessionmaker(bind=engine, future=True, expire_on_commit=False)
