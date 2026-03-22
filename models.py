"""
SQLAlchemy models for the Artwork Display Engine.
Phase 3: Many-to-Many relationship between Playlists and Artworks.
"""

from typing import List, Optional
from sqlalchemy import Column, Integer, String, Float, ForeignKey, Text, Table, Boolean
from sqlalchemy.orm import relationship, Mapped, mapped_column

from database import Base

# Association Table for Many-to-Many relationship
# Includes display_order to allow unique sequencing per playlist
playlist_artwork = Table(
    "playlist_artwork",
    Base.metadata,
    Column("playlist_id", Integer, ForeignKey("playlists.id"), primary_key=True),
    Column("artwork_id", Integer, ForeignKey("artworks.id"), primary_key=True),
    Column("display_order", Integer, default=0)
)

class PlaylistModel(Base):
    """
    Table defining the artwork playlists.
    """
    __tablename__ = "playlists"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String, unique=True, index=True)
    display_time: Mapped[int] = mapped_column(Integer, default=30)
    default_mode: Mapped[str] = mapped_column(String, default="ken-burns")
    shuffle: Mapped[bool] = mapped_column(Boolean, default=False)
    
    # Placard Timers (stored in seconds)
    placard_initial_wait_sec: Mapped[int] = mapped_column(Integer, default=5)
    placard_initial_show_sec: Mapped[int] = mapped_column(Integer, default=15)
    placard_interaction_show_sec: Mapped[int] = mapped_column(Integer, default=10)
    
    # Many-to-Many relationship
    artworks: Mapped[List["ArtworkModel"]] = relationship(
        secondary=playlist_artwork,
        back_populates="playlists",
        lazy="selectin",
        order_by="playlist_artwork.c.display_order"
    )

    def __repr__(self) -> str:
        return f"<Playlist(name='{self.name}')>"

class ArtworkModel(Base):
    """
    Table defining individual artwork metadata.
    Decoupled from specific playlists to allow library-wide management.
    """
    __tablename__ = "artworks"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    filename: Mapped[str] = mapped_column(String, index=True)
    
    # Original Dimensions
    original_width: Mapped[int] = mapped_column(Integer, default=0)
    original_height: Mapped[int] = mapped_column(Integer, default=0)
    
    # Many-to-Many relationship
    playlists: Mapped[List["PlaylistModel"]] = relationship(
        secondary=playlist_artwork,
        back_populates="artworks"
    )

    # Metadata
    title: Mapped[Optional[str]] = mapped_column(String, index=True)
    artist: Mapped[Optional[str]] = mapped_column(String, index=True)
    year: Mapped[Optional[str]] = mapped_column(String)
    description: Mapped[Optional[str]] = mapped_column(Text)
    tags: Mapped[Optional[str]] = mapped_column(Text)
    
    status: Mapped[str] = mapped_column(String, default='pending_review', index=True)

    # Crop Metadata (Stored in Original Pixels)
    crop_x: Mapped[Optional[float]] = mapped_column(Float, default=0.0)
    crop_y: Mapped[Optional[float]] = mapped_column(Float, default=0.0)
    crop_width: Mapped[Optional[float]] = mapped_column(Float, default=0.0)
    crop_height: Mapped[Optional[float]] = mapped_column(Float, default=0.0)

    def __repr__(self) -> str:
        return f"<Artwork(filename='{self.filename}', status='{self.status}')>"
