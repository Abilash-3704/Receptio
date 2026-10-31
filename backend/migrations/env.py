from alembic import context
from sqlalchemy import Connection

# The app passes the connection it already opened (see backend.app.migrate).
connection: Connection = context.config.attributes["connection"]
context.configure(connection=connection, target_metadata=None)
with context.begin_transaction():
    context.run_migrations()
