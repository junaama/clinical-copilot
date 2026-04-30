<?php

/**
 * AuditApiController — receives a single audit row from the Co-Pilot iframe
 * and persists it via AgentAuditLogger.
 *
 * The wire format mirrors agent_audit columns, with snake_case keys. This
 * endpoint is the system boundary, so we parse the raw body into a typed
 * AuditEntry immediately — no string-typed downstream code.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\CopilotLauncher\Controller;

use OpenEMR\Modules\CopilotLauncher\Domain\AuditDecision;
use OpenEMR\Modules\CopilotLauncher\Domain\AuditEntry;
use OpenEMR\Modules\CopilotLauncher\Domain\ConversationId;
use OpenEMR\Modules\CopilotLauncher\Domain\PatientPid;
use OpenEMR\Modules\CopilotLauncher\Service\AgentAuditLogger;
use Psr\Log\LoggerInterface;

final readonly class AuditApiController
{
    public function __construct(
        private AgentAuditLogger $logger,
        private LoggerInterface $psrLogger,
    ) {
    }

    /**
     * @param array<string, mixed> $body The decoded JSON body.
     * @param array<string, scalar|null> $session The active session data.
     * @return array{status: int, error?: string}
     */
    public function handle(array $body, array $session): array
    {
        $sessionPid = $session['pid'] ?? null;
        $sessionUser = $session['authUserID'] ?? null;
        if ($sessionPid === null || $sessionUser === null) {
            return ['status' => 401, 'error' => 'no active session'];
        }

        try {
            $entry = self::parseBody($body, (int) $sessionPid, (int) $sessionUser);
        } catch (\LogicException $e) {
            $this->psrLogger->warning('Invalid audit payload', ['exception' => $e]);
            return ['status' => 400, 'error' => 'invalid payload'];
        }

        try {
            $this->logger->record($entry);
        } catch (\RuntimeException) {
            return ['status' => 500, 'error' => 'failed to record audit'];
        }

        return ['status' => 204];
    }

    /**
     * @param array<string, mixed> $body
     */
    private static function parseBody(array $body, int $sessionPid, int $sessionUser): AuditEntry
    {
        $conversationId = new ConversationId(self::stringOf($body, 'conversation_id'));
        $turn = self::intOf($body, 'turn');
        $decision = AuditDecision::fromWire(self::stringOf($body, 'decision'));

        $pidRaw = $body['pid'] ?? null;
        if ($pidRaw === null) {
            $pid = new PatientPid($sessionPid);
        } elseif (is_int($pidRaw) || is_string($pidRaw)) {
            $pid = PatientPid::fromString((string) $pidRaw);
        } else {
            throw new \DomainException('pid must be int or string');
        }
        if ($pid->value !== $sessionPid) {
            // Never let the iframe overwrite the session-scoped pid.
            throw new \DomainException('pid in audit payload does not match session');
        }

        return new AuditEntry(
            conversationId: $conversationId,
            turn: $turn,
            pid: $pid,
            userId: $sessionUser,
            decision: $decision,
            workflowId: self::stringOrNull($body, 'workflow_id'),
            classifierConfidence: self::floatOrNull($body, 'classifier_confidence'),
            escalationReason: self::stringOrNull($body, 'escalation_reason'),
            model: self::stringOrNull($body, 'model'),
            tokensIn: self::intOrNull($body, 'tokens_in'),
            tokensOut: self::intOrNull($body, 'tokens_out'),
            latencyMs: self::intOrNull($body, 'latency_ms'),
            costUsd: self::floatOrNull($body, 'cost_usd'),
            breakGlass: (bool) ($body['break_glass'] ?? false),
            ipAddress: self::stringOrNull($body, 'ip_address'),
        );
    }

    /** @param array<string, mixed> $body */
    private static function stringOf(array $body, string $key): string
    {
        if (!isset($body[$key]) || !is_string($body[$key]) || $body[$key] === '') {
            throw new \DomainException("missing string field: {$key}");
        }
        return $body[$key];
    }

    /** @param array<string, mixed> $body */
    private static function intOf(array $body, string $key): int
    {
        $raw = $body[$key] ?? null;
        if (!is_int($raw) && !(is_string($raw) && preg_match('/^-?[0-9]+$/', $raw) === 1)) {
            throw new \DomainException("missing int field: {$key}");
        }
        return (int) $raw;
    }

    /** @param array<string, mixed> $body */
    private static function stringOrNull(array $body, string $key): ?string
    {
        $raw = $body[$key] ?? null;
        if ($raw === null || $raw === '') {
            return null;
        }
        if (!is_string($raw)) {
            throw new \DomainException("field {$key} must be a string");
        }
        return $raw;
    }

    /** @param array<string, mixed> $body */
    private static function intOrNull(array $body, string $key): ?int
    {
        $raw = $body[$key] ?? null;
        if ($raw === null) {
            return null;
        }
        if (!is_int($raw) && !(is_string($raw) && preg_match('/^-?[0-9]+$/', $raw) === 1)) {
            throw new \DomainException("field {$key} must be an int");
        }
        return (int) $raw;
    }

    /** @param array<string, mixed> $body */
    private static function floatOrNull(array $body, string $key): ?float
    {
        $raw = $body[$key] ?? null;
        if ($raw === null) {
            return null;
        }
        if (!is_int($raw) && !is_float($raw) && !(is_string($raw) && is_numeric($raw))) {
            throw new \DomainException("field {$key} must be numeric");
        }
        return (float) $raw;
    }
}
