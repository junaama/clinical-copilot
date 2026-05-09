<?php

/**
 * Thin database seam for isolated tests.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Naama <naama.paulemont@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Naama
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\CopilotLabWriter\Service;

interface DatabaseExecutor
{
    /**
     * @param list<string|int|float|bool|null> $binds
     */
    public function insert(string $sql, array $binds): int;

    /**
     * @param list<string|int|float|bool|null> $binds
     * @return array<string, scalar|null>|null
     */
    public function fetchRow(string $sql, array $binds): ?array;

    /**
     * @param list<string|int|float|bool|null> $binds
     */
    public function execute(string $sql, array $binds): void;
}
