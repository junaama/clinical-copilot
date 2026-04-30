<?php

/**
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\CopilotLauncher\Domain;

final readonly class PatientPid
{
    public function __construct(public int $value)
    {
        if ($value <= 0) {
            throw new \DomainException('PatientPid must be a positive integer');
        }
    }

    public static function fromString(string $raw): self
    {
        if (preg_match('/^[1-9][0-9]*$/', $raw) !== 1) {
            throw new \DomainException('PatientPid must be a positive integer string');
        }
        return new self((int) $raw);
    }
}
