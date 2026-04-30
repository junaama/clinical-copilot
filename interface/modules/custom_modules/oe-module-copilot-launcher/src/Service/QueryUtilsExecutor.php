<?php

/**
 * Production DatabaseExecutor — delegates to OpenEMR's QueryUtils.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\CopilotLauncher\Service;

use OpenEMR\Common\Database\QueryUtils;

final class QueryUtilsExecutor implements DatabaseExecutor
{
    public function insert(string $sql, array $binds): int
    {
        return (int) QueryUtils::sqlInsert($sql, $binds);
    }

    public function fetchRow(string $sql, array $binds): ?array
    {
        $rows = QueryUtils::fetchRecords($sql, $binds);
        if ($rows === []) {
            return null;
        }
        /** @var array<string, scalar|null> $first */
        $first = $rows[0];
        return $first;
    }

    public function execute(string $sql, array $binds): void
    {
        QueryUtils::sqlStatementThrowException($sql, $binds);
    }
}
