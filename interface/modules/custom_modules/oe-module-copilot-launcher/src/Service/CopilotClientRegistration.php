<?php

/**
 * Idempotent registration of the Co-Pilot SMART client in oauth_clients.
 *
 * - If the row already exists with a non-empty client_secret, no-op.
 * - If the row exists but has no secret yet, generate one and persist it
 *   in both oauth_clients.client_secret AND globals.copilot_oauth_client_secret
 *   so the agent backend can read it.
 * - If the row does not exist, insert it.
 *
 * Designed so callers don't have to know the difference; everything funnels
 * through ensureRegistered() which returns the public client id on success.
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
    public const CLIENT_ID = 'copilot-launcher';
    public const CLIENT_NAME = 'Clinical Co-Pilot';

    /**
     * @param \Closure():string $secretGenerator Returns a fresh random secret
     *        when none exists. Injected so tests can pin the value.
     */
    public function __construct(
        private DatabaseExecutor $db,
        private LoggerInterface $logger,
        private string $copilotAppUrl,
        private \Closure $secretGenerator,
    ) {
    }

    /**
     * @return string the registered client id
     */
    public function ensureRegistered(): string
    {
        $existing = $this->db->fetchRow(
            'SELECT `client_id`, `client_secret` FROM `oauth_clients` WHERE `client_id` = ?',
            [self::CLIENT_ID]
        );

        if ($existing !== null && (string) ($existing['client_secret'] ?? '') !== '') {
            $this->logger->info('Co-Pilot SMART client already registered', [
                'client_id' => self::CLIENT_ID,
            ]);
            return self::CLIENT_ID;
        }

        $secret = ($this->secretGenerator)();
        if ($secret === '') {
            throw new \DomainException('secretGenerator must return a non-empty string');
        }
        $redirectUri = rtrim($this->copilotAppUrl, '/') . '/callback';
        $scope = self::scopeString();

        if ($existing === null) {
            $this->db->execute(
                'INSERT INTO `oauth_clients` ('
                . '`client_id`,`client_role`,`client_name`,`client_secret`,`redirect_uri`,'
                . '`grant_types`,`scope`,`is_confidential`,`is_enabled`,'
                . '`skip_ehr_launch_authorization_flow`,`register_date`'
                . ') VALUES (?,?,?,?,?,?,?,?,?,?,NOW())',
                [
                    self::CLIENT_ID,
                    'patient',
                    self::CLIENT_NAME,
                    $secret,
                    $redirectUri,
                    'authorization_code',
                    $scope,
                    1,
                    1,
                    0,
                ]
            );
            $this->logger->info('Co-Pilot SMART client inserted', ['client_id' => self::CLIENT_ID]);
        } else {
            $this->db->execute(
                'UPDATE `oauth_clients` SET `client_secret` = ?, `redirect_uri` = ?, '
                . '`scope` = ?, `is_enabled` = 1 WHERE `client_id` = ?',
                [$secret, $redirectUri, $scope, self::CLIENT_ID]
            );
            $this->logger->info('Co-Pilot SMART client secret rotated', ['client_id' => self::CLIENT_ID]);
        }

        // Mirror the secret into globals so the agent backend can read it.
        $this->db->execute(
            'INSERT INTO `globals` (`gl_name`, `gl_index`, `gl_value`) VALUES (?, 0, ?) '
            . 'ON DUPLICATE KEY UPDATE `gl_value` = VALUES(`gl_value`)',
            ['copilot_oauth_client_secret', $secret]
        );

        return self::CLIENT_ID;
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
}
