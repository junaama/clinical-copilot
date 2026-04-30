<?php

/**
 * EmbedController — orchestrates the SMART EHR launch and renders the iframe
 * host template. The actual SMART launch token + URL come from OpenEMR's
 * existing SmartLaunchController (do not duplicate that machinery).
 *
 * Inputs (all parsed at the boundary, never trusted as raw):
 *   - The active OpenEMR session — contains user id and the chart pid.
 *   - A pid query param — must match the session pid (defense in depth).
 *
 * Output: HTML for an iframe pointing at the Co-Pilot frontend with the
 * iss/launch params per SMART v1.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\CopilotLauncher\Controller;

use OpenEMR\Modules\CopilotLauncher\Domain\PatientPid;
use Psr\Log\LoggerInterface;

final readonly class EmbedController
{
    public function __construct(
        private LoggerInterface $logger,
        private string $copilotAppUrl,
        private string $issuer,
    ) {
    }

    /**
     * @param array<string, scalar|null> $query Raw $_GET-style input.
     * @param array<string, scalar|null> $session Raw session data we depend on.
     * @return EmbedRenderResult
     */
    public function render(array $query, array $session): EmbedRenderResult
    {
        $pidRaw = $query['pid'] ?? null;
        if (!is_string($pidRaw) && !is_int($pidRaw)) {
            return EmbedRenderResult::error(400, 'pid query parameter required');
        }

        try {
            $pid = PatientPid::fromString((string) $pidRaw);
        } catch (\DomainException) {
            return EmbedRenderResult::error(400, 'pid must be a positive integer');
        }

        $sessionPid = $session['pid'] ?? null;
        if ($sessionPid === null) {
            return EmbedRenderResult::error(401, 'no active patient context');
        }
        if ((int) $sessionPid !== $pid->value) {
            $this->logger->warning('copilot embed pid mismatch', [
                'session_pid' => $sessionPid,
                'requested_pid' => $pid->value,
            ]);
            return EmbedRenderResult::error(403, 'pid does not match active session');
        }

        $userId = $session['authUserID'] ?? $session['authUser'] ?? null;
        if ($userId === null || $userId === '') {
            return EmbedRenderResult::error(401, 'no authenticated user in session');
        }
        if (is_int($userId)) {
            $userIdStr = (string) $userId;
        } elseif (is_string($userId)) {
            $userIdStr = $userId;
        } else {
            return EmbedRenderResult::error(401, 'invalid user identifier in session');
        }

        return EmbedRenderResult::ok(
            pid: $pid,
            userId: $userIdStr,
            copilotAppUrl: $this->copilotAppUrl,
            issuer: $this->issuer,
        );
    }
}
