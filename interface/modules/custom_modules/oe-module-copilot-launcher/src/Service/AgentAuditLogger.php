<?php

/**
 * Writes immutable rows to agent_audit. Pure side-effect collaborator: no
 * read paths, no mutation of inputs. Inject a DatabaseExecutor so callers can
 * unit-test by handing in a fake.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\CopilotLauncher\Service;

use OpenEMR\Modules\CopilotLauncher\Domain\AuditEntry;
use Psr\Log\LoggerInterface;

final readonly class AgentAuditLogger
{
    public function __construct(
        private DatabaseExecutor $db,
        private LoggerInterface $logger,
    ) {
    }

    /**
     * @return int the newly inserted row id
     */
    public function record(AuditEntry $entry): int
    {
        $sql = 'INSERT INTO `agent_audit` ('
            . '`conversation_id`,`turn`,`pid`,`user_id`,'
            . '`workflow_id`,`classifier_confidence`,`decision`,`escalation_reason`,'
            . '`model`,`tokens_in`,`tokens_out`,`latency_ms`,`cost_usd`,'
            . '`break_glass`,`ip_address`'
            . ') VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)';

        $binds = [
            $entry->conversationId->value,
            $entry->turn,
            $entry->pid->value,
            $entry->userId,
            $entry->workflowId,
            $entry->classifierConfidence,
            $entry->decision->value,
            $entry->escalationReason,
            $entry->model,
            $entry->tokensIn,
            $entry->tokensOut,
            $entry->latencyMs,
            $entry->costUsd,
            $entry->breakGlass ? 1 : 0,
            $entry->ipAddress,
        ];

        try {
            $id = $this->db->insert($sql, $binds);
            $this->logger->info('agent_audit row written', [
                'audit_id' => $id,
                'conversation_id' => $entry->conversationId->value,
                'turn' => $entry->turn,
                'decision' => $entry->decision->value,
                'pid' => $entry->pid->value,
            ]);
            return $id;
        } catch (\Throwable $e) {
            // Never silently swallow — audit failures must surface to operators.
            $this->logger->error('Failed to write agent_audit row', [
                'conversation_id' => $entry->conversationId->value,
                'decision' => $entry->decision->value,
                'exception' => $e,
            ]);
            throw $e;
        }
    }
}
