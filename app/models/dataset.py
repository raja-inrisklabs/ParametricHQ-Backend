import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, Text, BigInteger
from app.core.database import Base


class DatasetRecord(Base):
    __tablename__ = "datasets"

    id = Column(Integer, primary_key=True, index=True)
    data_key = Column(String(128), unique=True, index=True, nullable=False)
    provider_id = Column(String(64), index=True, nullable=False)
    parameter_type = Column(String(64), index=True, nullable=False)
    location = Column(String(255), nullable=False)
    
    # Spatial bounding box / coordinates
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    bbox_north = Column(Float, nullable=True)
    bbox_south = Column(Float, nullable=True)
    bbox_east = Column(Float, nullable=True)
    bbox_west = Column(Float, nullable=True)

    # Temporal range & resolution
    start_date = Column(String(32), nullable=False)
    end_date = Column(String(32), nullable=False)
    resolution = Column(String(32), default="0.25deg")
    version = Column(String(32), default="v1")

    # Status & Storage
    status = Column(String(32), default="ready", index=True)  # pending, processing, ready, failed
    file_path = Column(String(512), nullable=True)
    file_size_bytes = Column(BigInteger, default=0)
    record_count = Column(Integer, default=0)
    checksum_sha256 = Column(String(64), nullable=True)
    metadata_json = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"

    id = Column(String(64), primary_key=True, index=True)
    data_key = Column(String(128), index=True, nullable=False)
    provider_id = Column(String(64), nullable=False)
    status = Column(String(32), default="pending", index=True)  # pending, processing, completed, failed
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
