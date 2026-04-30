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
    public function testReturnsEarlyWhenClientAlreadyRegisteredWithSecret(): void
    {
        $db = new InMemoryExecutor();
        $db->stubFetch(
            'SELECT `client_id`, `client_secret` FROM `oauth_clients` WHERE `client_id` = ?',
            ['copilot-launcher'],
            ['client_id' => 'copilot-launcher', 'client_secret' => 'already-set']
        );

        $sut = new CopilotClientRegistration(
            $db,
            new NullLogger(),
            'http://copilot.example',
            static fn (): string => 'never-called',
        );

        $id = $sut->ensureRegistered();

        $this->assertSame('copilot-launcher', $id);
        $writeOps = array_filter(
            $db->log,
            static fn (array $op): bool => in_array($op['type'], ['insert', 'execute'], true),
        );
        $this->assertCount(0, $writeOps, 'no writes should occur when already registered');
    }

    public function testInsertsRowWhenNoneExists(): void
    {
        $db = new InMemoryExecutor();
        // No stub → fetchRow returns null → INSERT branch fires.

        $sut = new CopilotClientRegistration(
            $db,
            new NullLogger(),
            'http://copilot.example/',
            static fn (): string => 'fixed-secret',
        );

        $id = $sut->ensureRegistered();
        $this->assertSame('copilot-launcher', $id);

        $executes = array_values(array_filter(
            $db->log,
            static fn (array $op): bool => $op['type'] === 'execute',
        ));
        $this->assertGreaterThanOrEqual(2, count($executes), 'one INSERT + one globals upsert');

        $insert = $executes[0];
        $this->assertStringContainsString('INSERT INTO `oauth_clients`', $insert['sql']);
        $this->assertContains('copilot-launcher', $insert['binds']);
        $this->assertContains('fixed-secret', $insert['binds']);
        $this->assertContains('http://copilot.example/callback', $insert['binds']);

        $globals = $executes[1];
        $this->assertStringContainsString('`globals`', $globals['sql']);
        $this->assertContains('copilot_oauth_client_secret', $globals['binds']);
        $this->assertContains('fixed-secret', $globals['binds']);
    }

    public function testRotatesSecretWhenRowExistsButSecretMissing(): void
    {
        $db = new InMemoryExecutor();
        $db->stubFetch(
            'SELECT `client_id`, `client_secret` FROM `oauth_clients` WHERE `client_id` = ?',
            ['copilot-launcher'],
            ['client_id' => 'copilot-launcher', 'client_secret' => '']
        );

        $sut = new CopilotClientRegistration(
            $db,
            new NullLogger(),
            'http://copilot.example',
            static fn (): string => 'rotated',
        );

        $sut->ensureRegistered();

        $executes = array_values(array_filter(
            $db->log,
            static fn (array $op): bool => $op['type'] === 'execute',
        ));
        $this->assertNotEmpty($executes);
        $this->assertStringContainsString('UPDATE `oauth_clients`', $executes[0]['sql']);
        $this->assertContains('rotated', $executes[0]['binds']);
    }

    public function testRejectsEmptyGeneratedSecret(): void
    {
        $db = new InMemoryExecutor();
        $sut = new CopilotClientRegistration(
            $db,
            new NullLogger(),
            'http://copilot.example',
            static fn (): string => '',
        );

        $this->expectException(\DomainException::class);
        $sut->ensureRegistered();
    }

    public function testScopeStringIncludesAllRequiredSmartScopes(): void
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
}
