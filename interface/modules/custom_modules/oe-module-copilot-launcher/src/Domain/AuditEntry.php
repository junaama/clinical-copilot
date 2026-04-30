<?php

/**
 * Immutable DTO describing one audit row.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\CopilotLauncher\Domain;

final readonly class AuditEntry
{
    public function __construct(
        public ConversationId $conversationId,
        public int $turn,
        public PatientPid $pid,
        public int $userId,
        public AuditDecision $decision,
        public ?string $workflowId = null,
        public ?float $classifierConfidence = null,
        public ?string $escalationReason = null,
        public ?string $model = null,
        public ?int $tokensIn = null,
        public ?int $tokensOut = null,
        public ?int $latencyMs = null,
        public ?float $costUsd = null,
        public bool $breakGlass = false,
        public ?string $ipAddress = null,
    ) {
        if ($turn < 1) {
            throw new \DomainException('turn must be >= 1');
        }
        if ($userId <= 0) {
            throw new \DomainException('userId must be positive');
        }
        if (
            $classifierConfidence !== null
            && ($classifierConfidence < 0.0 || $classifierConfidence > 1.0)
        ) {
            throw new \DomainException('classifier_confidence must be in [0,1]');
        }
    }
}
