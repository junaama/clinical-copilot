<?php

/**
 * Writes Co-Pilot lab extractions into OpenEMR native lab tables.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Naama <naama.paulemont@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Naama
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\CopilotLabWriter\Service;

final class LabResultWriter
{
    private const IDEMPOTENCY_SQL =
        'SELECT `procedure_order_id`, `procedure_report_id`, `procedure_result_id`'
        . ' FROM `copilot_lab_result_map`'
        . ' WHERE `patient_id` = ? AND `source_document_reference` = ? AND `field_path` = ?';

    /**
     * @param \Closure(): string $uuidGenerator Returns 16 random bytes.
     */
    public function __construct(
        private readonly DatabaseExecutor $db,
        private readonly \Closure $uuidGenerator,
    ) {
    }

    /**
     * @param array<string, mixed> $payload
     * @return array{persistence_status: string, results: list<array<string, mixed>>}
     */
    public function persist(int $patientId, array $payload): array
    {
        $rows = $this->normalizeRows($payload);
        $results = [];
        foreach ($rows as $index => $row) {
            try {
                $results[] = $this->persistOne($patientId, $row, $index);
            } catch (\DomainException $e) {
                $results[] = $this->failedResult($row, $index, $e->getMessage(), $e->getMessage());
            } catch (\Throwable $e) {
                $results[] = $this->failedResult($row, $index, 'write_failed', 'database write failed');
            }
        }

        return [
            'persistence_status' => $this->aggregateStatus($results),
            'results' => $results,
        ];
    }

    /**
     * @param array<string, mixed> $payload
     * @return list<array<string, mixed>>
     */
    private function normalizeRows(array $payload): array
    {
        $rows = $payload['results'] ?? null;
        if (is_array($rows)) {
            return array_values(array_filter($rows, static fn ($row): bool => is_array($row)));
        }
        return [$payload];
    }

    /**
     * @param array<string, mixed> $row
     * @return array<string, mixed>
     */
    private function persistOne(int $patientId, array $row, int $index): array
    {
        $fieldPath = $this->requiredString($row, 'field_path');
        $sourceDocumentReference = $this->requiredString($row, 'source_document_id');
        $effectiveDate = $this->dateTime($this->requiredString($row, 'effective_datetime'), 'effective_datetime');
        $testName = $this->requiredString($row, 'test_name');
        $value = $this->requiredString($row, 'value');
        $unit = $this->requiredString($row, 'unit');
        $code = $this->optionalString($row, 'loinc_code') ?: $testName;
        $range = $this->referenceRange($this->optionalString($row, 'reference_range'), $unit);
        $abnormal = $this->abnormal($this->optionalString($row, 'abnormal_flag'));
        $documentId = $this->documentId($sourceDocumentReference);
        $comments = $this->comments($row, $sourceDocumentReference, $fieldPath);

        $existing = $this->db->fetchRow(self::IDEMPOTENCY_SQL, [
            $patientId,
            $sourceDocumentReference,
            $fieldPath,
        ]);
        if ($existing !== null) {
            $this->updateExisting(
                (int) $existing['procedure_order_id'],
                (int) $existing['procedure_report_id'],
                (int) $existing['procedure_result_id'],
                $effectiveDate,
                $testName,
                $code,
                $value,
                $unit,
                $range,
                $abnormal,
                $documentId,
                $comments,
            );
            return $this->successResult($fieldPath, 'updated', $existing);
        }

        $procedureOrderId = $this->db->insert(
            'INSERT INTO `procedure_order`'
            . ' (`uuid`, `provider_id`, `patient_id`, `encounter_id`, `date_collected`,'
            . ' `date_ordered`, `order_status`, `activity`, `control_id`, `procedure_order_type`)'
            . ' VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            [
                ($this->uuidGenerator)(),
                0,
                $patientId,
                0,
                $effectiveDate,
                $effectiveDate,
                'complete',
                1,
                $this->controlId($sourceDocumentReference, $fieldPath),
                'laboratory_test',
            ],
        );

        $this->db->insert(
            'INSERT INTO `procedure_order_code`'
            . ' (`procedure_order_id`, `procedure_order_seq`, `procedure_code`,'
            . ' `procedure_name`, `procedure_source`, `procedure_order_title`, `procedure_type`)'
            . ' VALUES (?, ?, ?, ?, ?, ?, ?)',
            [$procedureOrderId, 1, $code, $testName, '2', $testName, 'laboratory_test'],
        );

        $procedureReportId = $this->db->insert(
            'INSERT INTO `procedure_report`'
            . ' (`uuid`, `procedure_order_id`, `procedure_order_seq`, `date_collected`,'
            . ' `date_report`, `source`, `report_status`, `review_status`, `report_notes`)'
            . ' VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            [
                ($this->uuidGenerator)(),
                $procedureOrderId,
                1,
                $effectiveDate,
                $effectiveDate,
                0,
                'complete',
                'received',
                'Co-Pilot extracted lab result',
            ],
        );

        $procedureResultId = $this->db->insert(
            'INSERT INTO `procedure_result`'
            . ' (`uuid`, `procedure_report_id`, `result_data_type`, `result_code`,'
            . ' `result_text`, `date`, `units`, `result`, `range`, `abnormal`,'
            . ' `comments`, `document_id`, `result_status`)'
            . ' VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            [
                ($this->uuidGenerator)(),
                $procedureReportId,
                is_numeric($value) ? 'N' : 'S',
                $code,
                $testName,
                $effectiveDate,
                $unit,
                $value,
                $range,
                $abnormal,
                $comments,
                $documentId,
                'final',
            ],
        );

        $this->db->execute(
            'INSERT INTO `copilot_lab_result_map`'
            . ' (`patient_id`, `source_document_reference`, `field_path`,'
            . ' `procedure_order_id`, `procedure_report_id`, `procedure_result_id`)'
            . ' VALUES (?, ?, ?, ?, ?, ?)',
            [$patientId, $sourceDocumentReference, $fieldPath, $procedureOrderId, $procedureReportId, $procedureResultId],
        );

        return [
            'field_path' => $fieldPath,
            'persistence_status' => 'created',
            'procedure_order_id' => $procedureOrderId,
            'procedure_report_id' => $procedureReportId,
            'procedure_result_id' => $procedureResultId,
        ];
    }

    private function updateExisting(
        int $procedureOrderId,
        int $procedureReportId,
        int $procedureResultId,
        string $effectiveDate,
        string $testName,
        string $code,
        string $value,
        string $unit,
        string $range,
        string $abnormal,
        int $documentId,
        string $comments,
    ): void {
        $this->db->execute(
            'UPDATE `procedure_order` SET `date_collected` = ?, `date_ordered` = ?,'
            . ' `order_status` = ? WHERE `procedure_order_id` = ?',
            [$effectiveDate, $effectiveDate, 'complete', $procedureOrderId],
        );
        $this->db->execute(
            'UPDATE `procedure_report` SET `date_collected` = ?, `date_report` = ?,'
            . ' `report_status` = ? WHERE `procedure_report_id` = ?',
            [$effectiveDate, $effectiveDate, 'complete', $procedureReportId],
        );
        $this->db->execute(
            'UPDATE `procedure_result` SET `result_data_type` = ?, `result_code` = ?,'
            . ' `result_text` = ?, `date` = ?, `units` = ?, `result` = ?, `range` = ?,'
            . ' `abnormal` = ?, `comments` = ?, `document_id` = ?, `result_status` = ?'
            . ' WHERE `procedure_result_id` = ?',
            [
                is_numeric($value) ? 'N' : 'S',
                $code,
                $testName,
                $effectiveDate,
                $unit,
                $value,
                $range,
                $abnormal,
                $comments,
                $documentId,
                'final',
                $procedureResultId,
            ],
        );
    }

    /**
     * @param array<string, scalar|null> $ids
     * @return array<string, mixed>
     */
    private function successResult(string $fieldPath, string $status, array $ids): array
    {
        return [
            'field_path' => $fieldPath,
            'persistence_status' => $status,
            'procedure_order_id' => (int) $ids['procedure_order_id'],
            'procedure_report_id' => (int) $ids['procedure_report_id'],
            'procedure_result_id' => (int) $ids['procedure_result_id'],
        ];
    }

    /**
     * @param array<string, mixed> $row
     * @return array<string, mixed>
     */
    private function failedResult(array $row, int $index, string $code, string $message): array
    {
        return [
            'field_path' => is_string($row['field_path'] ?? null) ? $row['field_path'] : 'results.' . $index,
            'persistence_status' => 'failed',
            'error' => [
                'code' => $code,
                'message' => $message,
            ],
        ];
    }

    /**
     * @param list<array<string, mixed>> $results
     */
    private function aggregateStatus(array $results): string
    {
        $failed = count(array_filter(
            $results,
            static fn (array $result): bool => ($result['persistence_status'] ?? '') === 'failed',
        ));
        if ($failed === 0) {
            return 'succeeded';
        }
        if ($failed === count($results)) {
            return 'failed';
        }
        return 'partial';
    }

    /**
     * @param array<string, mixed> $row
     */
    private function requiredString(array $row, string $key): string
    {
        $value = $row[$key] ?? null;
        if (!is_string($value) || trim($value) === '') {
            throw new \DomainException('missing_' . $key);
        }
        return trim($value);
    }

    /**
     * @param array<string, mixed> $row
     */
    private function optionalString(array $row, string $key): ?string
    {
        $value = $row[$key] ?? null;
        if (!is_string($value) || trim($value) === '') {
            return null;
        }
        return trim($value);
    }

    private function dateTime(string $value, string $key): string
    {
        try {
            return (new \DateTimeImmutable($value))
                ->setTimezone(new \DateTimeZone('UTC'))
                ->format('Y-m-d H:i:s');
        } catch (\Throwable) {
            throw new \DomainException('invalid_' . $key);
        }
    }

    private function referenceRange(?string $range, string $unit): string
    {
        if ($range === null) {
            return '';
        }
        if (preg_match('/^\s*([+-]?\d+(?:\.\d+)?)\s*[-–]\s*([+-]?\d+(?:\.\d+)?)\s*([^\s]+)?\s*$/', $range, $matches) === 1) {
            return $matches[1] . '-' . $matches[2] . (!empty($matches[3]) && $matches[3] !== $unit ? ' ' . $matches[3] : '');
        }
        return $range;
    }

    private function abnormal(?string $flag): string
    {
        return match ($flag) {
            'normal' => 'no',
            'high' => 'high',
            'low' => 'low',
            'critical' => 'vhigh',
            'unknown', null => '',
            default => 'yes',
        };
    }

    private function documentId(string $sourceDocumentReference): int
    {
        $id = preg_replace('/^DocumentReference\//', '', $sourceDocumentReference);
        if (is_string($id) && ctype_digit($id)) {
            return (int) $id;
        }
        if (is_string($id) && $id !== '') {
            $row = $this->db->fetchRow(
                'SELECT `id` FROM `documents` WHERE `uuid` = ?',
                [$id],
            );
            if ($row !== null && is_numeric($row['id'] ?? null)) {
                return (int) $row['id'];
            }
        }
        return 0;
    }

    /**
     * @param array<string, mixed> $row
     */
    private function comments(array $row, string $sourceDocumentReference, string $fieldPath): string
    {
        $parts = [
            'copilot_source_document_reference=' . $sourceDocumentReference,
            'copilot_field_path=' . $fieldPath,
        ];
        foreach (['original_unit', 'reference_range', 'ordering_provider'] as $key) {
            $value = $this->optionalString($row, $key);
            if ($value !== null) {
                $parts[] = $key . '=' . $value;
            }
        }
        return implode("\n", $parts);
    }

    private function controlId(string $sourceDocumentReference, string $fieldPath): string
    {
        return substr(hash('sha256', $sourceDocumentReference . '|' . $fieldPath), 0, 40);
    }
}
