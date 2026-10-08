from alembic import context

from app.config import Settings
from app.database import make_database
from app.models import Base


def run():
    settings = Settings()
    if context.is_offline_mode():
        context.configure(url=settings.database_url, target_metadata=Base.metadata,
                          literal_binds=True)
        with context.begin_transaction():
            context.run_migrations()
    else:
        engine, _ = make_database(settings.database_url)
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=Base.metadata)
            with context.begin_transaction():
                context.run_migrations()
        engine.dispose()


run()
