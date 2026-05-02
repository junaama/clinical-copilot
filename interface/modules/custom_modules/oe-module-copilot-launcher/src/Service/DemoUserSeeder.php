<?php

/**
 * Idempotent seed for the demo non-admin provider used by the standalone
 * Co-Pilot login flow.
 *
 * The standalone Co-Pilot portal logs the clinician in via SMART-on-FHIR
 * standalone launch against OpenEMR's OAuth2 server. To exercise the
 * CareTeam authorization gate against a *real* authorization boundary
 * (and not admin's god-mode access), demos and evals must log in as a
 * non-admin provider. This seeder makes that user exist on day one.
 *
 * Behavior:
 * - If the demo user row already exists in `users`, no-op.
 * - Otherwise, INSERT into `users` (provider role: authorized=1, active=1),
 *   then `users_secure` with the hashed password (FK on `users.id`),
 *   then `uuid_registry` so the OpenEMR services and FHIR Practitioner
 *   resource resolve the same UUID.
 *
 * Dependencies are injected as Closures so the seeder is unit-testable
 * without OpenEMR's runtime password-hashing or random-uuid plumbing.
 *
 * The corresponding FHIR `Practitioner` resource is derived automatically
 * by OpenEMR's FHIR layer from the `users` row + `uuid_registry` mapping;
 * no separate write is required.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\CopilotLauncher\Service;

use Psr\Log\LoggerInterface;

final readonly class DemoUserSeeder
{
    public const USERNAME = 'dr_smith';
    public const FNAME = 'Jane';
    public const LNAME = 'Smith';
    public const TITLE = 'Dr.';
    public const TAXONOMY = '207Q00000X'; // Family medicine — arbitrary plausible default
    public const ABOOK_TYPE = 'pro_med';   // Provider/medical, address book classification

    /**
     * @param \Closure(string): string $passwordHasher Returns a hashed password.
     *        Production wires AuthHash::passwordHash; tests pin a deterministic
     *        value.
     * @param \Closure(): string $uuidGenerator Returns 16 raw bytes for a binary
     *        UUID. Production uses random_bytes(16); tests pin a fixed value.
     */
    public function __construct(
        private DatabaseExecutor $db,
        private LoggerInterface $logger,
        private string $password,
        private \Closure $passwordHasher,
        private \Closure $uuidGenerator,
    ) {
        if ($this->password === '') {
            throw new \DomainException('demo password must be non-empty');
        }
    }

    public function ensureSeeded(): void
    {
        $existing = $this->db->fetchRow(
            'SELECT `id` FROM `users` WHERE `username` = ?',
            [self::USERNAME]
        );
        if ($existing !== null) {
            $this->logger->info('demo user already seeded', ['username' => self::USERNAME]);
            return;
        }

        $uuid = ($this->uuidGenerator)();
        if (strlen($uuid) !== 16) {
            throw new \DomainException('uuidGenerator must return 16 raw bytes');
        }

        $hashed = ($this->passwordHasher)($this->password);
        if ($hashed === '') {
            throw new \DomainException('passwordHasher returned empty string');
        }

        // Provider account: authorized=1 marks the user as a billable
        // provider; active=1 enables login. Legacy `users.password` column
        // stays empty — the modern hash lives in `users_secure`.
        $userId = $this->db->insert(
            'INSERT INTO `users` ('
            . '`uuid`,`username`,`password`,`fname`,`lname`,`title`,'
            . '`authorized`,`active`,`see_auth`,`taxonomy`,`abook_type`,'
            . '`main_menu_role`,`patient_menu_role`,`portal_user`,`calendar`'
            . ') VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            [
                $uuid,
                self::USERNAME,
                '',
                self::FNAME,
                self::LNAME,
                self::TITLE,
                1, // authorized = provider
                1, // active
                1, // see_auth
                self::TAXONOMY,
                self::ABOOK_TYPE,
                'standard',
                'standard',
                0,
                1, // calendar = visible
            ]
        );

        $this->db->execute(
            'INSERT INTO `users_secure` (`id`,`username`,`password`,`last_update_password`) '
            . 'VALUES (?,?,?,NOW())',
            [$userId, self::USERNAME, $hashed]
        );

        // Mirror into uuid_registry so OpenEMR's FHIR Practitioner endpoint
        // resolves Practitioner/<uuid> back to this user without an external
        // populateAllMissingUuids() pass. table_id is stored as a string per
        // the column definition.
        $this->db->execute(
            'INSERT INTO `uuid_registry` (`uuid`,`table_name`,`table_id`,`couchdb`,`document_drive`,`mapped`,`created`) '
            . 'VALUES (?,?,?,?,?,?,NOW())',
            [$uuid, 'users', (string) $userId, '', 0, 0]
        );

        $this->logger->info('demo user seeded', [
            'username' => self::USERNAME,
            'user_id' => $userId,
        ]);
    }
}
