<?php

/**
 * Closed set of agent decisions per ARCHITECTURE.md §9 step 11.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\CopilotLauncher\Domain;

enum AuditDecision: string
{
    case Allow = 'allow';
    case DeniedAuthz = 'denied_authz';
    case BlockedVerification = 'blocked_verification';
    case RefusedSafety = 'refused_safety';
    case ToolFailure = 'tool_failure';

    public static function fromWire(string $raw): self
    {
        return self::tryFrom($raw)
            ?? throw new \DomainException('Unknown agent audit decision: ' . $raw);
    }
}
