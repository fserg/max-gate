import asyncio

from alembic import context

from maxgate.db import create_storage
from maxgate.db.models import Base


def run(connection):
    context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


async def migrate():
    engine, _ = create_storage(context.config.get_main_option("sqlalchemy.url"))
    async with engine.connect() as connection:
        # SQLite batch migrations recreate parent tables. Suppress ON DELETE CASCADE
        # only on this isolated migration connection, then validate before commit.
        await connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        await connection.commit()
        try:
            async with connection.begin():
                await connection.exec_driver_sql("BEGIN")
                await connection.run_sync(run)
                violations = (await connection.exec_driver_sql("PRAGMA foreign_key_check")).all()
                if violations:
                    raise ValueError("Foreign key violations after migration")
        finally:
            await connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    await engine.dispose()


asyncio.run(migrate())
