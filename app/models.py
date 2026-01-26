from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, Float, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base


class Podcast(Base):
    __tablename__ = 'podcasts'

    id = Column(Integer, primary_key=True)
    feed_url = Column(String(512), unique=True, nullable=False)
    title = Column(String(256))
    description = Column(Text)
    image_url = Column(String(512))
    author = Column(String(256))
    created_at = Column(DateTime, default=datetime.utcnow)
    last_checked_at = Column(DateTime)

    # Processing progress fields
    processing_status = Column(String(50), default='idle')  # idle, downloading, processing, complete
    current_episode_title = Column(String(256))
    episodes_processed = Column(Integer, default=0)
    total_episodes = Column(Integer, default=0)
    processing_started_at = Column(DateTime)
    processing_progress = Column(Integer, default=0)  # 0-100 for current episode

    # Relationships
    episodes = relationship('Episode', back_populates='podcast', cascade='all, delete-orphan')

    def __repr__(self):
        return f'<Podcast {self.title}>'


class Episode(Base):
    __tablename__ = 'episodes'

    id = Column(Integer, primary_key=True)
    podcast_id = Column(Integer, ForeignKey('podcasts.id'), nullable=False)
    guid = Column(String(512), nullable=False)
    title = Column(String(256))
    description = Column(Text)
    pub_date = Column(DateTime)
    original_audio_url = Column(String(512))
    original_audio_path = Column(String(512))
    processed_audio_path = Column(String(512))
    duration_seconds = Column(Float)
    processed = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    podcast = relationship('Podcast', back_populates='episodes')
    removed_segments = relationship('RemovedSegment', back_populates='episode',
                                   foreign_keys='RemovedSegment.episode_id',
                                   cascade='all, delete-orphan')

    def __repr__(self):
        return f'<Episode {self.title}>'


class RemovedSegment(Base):
    __tablename__ = 'removed_segments'

    id = Column(Integer, primary_key=True)
    episode_id = Column(Integer, ForeignKey('episodes.id'), nullable=False)
    start_time = Column(Float, nullable=False)  # seconds
    end_time = Column(Float, nullable=False)    # seconds
    duration = Column(Float, nullable=False)     # seconds
    matched_episode_id = Column(Integer, ForeignKey('episodes.id'))
    matched_start_time = Column(Float)           # seconds
    segment_audio_path = Column(String(512))
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    episode = relationship('Episode', back_populates='removed_segments',
                         foreign_keys=[episode_id])
    matched_episode = relationship('Episode', foreign_keys=[matched_episode_id])

    def __repr__(self):
        return f'<RemovedSegment {self.start_time}-{self.end_time}s from Episode {self.episode_id}>'


class AudioFingerprint(Base):
    """Store audio fingerprints for duplicate detection"""
    __tablename__ = 'audio_fingerprints'

    id = Column(Integer, primary_key=True)
    episode_id = Column(Integer, ForeignKey('episodes.id'), nullable=False)
    start_time = Column(Float, nullable=False)  # seconds
    end_time = Column(Float, nullable=False)    # seconds
    fingerprint = Column(Text, nullable=False)  # Chromaprint fingerprint as string
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationship
    episode = relationship('Episode')

    def __repr__(self):
        return f'<AudioFingerprint Episode {self.episode_id} @ {self.start_time}s>'
