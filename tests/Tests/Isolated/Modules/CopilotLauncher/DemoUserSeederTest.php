<?php

/**
 * DemoUserSeeder isolated test — verifies idempotent seed of the demo
 * non-admin provider used by the standalone Co-Pilot login flow.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Tests\Isolated\Modules\CopilotLauncher;

require_once __DIR__ . '/_module_autoload.php';

use OpenEMR\Modules\CopilotLauncher\Service\DemoUserSeeder;
use PHPUnit\Framework\TestCase;
use Psr\Log\NullLogger;

final class DemoUserSeederTest extends TestCase
{
    private const SELECT_USER_SQL =
        'SELECT `id` FROM `users` WHERE `username` = ?';

    public function testReturnsEarlyWhenUserAlreadyExists(): void
    {
        $db = new InMemoryExecutor();
        $db->stubFetch(
            self::SELECT_USER_SQL,
            [DemoUserSeeder::USERNAME],
            ['id' => 42]
        );

        $sut = $this->buildSeeder(
            $db,
            passwordHasher: static fn (string $_p): string => 'never-called',
            uuidGenerator: static fn (): string => 'never-called',
        );
        $sut->ensureSeeded();

        $writeOps = array_filter(
            $db->log,
            static fn (array $op): bool => in_array($op['type'], ['insert', 'execute'], true),
        );
        $this->assertCount(0, $writeOps, 'no writes when demo user already present');
    }

    public function testInsertsUserAndSecureRowAndUuidWhenAbsent(): void
    {
        $db = new InMemoryExecutor();
        $db->nextInsertId = 17;

        $sut = $this->buildSeeder(
            $db,
            passwordHasher: static fn (string $raw): string => 'hashed:' . $raw,
            uuidGenerator: static fn (): string => str_repeat("\x42", 16),
        );
        $sut->ensureSeeded();

        $inserts = array_values(array_filter(
            $db->log,
            static fn (array $op): bool => $op['type'] === 'insert',
        ));
        $executes = array_values(array_filter(
            $db->log,
            static fn (array $op): bool => $op['type'] === 'execute',
        ));

        // 1 INSERT into users (which returns the new id), then 2 executes:
        //  users_secure + uuid_registry. Order matters: users first so id is real,
        //  then users_secure (FK on id), then uuid_registry mirror.
        $this->assertCount(1, $inserts, 'expected one insert (users)');
        $this->assertCount(2, $executes, 'expected two executes (users_secure + uuid_registry)');

        $usersInsert = $inserts[0];
        $this->assertStringContainsString('INSERT INTO `users`', $usersInsert['sql']);
        $this->assertContains(DemoUserSeeder::USERNAME, $usersInsert['binds']);
        $this->assertContains(DemoUserSeeder::FNAME, $usersInsert['binds']);
        $this->assertContains(DemoUserSeeder::LNAME, $usersInsert['binds']);
        $this->assertContains(str_repeat("\x42", 16), $usersInsert['binds']);
        // Provider role: authorized=1, active=1, non-admin (no admin flag set).
        $this->assertContains(1, $usersInsert['binds'], 'authorized=1 (provider) expected');

        $secureExec = $executes[0];
        $this->assertStringContainsString('users_secure', $secureExec['sql']);
        $this->assertContains(17, $secureExec['binds'], 'users_secure must use returned user id');
        $this->assertContains(DemoUserSeeder::USERNAME, $secureExec['binds']);
        $this->assertContains('hashed:dr_smith_pass', $secureExec['binds']);

        $uuidExec = $executes[1];
        $this->assertStringContainsString('uuid_registry', $uuidExec['sql']);
        $this->assertContains(str_repeat("\x42", 16), $uuidExec['binds']);
        $this->assertContains('users', $uuidExec['binds']);
        $this->assertContains('17', $uuidExec['binds'], 'uuid_registry table_id stored as string');
    }

    public function testRejectsEmptyHashedPassword(): void
    {
        $db = new InMemoryExecutor();

        $sut = $this->buildSeeder(
            $db,
            passwordHasher: static fn (string $_p): string => '',
            uuidGenerator: static fn (): string => str_repeat("\x01", 16),
        );

        $this->expectException(\DomainException::class);
        $sut->ensureSeeded();
    }

    public function testRejectsNon16ByteUuid(): void
    {
        $db = new InMemoryExecutor();

        $sut = $this->buildSeeder(
            $db,
            passwordHasher: static fn (string $raw): string => 'hashed:' . $raw,
            uuidGenerator: static fn (): string => 'not-16-bytes',
        );

        $this->expectException(\DomainException::class);
        $sut->ensureSeeded();
    }

    public function testRejectsEmptyPasswordConfig(): void
    {
        $db = new InMemoryExecutor();

        $this->expectException(\DomainException::class);
        new DemoUserSeeder(
            db: $db,
            logger: new NullLogger(),
            password: '',
            passwordHasher: static fn (string $raw): string => 'hashed:' . $raw,
            uuidGenerator: static fn (): string => str_repeat("\x42", 16),
        );
    }

    private function buildSeeder(
        InMemoryExecutor $db,
        \Closure $passwordHasher,
        \Closure $uuidGenerator,
    ): DemoUserSeeder {
        return new DemoUserSeeder(
            db: $db,
            logger: new NullLogger(),
            password: 'dr_smith_pass',
            passwordHasher: $passwordHasher,
            uuidGenerator: $uuidGenerator,
        );
    }
}
