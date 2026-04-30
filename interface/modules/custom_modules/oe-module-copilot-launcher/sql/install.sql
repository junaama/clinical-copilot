--
-- Co-Pilot Launcher Module — Install
--
-- Idempotent: safe to re-run. Uses #IfNotTable directives parsed by OpenEMR's
-- module installer (see sql/4_2_0-to-4_2_1_upgrade.sql for reference).
--
-- @package   OpenEMR
-- @link      https://www.open-emr.org
-- @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
--

-- ============================================================================
-- agent_audit — one row per agent decision (per ARCHITECTURE.md §9 step 11)
-- ============================================================================

#IfNotTable agent_audit
CREATE TABLE `agent_audit` (
    `id` bigint(20) NOT NULL AUTO_INCREMENT,
    `conversation_id` varchar(64) NOT NULL,
    `turn` int NOT NULL,
    `pid` bigint(20) NOT NULL,
    `user_id` bigint(20) NOT NULL,
    `workflow_id` varchar(32) DEFAULT NULL,
    `classifier_confidence` decimal(4,3) DEFAULT NULL,
    `decision` varchar(48) NOT NULL COMMENT 'allow | denied_authz | blocked_verification | refused_safety | tool_failure',
    `escalation_reason` varchar(255) DEFAULT NULL,
    `model` varchar(64) DEFAULT NULL,
    `tokens_in` int DEFAULT NULL,
    `tokens_out` int DEFAULT NULL,
    `latency_ms` int DEFAULT NULL,
    `cost_usd` decimal(10,6) DEFAULT NULL,
    `break_glass` tinyint(1) NOT NULL DEFAULT 0,
    `ip_address` varchar(100) DEFAULT NULL,
    `created_time` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    KEY `idx_conversation` (`conversation_id`),
    KEY `idx_pid_created` (`pid`, `created_time`),
    KEY `idx_decision` (`decision`)
) ENGINE=InnoDB COMMENT='Per-turn agent decision audit log for the Clinical Co-Pilot';
#EndIf

-- ============================================================================
-- SMART client registration — idempotent. The actual scope/secret values are
-- written by Service\CopilotClientRegistration on module enable; this row
-- exists only as a stub so the FK-style references resolve immediately.
-- ============================================================================

INSERT IGNORE INTO `oauth_clients` (
    `client_id`, `client_role`, `client_name`,
    `client_secret`, `redirect_uri`, `grant_types`, `scope`,
    `is_confidential`, `is_enabled`, `skip_ehr_launch_authorization_flow`,
    `register_date`
) VALUES (
    'copilot-launcher', 'patient', 'Clinical Co-Pilot',
    '', 'http://localhost:5173/callback', 'authorization_code',
    'openid fhirUser launch launch/patient patient/Observation.read patient/Condition.read patient/MedicationRequest.read patient/MedicationAdministration.read patient/Encounter.read patient/Patient.read patient/AllergyIntolerance.read patient/DocumentReference.read patient/DiagnosticReport.read patient/ServiceRequest.read',
    1, 0, 0,
    NOW()
);
