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

use OpenEMR\Modules\CopilotLabWriter\Service\LabResultWriter;
use OpenEMR\RestControllers\RestControllerHelper;
use Psr\Http\Message\ResponseInterface;

final class LabResultApiController
{
    public function __construct(
        private readonly LabResultWriter $writer,
    ) {
    }

    /**
     * @param array<string, mixed> $payload
     */
    public function post(string|int $pid, array $payload): ResponseInterface
    {
        if (!is_numeric($pid) || (int) $pid <= 0) {
            return RestControllerHelper::returnSingleObjectResponse([
                'persistence_status' => 'failed',
                'results' => [],
                'error' => [
                    'code' => 'invalid_patient_id',
                    'message' => 'patient id must be a positive integer',
                ],
            ]);
        }

        return RestControllerHelper::returnSingleObjectResponse(
            $this->writer->persist((int) $pid, $payload)
        );
    }
}
