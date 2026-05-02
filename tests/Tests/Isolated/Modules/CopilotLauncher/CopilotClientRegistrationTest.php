<?php

/**
 * CopilotClientRegistration isolated test — focuses on idempotency.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Tests\Isolated\Modules\CopilotLauncher;

require_once __DIR__ . '/_module_autoload.php';

use OpenEMR\Modules\CopilotLauncher\Service\CopilotClientRegistration;
use PHPUnit\Framework\TestCase;
use Psr\Log\NullLogger;

final class CopilotClientRegistrationTest extends TestCase
{
    private const SELECT_SQL =
        'SELECT `client_id`, `client_secret` FROM `oauth_clients` WHERE `client_id` = ?';

    public function testReturnsEarlyWhenBothClientsAlreadyRegisteredWithSecrets(): void
    {
        $db = new InMemoryExecutor();
        $db->stubFetch(
            self::SELECT_SQL,
            ['copilot-launcher'],
            ['client_id' => 'copilot-launcher', 'client_secret' => 'launcher-already-set']
        );
        $db->stubFetch(
            self::SELECT_SQL,
            ['copilot-standalone'],
            ['client_id' => 'copilot-standalone', 'client_secret' => 'standalone-already-set']
        );

        $sut = $this->buildRegistration($db, static fn (): string => 'never-called');
        $sut->ensureRegistered();

        $writeOps = array_filter(
            $db->log,
            static fn (array $op): bool => in_array($op['type'], ['insert', 'execute'], true),
        );
        $this->assertCount(0, $writeOps, 'no writes should occur when both clients already registered');
    }

    public function testInsertsBothRowsWhenNeitherExists(): void
    {
        $db = new InMemoryExecutor();
        // No stubs → fetchRow returns null → INSERT branch fires for both clients.

        $secrets = ['launcher-secret', 'standalone-secret'];
        $sut = $this->buildRegistration(
            $db,
            static function () use (&$secrets): string {
                $next = array_shift($secrets);
                return $next !== null ? $next : 'fallback';
            }
        );

        $sut->ensureRegistered();

        $executes = array_values(array_filter(
            $db->log,
            static fn (array $op): bool => $op['type'] === 'execute',
        ));
        // 2 INSERT INTO oauth_clients + 2 globals upserts.
        $this->assertCount(4, $executes);

        $launcherInsert = $executes[0];
        $this->assertStringContainsString('INSERT INTO `oauth_clients`', $launcherInsert['sql']);
        $this->assertContains('copilot-launcher', $launcherInsert['binds']);
        $this->assertContains('patient', $launcherInsert['binds']);
        $this->assertContains('launcher-secret', $launcherInsert['binds']);
        $this->assertContains('http://copilot.example/callback', $launcherInsert['binds']);

        $launcherGlobals = $executes[1];
        $this->assertStringContainsString('`globals`', $launcherGlobals['sql']);
        $this->assertContains('copilot_oauth_client_secret', $launcherGlobals['binds']);
        $this->assertContains('launcher-secret', $launcherGlobals['binds']);

        $standaloneInsert = $executes[2];
        $this->assertStringContainsString('INSERT INTO `oauth_clients`', $standaloneInsert['sql']);
        $this->assertContains('copilot-standalone', $standaloneInsert['binds']);
        $this->assertContains('user', $standaloneInsert['binds']);
        $this->assertContains('standalone-secret', $standaloneInsert['binds']);
        $this->assertContains('http://agent.example/auth/smart/callback', $standaloneInsert['binds']);

        $standaloneGlobals = $executes[3];
        $this->assertStringContainsString('`globals`', $standaloneGlobals['sql']);
        $this->assertContains('copilot_oauth_standalone_client_secret', $standaloneGlobals['binds']);
        $this->assertContains('standalone-secret', $standaloneGlobals['binds']);
    }

    public function testRotatesSecretWhenLauncherRowExistsButSecretMissing(): void
    {
        $db = new InMemoryExecutor();
        $db->stubFetch(
            self::SELECT_SQL,
            ['copilot-launcher'],
            ['client_id' => 'copilot-launcher', 'client_secret' => '']
        );
        $db->stubFetch(
            self::SELECT_SQL,
            ['copilot-standalone'],
            ['client_id' => 'copilot-standalone', 'client_secret' => 'already-set']
        );

        $sut = $this->buildRegistration($db, static fn (): string => 'rotated');
        $sut->ensureRegistered();

        $executes = array_values(array_filter(
            $db->log,
            static fn (array $op): bool => $op['type'] === 'execute',
        ));
        $this->assertCount(2, $executes, 'one UPDATE + one globals upsert for launcher only');
        $this->assertStringContainsString('UPDATE `oauth_clients`', $executes[0]['sql']);
        $this->assertContains('copilot-launcher', $executes[0]['binds']);
        $this->assertContains('rotated', $executes[0]['binds']);
    }

    public function testRotatesSecretWhenStandaloneRowExistsButSecretMissing(): void
    {
        $db = new InMemoryExecutor();
        $db->stubFetch(
            self::SELECT_SQL,
            ['copilot-launcher'],
            ['client_id' => 'copilot-launcher', 'client_secret' => 'already-set']
        );
        $db->stubFetch(
            self::SELECT_SQL,
            ['copilot-standalone'],
            ['client_id' => 'copilot-standalone', 'client_secret' => '']
        );

        $sut = $this->buildRegistration($db, static fn (): string => 'rotated-standalone');
        $sut->ensureRegistered();

        $executes = array_values(array_filter(
            $db->log,
            static fn (array $op): bool => $op['type'] === 'execute',
        ));
        $this->assertCount(2, $executes, 'one UPDATE + one globals upsert for standalone only');
        $this->assertStringContainsString('UPDATE `oauth_clients`', $executes[0]['sql']);
        $this->assertContains('copilot-standalone', $executes[0]['binds']);
        $this->assertContains('rotated-standalone', $executes[0]['binds']);
        $this->assertContains('http://agent.example/auth/smart/callback', $executes[0]['binds']);
    }

    public function testRejectsEmptyGeneratedSecret(): void
    {
        $db = new InMemoryExecutor();
        $sut = $this->buildRegistration($db, static fn (): string => '');

        $this->expectException(\DomainException::class);
        $sut->ensureRegistered();
    }

    public function testLauncherScopeStringIncludesAllRequiredSmartScopes(): void
    {
        $scopes = CopilotClientRegistration::scopeString();
        foreach (
            [
                'launch',
                'launch/patient',
                'patient/Observation.read',
                'patient/Condition.read',
                'patient/MedicationRequest.read',
                'patient/Encounter.read',
                'patient/Patient.read',
                'patient/AllergyIntolerance.read',
                'patient/DocumentReference.read',
                'patient/DiagnosticReport.read',
            ] as $required
        ) {
            $this->assertStringContainsString($required, $scopes);
        }
    }

    public function testStandaloneScopeStringIsUserScopedAndCoversPRDResources(): void
    {
        $scopes = CopilotClientRegistration::scopeStringStandalone();
        foreach (
            [
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
            ] as $required
        ) {
            $this->assertStringContainsString($required, $scopes);
        }
        // Standalone scopes must NOT carry patient-scoped or launch/patient
        // permissions — those belong to the EHR-launch client.
        $this->assertStringNotContainsString('patient/', $scopes);
        $this->assertStringNotContainsString('launch/patient', $scopes);
    }

    public function testStandaloneInsertUsesUserRoleAndAgentBackendRedirect(): void
    {
        $db = new InMemoryExecutor();
        $db->stubFetch(
            self::SELECT_SQL,
            ['copilot-launcher'],
            ['client_id' => 'copilot-launcher', 'client_secret' => 'already-set']
        );
        // Standalone has no row → INSERT branch.

        $sut = $this->buildRegistration($db, static fn (): string => 'fresh');
        $sut->ensureRegistered();

        $executes = array_values(array_filter(
            $db->log,
            static fn (array $op): bool => $op['type'] === 'execute',
        ));
        $this->assertNotEmpty($executes);
        $insert = $executes[0];
        $this->assertStringContainsString('INSERT INTO `oauth_clients`', $insert['sql']);
        $this->assertContains('copilot-standalone', $insert['binds']);
        $this->assertContains('user', $insert['binds']);
        $this->assertContains('http://agent.example/auth/smart/callback', $insert['binds']);
        // Scope bound into the INSERT must be the standalone scope string.
        $this->assertContains(CopilotClientRegistration::scopeStringStandalone(), $insert['binds']);
    }

    private function buildRegistration(InMemoryExecutor $db, \Closure $secretGenerator): CopilotClientRegistration
    {
        return new CopilotClientRegistration(
            $db,
            new NullLogger(),
            'http://copilot.example',
            'http://agent.example',
            $secretGenerator,
        );
    }
}
