<?php

/**
 * LabResultWriter isolated tests.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Tests\Isolated\Modules\CopilotLabWriter;

require_once __DIR__ . '/_module_autoload.php';

use OpenEMR\Modules\CopilotLabWriter\Service\LabResultWriter;
use PHPUnit\Framework\TestCase;

final class LabResultWriterTest extends TestCase
{
    private const IDEMPOTENCY_SQL =
        'SELECT `procedure_order_id`, `procedure_report_id`, `procedure_result_id`'
        . ' FROM `copilot_lab_result_map`'
        . ' WHERE `patient_id` = ? AND `source_document_reference` = ? AND `field_path` = ?';

    public function testCreatesNativeLabRowsAndIdempotencyMap(): void
    {
        $db = new InMemoryExecutor();
        $writer = new LabResultWriter(
            db: $db,
            uuidGenerator: static fn (): string => str_repeat("\x42", 16),
        );

        $result = $writer->persist(123, [
            'results' => [
                [
                    'field_path' => 'results.0',
                    'source_document_id' => 'DocumentReference/77',
                    'loinc_code' => '13457-7',
                    'test_name' => 'LDL Cholesterol',
                    'value' => '180',
                    'unit' => 'mg/dL',
                    'original_unit' => 'mg/dL',
                    'reference_range' => '<100 mg/dL',
                    'effective_datetime' => '2026-04-15T10:30:00-04:00',
                    'ordering_provider' => 'Dr. Smith',
                    'abnormal_flag' => 'high',
                ],
            ],
        ]);

        $this->assertSame('succeeded', $result['persistence_status']);
        $this->assertSame('created', $result['results'][0]['persistence_status']);

        $insertSql = implode("\n", array_column($db->log, 'sql'));
        $this->assertStringContainsString('INSERT INTO `procedure_order`', $insertSql);
        $this->assertStringContainsString('INSERT INTO `procedure_order_code`', $insertSql);
        $this->assertStringContainsString('INSERT INTO `procedure_report`', $insertSql);
        $this->assertStringContainsString('INSERT INTO `procedure_result`', $insertSql);
        $this->assertStringContainsString('INSERT INTO `copilot_lab_result_map`', $insertSql);

        $resultInsert = array_values(array_filter(
            $db->log,
            static fn (array $op): bool => $op['type'] === 'insert'
                && str_contains($op['sql'], 'INSERT INTO `procedure_result`'),
        ))[0];
        $this->assertContains('13457-7', $resultInsert['binds']);
        $this->assertContains('LDL Cholesterol', $resultInsert['binds']);
        $this->assertContains('180', $resultInsert['binds']);
        $this->assertContains('mg/dL', $resultInsert['binds']);
        $this->assertContains('high', $resultInsert['binds']);
        $this->assertContains(77, $resultInsert['binds']);
    }

    public function testExistingIdempotencyKeyUpdatesInsteadOfDuplicating(): void
    {
        $db = new InMemoryExecutor();
        $db->stubFetch(self::IDEMPOTENCY_SQL, [123, 'DocumentReference/77', 'results.0'], [
            'procedure_order_id' => 10,
            'procedure_report_id' => 11,
            'procedure_result_id' => 12,
        ]);
        $writer = new LabResultWriter(
            db: $db,
            uuidGenerator: static fn (): string => str_repeat("\x42", 16),
        );

        $result = $writer->persist(123, [
            'results' => [
                [
                    'field_path' => 'results.0',
                    'source_document_id' => 'DocumentReference/77',
                    'loinc_code' => '13457-7',
                    'test_name' => 'LDL Cholesterol',
                    'value' => '181',
                    'unit' => 'mg/dL',
                    'reference_range' => '<100 mg/dL',
                    'effective_datetime' => '2026-04-15',
                    'abnormal_flag' => 'high',
                ],
            ],
        ]);

        $this->assertSame('succeeded', $result['persistence_status']);
        $this->assertSame('updated', $result['results'][0]['persistence_status']);

        $insertOps = array_filter($db->log, static fn (array $op): bool => $op['type'] === 'insert');
        $this->assertCount(0, $insertOps, 'idempotent retry should update existing rows');
        $updateSql = implode("\n", array_column($db->log, 'sql'));
        $this->assertStringContainsString('UPDATE `procedure_result`', $updateSql);
        $this->assertStringContainsString('UPDATE `procedure_report`', $updateSql);
        $this->assertStringContainsString('UPDATE `procedure_order`', $updateSql);
    }

    public function testInvalidResultReturnsPerResultFailure(): void
    {
        $writer = new LabResultWriter(
            db: new InMemoryExecutor(),
            uuidGenerator: static fn (): string => str_repeat("\x42", 16),
        );

        $result = $writer->persist(123, [
            'results' => [
                [
                    'field_path' => 'results.0',
                    'source_document_id' => 'DocumentReference/77',
                    'test_name' => 'LDL Cholesterol',
                    'value' => '180',
                    'unit' => 'mg/dL',
                    'abnormal_flag' => 'high',
                ],
            ],
        ]);

        $this->assertSame('failed', $result['persistence_status']);
        $this->assertSame('failed', $result['results'][0]['persistence_status']);
        $this->assertSame('missing_effective_datetime', $result['results'][0]['error']['code']);
    }
}
