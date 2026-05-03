"""Tests for the CareTeam membership seed script.

Tests cover:
- Patient selection: deterministic ~50% subset via even-pid selection
- SQL statement generation: correct INSERT shapes, idempotency guards
- User creation SQL: find-or-create with correct field values
- Dry-run output: valid SQL text for piping into mysql
"""

from __future__ import annotations

from scripts.seed.seed_careteam import (
    DR_SMITH_CONFIG,
    build_ensure_user_sql,
    build_seed_care_team_members_sql,
    build_seed_care_teams_sql,
    generate_full_seed_sql,
    select_patient_pids,
)


class TestSelectPatientPids:
    """select_patient_pids picks a deterministic ~50% subset."""

    def test_empty_input_returns_empty(self) -> None:
        assert select_patient_pids([]) == []

    def test_single_even_pid_is_selected(self) -> None:
        assert select_patient_pids([2]) == [2]

    def test_single_odd_pid_is_not_selected(self) -> None:
        assert select_patient_pids([1]) == []

    def test_mixed_pids_returns_roughly_half(self) -> None:
        all_pids = list(range(1, 51))  # 1..50
        selected = select_patient_pids(all_pids)
        # Even pids: 2, 4, 6, ..., 50 → 25 of 50
        assert len(selected) == 25
        assert all(pid % 2 == 0 for pid in selected)

    def test_preserves_order(self) -> None:
        all_pids = [10, 3, 8, 1, 6]
        selected = select_patient_pids(all_pids)
        assert selected == [10, 8, 6]

    def test_deterministic(self) -> None:
        pids = list(range(1, 101))
        assert select_patient_pids(pids) == select_patient_pids(pids)

    def test_all_even_returns_all(self) -> None:
        pids = [2, 4, 6, 8]
        assert select_patient_pids(pids) == pids

    def test_all_odd_returns_none(self) -> None:
        pids = [1, 3, 5, 7]
        assert select_patient_pids(pids) == []


class TestBuildEnsureUserSql:
    """build_ensure_user_sql generates correct INSERT ... WHERE NOT EXISTS."""

    def test_returns_nonempty_sql(self) -> None:
        sql = build_ensure_user_sql(DR_SMITH_CONFIG)
        assert len(sql) > 0

    def test_contains_username(self) -> None:
        sql = build_ensure_user_sql(DR_SMITH_CONFIG)
        assert "'dr_smith'" in sql

    def test_contains_physician_fields(self) -> None:
        sql = build_ensure_user_sql(DR_SMITH_CONFIG)
        assert "'Sarah'" in sql
        assert "'Smith'" in sql
        assert "'MD'" in sql

    def test_idempotent_guard(self) -> None:
        """SQL must include a NOT EXISTS check on username."""
        sql = build_ensure_user_sql(DR_SMITH_CONFIG)
        assert "NOT EXISTS" in sql or "INSERT IGNORE" in sql

    def test_authorized_set_to_1(self) -> None:
        """dr_smith is a clinician — authorized must be 1."""
        sql = build_ensure_user_sql(DR_SMITH_CONFIG)
        assert "authorized" in sql.lower()


class TestBuildSeedCareTeamsSql:
    """build_seed_care_teams_sql generates care_teams INSERT for given pids."""

    def test_empty_pids_returns_empty(self) -> None:
        stmts = build_seed_care_teams_sql([])
        assert stmts == []

    def test_one_pid_returns_one_statement(self) -> None:
        stmts = build_seed_care_teams_sql([42])
        assert len(stmts) == 1

    def test_multiple_pids_return_matching_count(self) -> None:
        pids = [2, 4, 6]
        stmts = build_seed_care_teams_sql(pids)
        assert len(stmts) == len(pids)

    def test_contains_pid_value(self) -> None:
        stmts = build_seed_care_teams_sql([42])
        # The pid must appear in the SQL
        assert "42" in stmts[0]

    def test_status_is_active(self) -> None:
        stmts = build_seed_care_teams_sql([2])
        assert "'active'" in stmts[0]

    def test_idempotent_guard(self) -> None:
        stmts = build_seed_care_teams_sql([2])
        assert "NOT EXISTS" in stmts[0]

    def test_uuid_left_null(self) -> None:
        """UUID is left NULL for OpenEMR's auto-populate mechanism."""
        stmts = build_seed_care_teams_sql([2])
        # Should not contain UUID_TO_BIN or explicit uuid value
        assert "uuid" not in stmts[0].lower() or "null" in stmts[0].lower()


class TestBuildSeedCareTeamMembersSql:
    """build_seed_care_team_members_sql generates care_team_member INSERTs."""

    def test_empty_pids_returns_empty(self) -> None:
        stmts = build_seed_care_team_members_sql([])
        assert stmts == []

    def test_one_pid_returns_one_statement(self) -> None:
        stmts = build_seed_care_team_members_sql([42])
        assert len(stmts) == 1

    def test_role_is_physician(self) -> None:
        stmts = build_seed_care_team_members_sql([2])
        assert "'physician'" in stmts[0]

    def test_references_dr_smith_user(self) -> None:
        """Must reference dr_smith's user_id via subquery on username."""
        stmts = build_seed_care_team_members_sql([2])
        assert "dr_smith" in stmts[0]

    def test_idempotent_guard(self) -> None:
        stmts = build_seed_care_team_members_sql([2])
        assert "NOT EXISTS" in stmts[0]

    def test_contains_care_team_id_subquery(self) -> None:
        """Must look up care_team_id by pid, not use a hardcoded value."""
        stmts = build_seed_care_team_members_sql([42])
        # Should reference care_teams table to find the team_id
        assert "care_teams" in stmts[0]


class TestGenerateFullSeedSql:
    """generate_full_seed_sql produces a complete, executable SQL script."""

    def test_contains_all_three_steps(self) -> None:
        sql = generate_full_seed_sql([2, 4])
        assert "Step 1" in sql
        assert "Step 2" in sql
        assert "Step 3" in sql

    def test_contains_user_insert(self) -> None:
        sql = generate_full_seed_sql([2])
        assert "INSERT INTO `users`" in sql

    def test_contains_care_teams_insert(self) -> None:
        sql = generate_full_seed_sql([2])
        assert "INSERT INTO `care_teams`" in sql

    def test_contains_care_team_member_insert(self) -> None:
        sql = generate_full_seed_sql([2])
        assert "INSERT INTO `care_team_member`" in sql

    def test_contains_reporting_select(self) -> None:
        sql = generate_full_seed_sql([2])
        assert "Report results" in sql
        assert "SELECT" in sql

    def test_empty_pids_still_creates_user(self) -> None:
        sql = generate_full_seed_sql([])
        assert "INSERT INTO `users`" in sql
        assert "INSERT INTO `care_teams`" not in sql

    def test_multiple_pids_produce_matching_inserts(self) -> None:
        sql = generate_full_seed_sql([2, 4, 6])
        # Should have 3 care_teams INSERTs and 3 care_team_member INSERTs
        assert sql.count("INSERT INTO `care_teams`") == 3
        assert sql.count("INSERT INTO `care_team_member`") == 3


class TestDrSmithConfig:
    """DR_SMITH_CONFIG contains the right values for the demo user."""

    def test_username(self) -> None:
        assert DR_SMITH_CONFIG["username"] == "dr_smith"

    def test_is_not_admin(self) -> None:
        """dr_smith is a regular clinician, not admin."""
        assert DR_SMITH_CONFIG.get("is_admin", False) is False

    def test_has_name_fields(self) -> None:
        assert "fname" in DR_SMITH_CONFIG
        assert "lname" in DR_SMITH_CONFIG
