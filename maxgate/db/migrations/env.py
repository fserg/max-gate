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
        await connection.run_sync(run)
    await engine.dispose()


asyncio.run(migrate())
