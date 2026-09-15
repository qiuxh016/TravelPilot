from dataclasses import dataclass, field
from datetime import date
from uuid import UUID, uuid4


@dataclass(frozen=True)
class Trip:
    destination: str
    start_date: date
    end_date: date
    id: UUID = field(default_factory=uuid4)
