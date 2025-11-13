"""
Database module with PostgreSQL models
"""
from sqlalchemy import create_engine, Column, String, Text, Boolean, ForeignKey, DateTime
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
from sqlalchemy.sql import func
from datetime import datetime
import uuid
import logging

from config import Config

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create SQLAlchemy engine
engine = create_engine(
    Config.DATABASE_URL,
    pool_pre_ping=True,
    echo=Config.DEBUG
)

# Create session factory
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

# Base class for models
Base = declarative_base()


class Service(Base):
    """Service model - represents a registered WSDL service"""
    __tablename__ = 'services'

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), unique=True, nullable=False, index=True)
    wsdl_url = Column(Text, nullable=False)
    description = Column(Text)
    openapi_spec = Column(JSONB, nullable=False)

    # Gateway registration tracking
    gateway_registered = Column(Boolean, default=False, index=True)
    gateway_server_uuid = Column(PGUUID(as_uuid=True), nullable=True)
    gateway_mcp_endpoint = Column(Text, nullable=True)
    gateway_registered_at = Column(DateTime(timezone=True), nullable=True)

    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    operations = relationship("Operation", back_populates="service", cascade="all, delete-orphan")

    def to_dict(self):
        """Convert to dictionary"""
        return {
            'id': str(self.id),
            'name': self.name,
            'wsdl_url': self.wsdl_url,
            'description': self.description,
            'openapi_spec': self.openapi_spec,
            'gateway_registered': self.gateway_registered,
            'gateway_server_uuid': str(self.gateway_server_uuid) if self.gateway_server_uuid else None,
            'gateway_mcp_endpoint': self.gateway_mcp_endpoint,
            'gateway_registered_at': self.gateway_registered_at.isoformat() if self.gateway_registered_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'operations_count': len(self.operations)
        }


class Operation(Base):
    """Operation model - represents a SOAP operation"""
    __tablename__ = 'operations'

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    service_id = Column(PGUUID(as_uuid=True), ForeignKey('services.id', ondelete='CASCADE'), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    soap_action = Column(Text)
    input_schema = Column(JSONB)
    output_schema = Column(JSONB)

    # Gateway tool tracking
    gateway_tool_id = Column(Text, nullable=True)

    # Relationship
    service = relationship("Service", back_populates="operations")

    def to_dict(self):
        """Convert to dictionary"""
        return {
            'id': str(self.id),
            'service_id': str(self.service_id),
            'name': self.name,
            'soap_action': self.soap_action,
            'input_schema': self.input_schema,
            'output_schema': self.output_schema,
            'gateway_tool_id': self.gateway_tool_id
        }


class WSDLCache(Base):
    """WSDL Cache model - stores WSDL metadata for optimization"""
    __tablename__ = 'wsdl_cache'

    wsdl_url = Column(Text, primary_key=True)
    service_name = Column(String(255), index=True)
    last_accessed = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def to_dict(self):
        """Convert to dictionary"""
        return {
            'wsdl_url': self.wsdl_url,
            'service_name': self.service_name,
            'last_accessed': self.last_accessed.isoformat() if self.last_accessed else None
        }


def init_db():
    """Initialize database - create all tables if they don't exist"""
    try:
        logger.info("Initializing database tables...")
        Base.metadata.create_all(bind=engine, checkfirst=True)
        logger.info("Database tables initialized successfully")
    except Exception as e:
        logger.error(f"Error initializing database tables: {e}")
        raise


def get_db():
    """Get database session (for dependency injection)"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Initialize database on module import
if __name__ != '__main__':
    init_db()
