"""
SQLAlchemy models for the Artwork Display Engine.
Phase 2: Database Layer (Playlists and Artwork Metadata).
"""

from typing import List, Optional
from sqlalchemy import Column, Integer, String, Float, ForeignKey
from sqlalchemy.orm import relationship, Mapped, mapped_column

from database import Base

class PlaylistModel(Base):
    __tablename__ = "playlists"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String, unique=True, index=True)
    display_time: Mapped[int] = mapped_column(Integer, default=30)
    
    artworks: Mapped[List["ArtworkModel"]] = relationship(
        back_populates="playlist", 
        cascade="all, delete-orphan",
        order_by="ArtworkModel.display_order"
    )

class ArtworkModel(Base):
    __tablename__ = "artworks"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    filename: Mapped[str] = mapped_column(String, index=True)
    display_order: Mapped[int] = mapped_column(Integer, default=0)
    
    # Original Dimensions (for precise crop scaling)
    original_width: Mapped[int] = mapped_column(Integer, default=0)
    original_height: Mapped[int] = mapped_column(Integer, default=0)
    
    playlist_id: Mapped[int] = mapped_column(ForeignKey("playlists.id"))
    playlist: Mapped["PlaylistModel"] = relationship(back_populates="artworks")

    # Crop Metadata (Stored in Original Pixels)
    crop_x: Mapped[Optional[float]] = mapped_column(Float, default=0.0)
    crop_y: Mapped[Optional[float]] = mapped_column(Float, default=0.0)
    crop_width: Mapped[Optional[float]] = mapped_column(Float, default=0.0)
    crop_height: Mapped[Optional[float]] = mapped_column(Float, default=0.0)

    def __repr__(self) -> str:
        return f"<Artwork(filename='{self.filename}', dim={self.original_width}x{self.original_height})>"
