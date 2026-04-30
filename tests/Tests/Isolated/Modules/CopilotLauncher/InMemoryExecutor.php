<?php

/**
 * In-memory DatabaseExecutor used across this module's isolated tests.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Tests\Isolated\Modules\CopilotLauncher;

require_once __DIR__ . '/_module_autoload.php';

use OpenEMR\Modules\CopilotLauncher\Service\DatabaseExecutor;

final class InMemoryExecutor implements DatabaseExecutor
{
    /** @var list<array{type: string, sql: string, binds: list<string|int|float|bool|null>}> */
    public array $log = [];

    /** @var array<string, array<string, scalar|null>|null> */
    public array $stubbedRows = [];

    public int $nextInsertId = 1;

    public bool $shouldThrowOnInsert = false;

    public function insert(string $sql, array $binds): int
    {
        $this->log[] = ['type' => 'insert', 'sql' => $sql, 'binds' => $binds];
        if ($this->shouldThrowOnInsert) {
            throw new \RuntimeException('insert blew up');
        }
        return $this->nextInsertId++;
    }

    public function fetchRow(string $sql, array $binds): ?array
    {
        $this->log[] = ['type' => 'fetch', 'sql' => $sql, 'binds' => $binds];
        $key = $this->keyFor($sql, $binds);
        return $this->stubbedRows[$key] ?? null;
    }

    public function execute(string $sql, array $binds): void
    {
        $this->log[] = ['type' => 'execute', 'sql' => $sql, 'binds' => $binds];
    }

    /**
     * @param list<string|int|float|bool|null> $binds
     * @param array<string, scalar|null>|null $row
     */
    public function stubFetch(string $sql, array $binds, ?array $row): void
    {
        $this->stubbedRows[$this->keyFor($sql, $binds)] = $row;
    }

    /**
     * @param list<string|int|float|bool|null> $binds
     */
    private function keyFor(string $sql, array $binds): string
    {
        return $sql . '|' . implode('||', array_map(static fn ($v): string => (string) $v, $binds));
    }
}
