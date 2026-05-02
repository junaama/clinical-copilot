<?php

/**
 * Idempotent registration of the Co-Pilot SMART clients in oauth_clients.
 *
 * Two clients are registered:
 *
 * - `copilot-launcher` — the EHR-launch client used by the chart-sidebar embed.
 *   client_role='patient', redirect URI under copilot-ui (`/callback`),
 *   patient-scoped scopes. Secret mirrored into globals.copilot_oauth_client_secret.
 *
 * - `copilot-standalone` — the standalone-launch client used by the full-screen
 *   Co-Pilot portal. client_role='user', redirect URI under the agent backend
 *   (`/auth/smart/callback`), user-scoped scopes. Secret mirrored into
 *   globals.copilot_oauth_standalone_client_secret.
 *
 * For each client:
 * - If the row already exists with a non-empty client_secret, no-op.
 * - If the row exists but has no secret yet, generate one and persist it
 *   in both oauth_clients.client_secret and the matching globals key so the
 *   agent backend can read it.
 * - If the row does not exist, insert it.
 *
 * Designed so callers don't have to know the difference; everything funnels
 * through ensureRegistered().
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\CopilotLauncher\Service;

use Psr\Log\LoggerInterface;

final readonly class CopilotClientRegistration
{
    public const LAUNCHER_CLIENT_ID = 'copilot-launcher';
    public const LAUNCHER_CLIENT_NAME = 'Clinical Co-Pilot';
    public const LAUNCHER_SECRET_GLOBAL = 'copilot_oauth_client_secret';

    public const STANDALONE_CLIENT_ID = 'copilot-standalone';
    public const STANDALONE_CLIENT_NAME = 'Clinical Co-Pilot (Standalone)';
    public const STANDALONE_SECRET_GLOBAL = 'copilot_oauth_standalone_client_secret';

    /** @deprecated Use {@see LAUNCHER_CLIENT_ID}. Retained for backwards compatibility. */
    public const CLIENT_ID = self::LAUNCHER_CLIENT_ID;
    /** @deprecated Use {@see LAUNCHER_CLIENT_NAME}. Retained for backwards compatibility. */
    public const CLIENT_NAME = self::LAUNCHER_CLIENT_NAME;

    /**
     * @param \Closure():string $secretGenerator Returns a fresh random secret
     *        when none exists. Injected so tests can pin the value.
     */
    public function __construct(
        private DatabaseExecutor $db,
        private LoggerInterface $logger,
        private string $copilotAppUrl,
        private string $agentBackendUrl,
        private \Closure $secretGenerator,
    ) {
    }

    public function ensureRegistered(): void
    {
        $this->registerClient(
            clientId: self::LAUNCHER_CLIENT_ID,
            clientName: self::LAUNCHER_CLIENT_NAME,
            clientRole: 'patient',
            redirectUri: rtrim($this->copilotAppUrl, '/') . '/callback',
            scope: self::scopeString(),
            globalKey: self::LAUNCHER_SECRET_GLOBAL,
        );

        $this->registerClient(
            clientId: self::STANDALONE_CLIENT_ID,
            clientName: self::STANDALONE_CLIENT_NAME,
            clientRole: 'user',
            redirectUri: rtrim($this->agentBackendUrl, '/') . '/auth/smart/callback',
            scope: self::scopeStringStandalone(),
            globalKey: self::STANDALONE_SECRET_GLOBAL,
        );
    }

    private function registerClient(
        string $clientId,
        string $clientName,
        string $clientRole,
        string $redirectUri,
        string $scope,
        string $globalKey,
    ): void {
        $existing = $this->db->fetchRow(
            'SELECT `client_id`, `client_secret` FROM `oauth_clients` WHERE `client_id` = ?',
            [$clientId]
        );

        if ($existing !== null && (string) ($existing['client_secret'] ?? '') !== '') {
            $this->logger->info('Co-Pilot SMART client already registered', [
                'client_id' => $clientId,
            ]);
            return;
        }

        $secret = ($this->secretGenerator)();
        if ($secret === '') {
            throw new \DomainException('secretGenerator must return a non-empty string');
        }

        if ($existing === null) {
            $this->db->execute(
                'INSERT INTO `oauth_clients` ('
                . '`client_id`,`client_role`,`client_name`,`client_secret`,`redirect_uri`,'
                . '`grant_types`,`scope`,`is_confidential`,`is_enabled`,'
                . '`skip_ehr_launch_authorization_flow`,`register_date`'
                . ') VALUES (?,?,?,?,?,?,?,?,?,?,NOW())',
                [
                    $clientId,
                    $clientRole,
                    $clientName,
                    $secret,
                    $redirectUri,
                    'authorization_code',
                    $scope,
                    1,
                    1,
                    0,
                ]
            );
            $this->logger->info('Co-Pilot SMART client inserted', ['client_id' => $clientId]);
        } else {
            $this->db->execute(
                'UPDATE `oauth_clients` SET `client_secret` = ?, `redirect_uri` = ?, '
                . '`scope` = ?, `is_enabled` = 1 WHERE `client_id` = ?',
                [$secret, $redirectUri, $scope, $clientId]
            );
            $this->logger->info('Co-Pilot SMART client secret rotated', ['client_id' => $clientId]);
        }

        // Mirror the secret into globals so the agent backend can read it.
        $this->db->execute(
            'INSERT INTO `globals` (`gl_name`, `gl_index`, `gl_value`) VALUES (?, 0, ?) '
            . 'ON DUPLICATE KEY UPDATE `gl_value` = VALUES(`gl_value`)',
            [$globalKey, $secret]
        );
    }

    public static function scopeString(): string
    {
        return implode(' ', [
            'openid',
            'fhirUser',
            'launch',
            'launch/patient',
            'patient/Observation.read',
            'patient/Condition.read',
            'patient/MedicationRequest.read',
            'patient/MedicationAdministration.read',
            'patient/Encounter.read',
            'patient/Patient.read',
            'patient/AllergyIntolerance.read',
            'patient/DocumentReference.read',
            'patient/DiagnosticReport.read',
            'patient/ServiceRequest.read',
        ]);
    }

    public static function scopeStringStandalone(): string
    {
        return implode(' ', [
            'openid',
            'fhirUser',
            'launch/user',
            'offline_access',
            'user/Patient.rs',
            'user/Observation.rs',
            'user/Condition.rs',
            'user/MedicationRequest.rs',
            'user/MedicationAdministration.rs',
            'user/Encounter.rs',
            'user/AllergyIntolerance.rs',
            'user/DocumentReference.rs',
            'user/DiagnosticReport.rs',
            'user/ServiceRequest.rs',
            'user/CareTeam.rs',
            'user/Practitioner.rs',
        ]);
    }
}
