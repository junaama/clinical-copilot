<?php

/**
 * Lab-result API controller.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Naama <naama.paulemont@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Naama
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\CopilotLabWriter\Controller;

use OpenEMR\Common\Database\QueryUtils;
use OpenEMR\Common\Uuid\UuidRegistry;
use OpenEMR\Modules\CopilotLabWriter\Service\LabResultWriter;
use OpenEMR\RestControllers\RestControllerHelper;
use Psr\Http\Message\ResponseInterface;

final class LabResultApiController
{
    /**
     * @param (\Closure(string|int): int|null)|null $patientIdResolver
     */
    public function __construct(
        private readonly LabResultWriter $writer,
        private readonly ?\Closure $patientIdResolver = null,
    ) {
    }

    /**
     * @param array<string, mixed> $payload
     */
    public function post(string|int $pid, array $payload): ResponseInterface
    {
        $resolvedPid = $this->resolvePatientPid($pid);
        if ($resolvedPid === null) {
            return RestControllerHelper::returnSingleObjectResponse([
                'persistence_status' => 'failed',
                'results' => [],
                'error' => [
                    'code' => 'invalid_patient_id',
                    'message' => 'patient id must resolve to an OpenEMR patient',
                ],
            ]);
        }

        return RestControllerHelper::returnSingleObjectResponse(
            $this->writer->persist($resolvedPid, $payload)
        );
    }

    private function resolvePatientPid(string|int $pid): ?int
    {
        if ($this->patientIdResolver !== null) {
            $resolved = ($this->patientIdResolver)($pid);
            return $resolved !== null && $resolved > 0 ? $resolved : null;
        }

        if (is_numeric($pid) && (int) $pid > 0) {
            return (int) $pid;
        }

        $patientUuid = (string) $pid;
        if (!UuidRegistry::isValidStringUUID($patientUuid)) {
            return null;
        }

        $resolvedPid = QueryUtils::fetchSingleValue(
            'SELECT `pid` FROM `patient_data` WHERE `uuid` = ? LIMIT 1',
            'pid',
            [UuidRegistry::uuidToBytes($patientUuid)]
        );
        return is_numeric($resolvedPid) && (int) $resolvedPid > 0
            ? (int) $resolvedPid
            : null;
    }
}
