import uuid
from sqlalchemy import Column, String, Integer, DateTime, Text
from sqlalchemy.types import Uuid
from sqlalchemy.sql import func

from app.db.base import Base


class AsrTask(Base):
    __tablename__ = "asr_tasks"

    task_id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    file_url = Column(Text, nullable=False)
    model = Column(String, nullable=False)
    response_format = Column(String, default="json")
    language = Column(String, default="")
    status = Column(String, default="pending")
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=2)
    result = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    file_path = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
    completed_at = Column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<AsrTask task_id={self.task_id} status={self.status} "
            f"retry={self.retry_count}/{self.max_retries}>"
        )
