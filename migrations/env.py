from alembic import context
from app.database import engine_for, database_url, metadata


def run(connection):
    context.configure(connection=connection, target_metadata=metadata)
    with context.begin_transaction():
        context.run_migrations()


connection = context.config.attributes.get("connection")
if connection is not None:
    run(connection)
else:
    with engine_for(database_url()).connect() as connection:
        run(connection)
