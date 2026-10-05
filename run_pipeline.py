"""Run numbered SQL modules; existing databases should use --skip-build."""
import argparse
import os
from pathlib import Path
import sys
from time import perf_counter
from datetime import datetime

import duckdb

ROOT = Path(__file__).resolve().parent


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--skip-build', action='store_true', help='Skip destructive SQL 01 rebuild')
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument('--start', type=int, choices=range(1, 10), help='Start at this module')
    selection.add_argument('--only', type=int, choices=range(1, 10), help='Run only this module')
    return parser.parse_args()


def fail_preflight(message):
    print(f'FAILED: {message}', file=sys.stderr)
    print('Pipeline summary: FAILED preflight; no SQL executed')
    return 1


def main():
    args = parse_args()
    modules = []
    for number in range(1, 10):
        if args.only is not None and number != args.only:
            continue
        if args.start is not None and number < args.start:
            continue
        if args.skip_build and number == 1:
            continue
        matches = list((ROOT / 'sql').glob(f'{number:02d}_*.sql'))
        if len(matches) != 1:
            return fail_preflight(f'expected exactly one SQL module {number:02d}')
        modules.append(matches[0])
    if not modules:
        return fail_preflight('no modules selected')
    # CSV is required for building and SQL 02 raw-view checks, but not SQL 03–09.
    raw_exists = (ROOT / 'data/raw/UserBehavior.csv').is_file()
    print(f'Raw CSV: {"FOUND" if raw_exists else "MISSING"}')
    if not raw_exists and any(p.name.startswith(('01_', '02_')) for p in modules):
        return fail_preflight('selected modules require data/raw/UserBehavior.csv')
    processed = ROOT / 'data/processed'
    processed.mkdir(parents=True, exist_ok=True)
    database = processed / 'ecommerce.duckdb'
    building = any(p.name.startswith('01_') for p in modules)
    if not building and not database.is_file():
        return fail_preflight('database missing; build with SQL 01 first')
    if building:
        print('BUILD: SQL 01 will rebuild user_behavior_clean; use --skip-build for existing data.')
    results = []
    started = perf_counter()
    previous_directory = Path.cwd()
    con = None
    try:
        # SQL 01 raw-view paths are relative to the repository root.
        os.chdir(ROOT)
        con = duckdb.connect(str(database), read_only=not building)
        con.execute("SET memory_limit='3GB'")
        con.execute('SET threads=4')
        for path in modules:
            begin = perf_counter()
            print(f'\nRUNNING {path.name} | {datetime.now().isoformat(timespec="seconds")}', flush=True)
            try:
                for index, statement in enumerate(con.extract_statements(path.read_text()), 1):
                    result = con.execute(statement)
                    if statement.type == duckdb.StatementType.SELECT:
                        columns = [column[0] for column in result.description]
                        rows = result.fetchall() if 'invalid_rows' in columns else result.fetchmany(20)
                        print(f'  SELECT {index}: {columns}')
                        for row in rows:
                            print(f'    {row}')
                        if 'invalid_rows' in columns:
                            position = columns.index('invalid_rows')
                            if any(row[position] is None or row[position] != 0 for row in rows):
                                raise ValueError(f'Quality checks failed: {rows}')
                        else:
                            print('  (preview limited to 20 rows)')
            except Exception as exc:
                results.append((path.name, 'FAILED', perf_counter() - begin))
                print(f'FAILED {path.name}: {type(exc).__name__}: {exc}', file=sys.stderr, flush=True)
                return 1
            results.append((path.name, 'SUCCESS', perf_counter() - begin))
            print(f'SUCCESS {path.name} | {results[-1][2]:.2f}s', flush=True)
        return 0
    except Exception as exc:
        print(f'FAILED pipeline: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 1
    finally:
        if con is not None:
            con.close()
        os.chdir(previous_directory)
        print('\nPipeline summary')
        for name, status, elapsed in results:
            print(f'{name}: {status} ({elapsed:.2f}s)')
        print(f'Completed {len(results)}/{len(modules)} selected modules; elapsed {perf_counter()-started:.2f}s')
        pending = [p.name for p in modules[len(results):]]
        if pending:
            print(f'NOT RUN: {", ".join(pending)}')


if __name__ == '__main__':
    sys.exit(main())
