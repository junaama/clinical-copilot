<?php

/**
 * Immutable result of EmbedController::render.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\CopilotLauncher\Controller;

use OpenEMR\Modules\CopilotLauncher\Domain\PatientPid;

final readonly class EmbedRenderResult
{
    private function __construct(
        public int $statusCode,
        public ?string $errorMessage,
        public ?PatientPid $pid,
        public ?string $userId,
        public ?string $copilotAppUrl,
        public ?string $issuer,
    ) {
    }

    public static function ok(
        PatientPid $pid,
        string $userId,
        string $copilotAppUrl,
        string $issuer,
    ): self {
        return new self(200, null, $pid, $userId, $copilotAppUrl, $issuer);
    }

    public static function error(int $statusCode, string $message): self
    {
        return new self($statusCode, $message, null, null, null, null);
    }

    public function isOk(): bool
    {
        return $this->statusCode >= 200 && $this->statusCode < 300;
    }
}
