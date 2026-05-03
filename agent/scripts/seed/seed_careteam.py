"""Seed CareTeam memberships for dr_smith in a real OpenEMR instance.

Creates a non-admin ``dr_smith`` clinician user and assigns them to roughly
half of the patient population via ``care_teams`` + ``care_team_member`` rows.
Idempotent: safe to run multiple times.

The UUID columns are left ``NULL`` on new rows.  OpenEMR's
``UuidRegistry::createMissingUuidsForTables`` auto-populates them on the
first FHIR query, so the FHIR ``CareTeam`` resources become visible
without manual UUID wiring.

Usage::

    # Print SQL to stdout (for piping into mysql on the OpenEMR container)
    cd agent && uv run python scripts/seed/seed_careteam.py --dry-run

    # Execute directly (requires pymysql — install via seed extras)
    MYSQL_HOST=127.0.0.1 MYSQL_PORT=3306 MYSQL_USER=root \\
    MYSQL_PASSWORD=root MYSQL_DATABASE=openemr \\
        uv run --extra seed python scripts/seed/seed_careteam.py

    # Via Railway (generate SQL locally, pipe into the container's mysql)
    cd agent && uv run python scripts/seed/seed_careteam.py --dry-run | \\
        railway ssh --service openemr 'mysql -u root openemr'
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

# ---------------------------------------------------------------------------
# dr_smith user configuration
# ---------------------------------------------------------------------------

DR_SMITH_CONFIG: dict[str, Any] = {
    "username": "dr_smith",
    "fname": "Sarah",
    "lname": "Smith",
    "title": "MD",
    "specialty": "Internal Medicine",
    "npi": "1234567890",
    "authorized": 1,
    "active": 1,
    "is_admin": False,
}


# ---------------------------------------------------------------------------
# Pure functions — testable without a database
# ---------------------------------------------------------------------------


def select_patient_pids(all_pids: list[int]) -> list[int]:
    """Select roughly half of the patient pids for CareTeam assignment.

    Uses even-pid selection: deterministic, reproducible, and stable across
    re-runs.  Returns the selected pids in the same relative order as the
    input.
    """
    return [pid for pid in all_pids if pid % 2 == 0]


def build_ensure_user_sql(config: dict[str, Any]) -> str:
    """Return an idempotent INSERT for the dr_smith users row.

    Uses ``INSERT ... SELECT ... WHERE NOT EXISTS`` so re-running is a no-op
    when the user already exists.
    """
    return (
        "INSERT INTO `users` "
        "(`username`, `fname`, `lname`, `title`, `specialty`, `npi`, "
        "`authorized`, `active`) "
        "SELECT "
        f"'{config['username']}', '{config['fname']}', '{config['lname']}', "
        f"'{config['title']}', '{config['specialty']}', '{config['npi']}', "
        f"{config['authorized']}, {config['active']} "
        "FROM DUAL "
        "WHERE NOT EXISTS ("
        f"SELECT 1 FROM `users` WHERE `username` = '{config['username']}'"
        ");"
    )


def build_seed_care_teams_sql(patient_pids: list[int]) -> list[str]:
    """Return one INSERT per patient pid for the ``care_teams`` table.

    Each INSERT is guarded by ``WHERE NOT EXISTS`` on ``(pid, created_by)``
    so re-running is idempotent.  The ``uuid`` column is left ``NULL`` for
    OpenEMR's auto-populate mechanism.  ``created_by`` is set to dr_smith's
    ``users.id`` via subquery so we don't hardcode a numeric id.
    """
    stmts: list[str] = []
    for pid in patient_pids:
        stmt = (
            "INSERT INTO `care_teams` "
            "(`pid`, `status`, `team_name`, `created_by`) "
            "SELECT "
            f"{pid}, 'active', "
            f"(SELECT CONCAT('Care Team - ', fname, ' ', lname) "
            f"FROM `patient_data` WHERE `pid` = {pid}), "
            "(SELECT `id` FROM `users` WHERE `username` = 'dr_smith' LIMIT 1) "
            "FROM DUAL "
            "WHERE NOT EXISTS ("
            "SELECT 1 FROM `care_teams` ct "
            f"WHERE ct.`pid` = {pid} "
            "AND ct.`created_by` = "
            "(SELECT `id` FROM `users` WHERE `username` = 'dr_smith' LIMIT 1)"
            ");"
        )
        stmts.append(stmt)
    return stmts


def build_seed_care_team_members_sql(patient_pids: list[int]) -> list[str]:
    """Return one INSERT per patient pid for ``care_team_member``.

    Links dr_smith (by ``users.id`` subquery) to the ``care_teams`` row for
    each patient pid.  Guarded by ``WHERE NOT EXISTS`` on
    ``(care_team_id, user_id)`` for idempotency.
    """
    stmts: list[str] = []
    for pid in patient_pids:
        stmt = (
            "INSERT INTO `care_team_member` "
            "(`care_team_id`, `user_id`, `role`, `status`) "
            "SELECT "
            f"ct.`id`, "
            "(SELECT `id` FROM `users` WHERE `username` = 'dr_smith' LIMIT 1), "
            "'physician', 'active' "
            f"FROM `care_teams` ct "
            f"WHERE ct.`pid` = {pid} "
            "AND ct.`created_by` = "
            "(SELECT `id` FROM `users` WHERE `username` = 'dr_smith' LIMIT 1) "
            "AND NOT EXISTS ("
            "SELECT 1 FROM `care_team_member` ctm "
            "WHERE ctm.`care_team_id` = ct.`id` "
            "AND ctm.`user_id` = "
            "(SELECT `id` FROM `users` WHERE `username` = 'dr_smith' LIMIT 1)"
            ") "
            "LIMIT 1;"
        )
        stmts.append(stmt)
    return stmts


def generate_full_seed_sql(patient_pids: list[int]) -> str:
    """Generate the complete SQL seed script as a string.

    Includes: user creation, care_teams, care_team_members, and a
    trailing SELECT to report results.
    """
    lines: list[str] = [
        "-- CareTeam seed for dr_smith",
        "-- Generated by agent/scripts/seed/seed_careteam.py",
        "-- Idempotent: safe to run multiple times.",
        "",
        "-- Step 1: Ensure dr_smith user exists",
        build_ensure_user_sql(DR_SMITH_CONFIG),
        "",
        f"-- Step 2: Create care_teams for {len(patient_pids)} patients",
    ]
    for stmt in build_seed_care_teams_sql(patient_pids):
        lines.append(stmt)

    lines.append("")
    lines.append("-- Step 3: Add dr_smith as physician member on each team")
    for stmt in build_seed_care_team_members_sql(patient_pids):
        lines.append(stmt)

    lines.append("")
    lines.append("-- Step 4: Report results")
    lines.append(
        "SELECT "
        "'dr_smith user_id' AS label, id AS value FROM users "
        "WHERE username = 'dr_smith' "
        "UNION ALL "
        "SELECT 'care_teams created', COUNT(*) FROM care_teams "
        "WHERE created_by = (SELECT id FROM users WHERE username = 'dr_smith') "
        "UNION ALL "
        "SELECT 'care_team_members created', COUNT(*) FROM care_team_member "
        "WHERE user_id = (SELECT id FROM users WHERE username = 'dr_smith');"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _execute_sql(sql: str) -> None:
    """Execute the seed SQL via pymysql.

    Requires MYSQL_HOST, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE env vars.
    MYSQL_PORT defaults to 3306.
    """
    try:
        import pymysql  # type: ignore[import-untyped]
    except ImportError:
        print(
            "ERROR: pymysql not installed. "
            "Install with: uv pip install pymysql\n"
            "Or use --dry-run to print SQL for piping into mysql.",
            file=sys.stderr,
        )
        sys.exit(1)

    conn = pymysql.connect(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ.get("MYSQL_PORT", "3306")),
        user=os.environ["MYSQL_USER"],
        password=os.environ.get("MYSQL_PASSWORD", ""),
        database=os.environ["MYSQL_DATABASE"],
        autocommit=True,
    )
    try:
        with conn.cursor() as cursor:
            # Split on semicolons and execute each non-empty statement.
            for stmt in sql.split(";"):
                stmt = stmt.strip()
                if not stmt or stmt.startswith("--"):
                    continue
                cursor.execute(stmt)
                # Print SELECT results if any.
                if stmt.upper().lstrip().startswith("SELECT"):
                    rows = cursor.fetchall()
                    for row in rows:
                        print(f"  {row[0]}: {row[1]}")
        print("Seed completed successfully.")
    finally:
        conn.close()


def _fetch_patient_pids_from_db() -> list[int]:
    """Fetch all patient pids from the database via pymysql."""
    try:
        import pymysql  # type: ignore[import-untyped]
    except ImportError:
        print(
            "ERROR: pymysql not installed for live pid fetch. "
            "Use --pids to provide pids manually, or install pymysql.",
            file=sys.stderr,
        )
        sys.exit(1)

    conn = pymysql.connect(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ.get("MYSQL_PORT", "3306")),
        user=os.environ["MYSQL_USER"],
        password=os.environ.get("MYSQL_PASSWORD", ""),
        database=os.environ["MYSQL_DATABASE"],
    )
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT pid FROM patient_data ORDER BY pid")
            return [row[0] for row in cursor.fetchall()]
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Seed CareTeam memberships for dr_smith in OpenEMR."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print SQL to stdout instead of executing against the database.",
    )
    parser.add_argument(
        "--pids",
        type=str,
        default=None,
        help=(
            "Comma-separated patient pids to use instead of querying the DB. "
            "Example: --pids 1,2,3,4,5,6,7,8,9,10"
        ),
    )
    args = parser.parse_args()

    # Resolve patient pids.
    if args.pids:
        all_pids = [int(p.strip()) for p in args.pids.split(",") if p.strip()]
    elif args.dry_run:
        # Dry-run without --pids: use a placeholder range.
        print(
            "-- NOTE: No --pids supplied and no DB connection in dry-run mode.",
            file=sys.stderr,
        )
        print(
            "-- Using placeholder pids 1..50. Re-run with --pids for real data.",
            file=sys.stderr,
        )
        all_pids = list(range(1, 51))
    else:
        all_pids = _fetch_patient_pids_from_db()

    selected = select_patient_pids(all_pids)
    sql = generate_full_seed_sql(selected)

    if args.dry_run:
        print(sql)
        print(
            f"\n-- {len(selected)} of {len(all_pids)} patients selected for dr_smith.",
            file=sys.stderr,
        )
        return 0

    print(f"Seeding dr_smith on {len(selected)} of {len(all_pids)} patients...")
    _execute_sql(sql)
    return 0


if __name__ == "__main__":
    sys.exit(main())
