"""Schemas for Frame."""

from pydantic import BaseModel, Field
from .config import MAX_VIDEO_SIZE


class UploadCreate(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0, le=MAX_VIDEO_SIZE)


class JobCreate(BaseModel):
    upload_id: str
    entities: list[str] = Field(min_length=1)
    instructions: str = Field(default="", max_length=10000)


class ChatMessage(BaseModel):
    content: str = Field(default="", max_length=4000)
    image_ids: list[str] = Field(default_factory=list, max_length=10)


class Login(BaseModel):
    username: str
    password: str
    remember: bool = False


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=12, max_length=128)
