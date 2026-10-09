"""Internal fixed SQL execution; never accepts SQL from a user question."""
from contextlib import contextmanager
from pathlib import Path
from datetime import date, datetime
from decimal import Decimal
import re
import hashlib
import duckdb

ROOT = Path(__file__).resolve().parents[2]
DATABASE = ROOT / "data/processed/ecommerce.duckdb"

@contextmanager
def connection():
    if not DATABASE.is_file():
        raise FileNotFoundError(f"Database not found: {DATABASE}")
    con = duckdb.connect(str(DATABASE), read_only=True)
    try:
        con.execute("SET TimeZone='Asia/Shanghai'")
        con.execute("SET memory_limit='3GB'")
        con.execute("SET threads=4")
        yield con
    finally:
        con.close()

def rows(con, sql, parameters=None):
    result = con.execute(sql, parameters or [])
    names = [c[0] for c in result.description]
    def native(v):
        if isinstance(v, (date, datetime)): return v.isoformat()
        if isinstance(v, Decimal): return float(v)
        return v
    return [dict(zip(names, map(native, row))) for row in result.fetchall()]

def prepare(con, filename, stop_before=None):
    """Only trusted, repo-owned SQL; temporary aggregates die with connection.

    Skip display-only SELECTs; prohibit persistent DDL/DML even if source changes.
    Validate all statements before executing any of them.
    """
    text = (ROOT / 'sql' / filename).read_text(encoding='utf-8')
    statements = []
    for statement in con.extract_statements(text):
        q = re.sub(r'--[^\n]*', '', statement.query).strip()
        if q.upper().startswith('DROP '): break
        if stop_before and stop_before in q: break
        if not (re.match(r'^CREATE OR REPLACE TEMP (TABLE|VIEW) \w+ AS\b', q, re.I)
                or re.match(r'^SET TimeZone\s*=', q, re.I)
                or statement.type == duckdb.StatementType.SELECT):
            raise ValueError(f"Disallowed statement in trusted module {filename}")
        statements.append((statement, q))
    for statement, q in statements:
        if statement.type != duckdb.StatementType.SELECT:
            con.execute(statement)

def metadata(*sources):
    return {
        'database': 'data/processed/ecommerce.duckdb',
        'table': 'user_behavior_clean', 'read_only': True,
        'observation_window': ['2017-11-25', '2017-12-03'],
        'timezone': 'Asia/Shanghai', 'rate_unit': 'percent (0–100)',
        'sources': list(sources),
        'source_sha256': {s: hashlib.sha256((ROOT/s).read_bytes()).hexdigest() for s in sources},
    }
