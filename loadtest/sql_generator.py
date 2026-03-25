"""Generate SQL templates and CREATE TABLE scripts."""


def generate_insert_sql(table: str, columns: dict[str, str]) -> str:
    col_names = list(columns.keys())
    params = [f"@p{i+1}" for i in range(len(col_names))]
    return (
        f"INSERT INTO {table} ({', '.join(col_names)}) "
        f"VALUES ({', '.join(params)})"
    )


def generate_update_sql(
    table: str, columns: dict[str, str], key_columns: list[str]
) -> str:
    non_key = [c for c in columns if c not in key_columns]
    param_idx = 1
    set_parts = []
    for col in non_key:
        set_parts.append(f"{col} = @p{param_idx}")
        param_idx += 1
    where_parts = []
    for col in key_columns:
        where_parts.append(f"{col} = @p{param_idx}")
        param_idx += 1
    return (
        f"UPDATE {table} SET {', '.join(set_parts)} "
        f"WHERE {' AND '.join(where_parts)}"
    )


def generate_delete_sql(
    table: str, key_columns: list[str]
) -> str:
    where_parts = [f"{col} = @p{i+1}" for i, col in enumerate(key_columns)]
    return f"DELETE FROM {table} WHERE {' AND '.join(where_parts)}"


def generate_create_table_sql(table: str, columns: dict[str, str]) -> str:
    col_defs = [f"    {name} {sql_type}" for name, sql_type in columns.items()]
    return f"CREATE TABLE {table} (\n{',\n'.join(col_defs)}\n);"
