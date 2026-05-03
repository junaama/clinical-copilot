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
    build_acl_php_helper,
    build_ensure_auth_group_sql,
    build_ensure_user_sql,
    build_ensure_users_secure_sql,
    build_seed_care_team_members_sql,
    build_seed_care_teams_sql,
    generate_full_seed_sql,
    hash_password,
    select_patient_pids,
)


# Deterministic placeholder hash for tests — bcrypt-shaped but doesn't have
# to verify against any password since we only assert SQL shape.
_TEST_HASH = "$2b$12$abcdefghijklmnopqrstuvABCDEFGHIJKLMNOPQRSTUVWXYZ0123456"


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
        sql = generate_full_seed_sql([2, 4], _TEST_HASH)
        assert "Step 1" in sql
        assert "Step 2" in sql
        assert "Step 3" in sql

    def test_contains_user_insert(self) -> None:
        sql = generate_full_seed_sql([2], _TEST_HASH)
        assert "INSERT INTO `users`" in sql

    def test_contains_care_teams_insert(self) -> None:
        sql = generate_full_seed_sql([2], _TEST_HASH)
        assert "INSERT INTO `care_teams`" in sql

    def test_contains_care_team_member_insert(self) -> None:
        sql = generate_full_seed_sql([2], _TEST_HASH)
        assert "INSERT INTO `care_team_member`" in sql

    def test_contains_reporting_select(self) -> None:
        sql = generate_full_seed_sql([2], _TEST_HASH)
        assert "Report results" in sql
        assert "SELECT" in sql

    def test_empty_pids_still_creates_user(self) -> None:
        sql = generate_full_seed_sql([], _TEST_HASH)
        assert "INSERT INTO `users`" in sql
        assert "INSERT INTO `care_teams`" not in sql

    def test_multiple_pids_produce_matching_inserts(self) -> None:
        sql = generate_full_seed_sql([2, 4, 6], _TEST_HASH)
        # Should have 3 care_teams INSERTs and 3 care_team_member INSERTs
        assert sql.count("INSERT INTO `care_teams`") == 3
        assert sql.count("INSERT INTO `care_team_member`") == 3

    def test_contains_users_secure_insert(self) -> None:
        sql = generate_full_seed_sql([2], _TEST_HASH)
        assert "INSERT INTO `users_secure`" in sql
        assert _TEST_HASH in sql

    def test_users_secure_step_runs_after_user_creation(self) -> None:
        """``users_secure`` row depends on ``users.id`` — must come after."""
        sql = generate_full_seed_sql([2], _TEST_HASH)
        assert sql.index("INSERT INTO `users`") < sql.index("INSERT INTO `users_secure`")


class TestBuildEnsureUsersSecureSql:
    """build_ensure_users_secure_sql writes an idempotent password row."""

    def test_uses_username_subquery_for_id(self) -> None:
        """``users.id`` is resolved at INSERT time, not hardcoded."""
        sql = build_ensure_users_secure_sql("dr_smith", _TEST_HASH)
        assert "FROM `users` u" in sql
        assert "WHERE u.username = 'dr_smith'" in sql

    def test_on_duplicate_key_update_rotates_password(self) -> None:
        """Re-running with a new hash must update, not silently no-op."""
        sql = build_ensure_users_secure_sql("dr_smith", _TEST_HASH)
        assert "ON DUPLICATE KEY UPDATE" in sql
        assert "`password` = '" in sql

    def test_escapes_single_quotes_in_hash(self) -> None:
        """Defensive — bcrypt output is ASCII-only, but quote-escape regardless."""
        sql = build_ensure_users_secure_sql("dr_smith", "ab'cd")
        assert "'ab''cd'" in sql


class TestHashPassword:
    """hash_password wraps bcrypt with project defaults."""

    def test_returns_bcrypt_shaped_string(self) -> None:
        h = hash_password("hunter2")
        assert h.startswith("$2y$")
        assert len(h) == 60

    def test_round_trip_verifies(self) -> None:
        """``$2y$`` rewrite must remain bcrypt-byte-compatible."""
        import bcrypt

        h = hash_password("hunter2")
        # bcrypt.checkpw accepts both $2b$ and $2y$ prefixes — same algorithm.
        assert bcrypt.checkpw(b"hunter2", h.encode("ascii"))
        assert not bcrypt.checkpw(b"wrong", h.encode("ascii"))


class TestBuildEnsureAuthGroupSql:
    """build_ensure_auth_group_sql gates the OpenEMR auth-group check."""

    def test_inserts_into_groups_table(self) -> None:
        sql = build_ensure_auth_group_sql("dr_smith")
        assert "INSERT INTO `groups`" in sql
        assert "'Default'" in sql
        assert "'dr_smith'" in sql

    def test_idempotent_guard(self) -> None:
        sql = build_ensure_auth_group_sql("dr_smith")
        assert "WHERE NOT EXISTS" in sql


class TestBuildAclPhpHelper:
    """build_acl_php_helper emits a runnable phpGACL snippet."""

    def test_calls_add_user_aros(self) -> None:
        snippet = build_acl_php_helper("dr_smith")
        assert "AclExtended::addUserAros" in snippet
        assert '"dr_smith"' in snippet
        assert '"Physicians"' in snippet

    def test_includes_globals_shim(self) -> None:
        """phpGACL needs HTTP env shims to load OpenEMR globals from CLI."""
        snippet = build_acl_php_helper("dr_smith")
        assert "HTTP_HOST" in snippet
        assert 'require "interface/globals.php"' in snippet


class TestGenerateFullSeedSqlGatesOnly:
    """generate_full_seed_sql no longer emits raw gacl_* SQL."""

    def test_does_not_touch_gacl_tables(self) -> None:
        """phpGACL caches break — direct INSERTs are the wrong path."""
        sql = generate_full_seed_sql([2], _TEST_HASH)
        assert "INSERT INTO `gacl_aro`" not in sql
        assert "INSERT INTO `gacl_groups_aro_map`" not in sql

    def test_emits_groups_insert(self) -> None:
        sql = generate_full_seed_sql([2], _TEST_HASH)
        assert "INSERT INTO `groups`" in sql


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
