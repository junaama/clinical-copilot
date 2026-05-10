<?php

/**
 * LabResultApiController isolated tests.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Tests\Isolated\Modules\CopilotLabWriter;

require_once __DIR__ . '/_module_autoload.php';

use OpenEMR\Modules\CopilotLabWriter\Controller\LabResultApiController;
use OpenEMR\Modules\CopilotLabWriter\Service\LabResultWriter;
use PHPUnit\Framework\TestCase;

final class LabResultApiControllerTest extends TestCase
{
    public function testUuidPatientIdCanResolveToNumericPid(): void
    {
        $db = new InMemoryExecutor();
        $controller = new LabResultApiController(
            writer: new LabResultWriter(
                db: $db,
                uuidGenerator: static fn (): string => str_repeat("\x42", 16),
            ),
            patientIdResolver: static fn (string|int $pid): ?int => $pid === '11111111-1111-1111-1111-111111111111'
                ? 123
                : null,
        );

        $response = $controller->post('11111111-1111-1111-1111-111111111111', [
            'results' => [
                [
                    'field_path' => 'results.0',
                    'source_document_id' => 'DocumentReference/77',
                    'loinc_code' => '13457-7',
                    'test_name' => 'LDL Cholesterol',
                    'value' => '180',
                    'unit' => 'mg/dL',
                    'reference_range' => '<100 mg/dL',
                    'effective_datetime' => '2026-04-15',
                    'abnormal_flag' => 'high',
                ],
            ],
        ]);

        $body = json_decode((string) $response->getBody(), true);
        $this->assertSame('succeeded', $body['persistence_status']);

        $fetchOp = $db->log[0];
        $this->assertSame('fetch', $fetchOp['type']);
        $this->assertSame(123, $fetchOp['binds'][0]);
    }

    public function testUnresolvedPatientIdReturnsFailurePayload(): void
    {
        $controller = new LabResultApiController(
            writer: new LabResultWriter(
                db: new InMemoryExecutor(),
                uuidGenerator: static fn (): string => str_repeat("\x42", 16),
            ),
            patientIdResolver: static fn (string|int $pid): ?int => null,
        );

        $response = $controller->post('missing-patient', []);

        $body = json_decode((string) $response->getBody(), true);
        $this->assertSame('failed', $body['persistence_status']);
        $this->assertSame('invalid_patient_id', $body['error']['code']);
    }
}
