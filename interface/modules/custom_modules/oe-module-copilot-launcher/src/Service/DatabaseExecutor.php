<?php

/**
 * Thin seam between the module's services and OpenEMR's QueryUtils.
 *
 * Exists for one reason: QueryUtils' methods are static, which makes them
 * un-mockable in isolated unit tests. Production wires the
 * QueryUtilsExecutor implementation; tests inject InMemoryExecutor.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\CopilotLauncher\Service;

interface DatabaseExecutor
{
    /**
     * Execute an INSERT and return the newly-allocated id.
     *
     * @param list<string|int|float|bool|null> $binds
     */
    public function insert(string $sql, array $binds): int;

    /**
     * Execute a SELECT and return the first row, or null when empty.
     *
     * @param list<string|int|float|bool|null> $binds
     * @return array<string, scalar|null>|null
     */
    public function fetchRow(string $sql, array $binds): ?array;

    /**
     * Execute a non-returning statement (UPDATE/DELETE/INSERT-IGNORE).
     *
     * @param list<string|int|float|bool|null> $binds
     */
    public function execute(string $sql, array $binds): void;
}
