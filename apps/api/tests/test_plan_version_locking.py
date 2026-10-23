from uuid import uuid4

import pytest
from travelpilot_infra.db.models.trip import TripModel
from travelpilot_infra.db.repositories.plans import PlanRepository


class ScalarSession:
    def __init__(self):
        self.statements = []
        self.values = [uuid4(), 6]

    async def scalar(self, statement):
        self.statements.append(statement)
        return self.values.pop(0)


@pytest.mark.asyncio
async def test_plan_version_allocation_locks_trip_before_reading_max_version():
    trip_id = uuid4()
    session = ScalarSession()

    version = await PlanRepository(session).next_version(trip_id)

    assert version == 7
    lock_statement, max_version_statement = session.statements
    assert lock_statement._for_update_arg is not None
    assert lock_statement.column_descriptions[0]["expr"] is TripModel.id
    compiled = str(lock_statement.compile(compile_kwargs={"literal_binds": True})).replace("-", "")
    assert trip_id.hex in compiled
    assert max_version_statement._for_update_arg is None
