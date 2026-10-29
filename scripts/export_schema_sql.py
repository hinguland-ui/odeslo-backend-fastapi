"""Print the PostgreSQL CREATE TABLE statements for every model (review SQL before running it).

    python -m scripts.export_schema_sql > schema.sql
"""

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

import app.models  # noqa: F401
from app.core.database import Base


def main() -> None:
    dialect = postgresql.dialect()
    for table in Base.metadata.sorted_tables:
        print(f"{str(CreateTable(table).compile(dialect=dialect)).strip()};\n")
        for index in table.indexes:
            print(f"{str(CreateIndex(index).compile(dialect=dialect)).strip()};")
        print()


if __name__ == "__main__":
    main()
