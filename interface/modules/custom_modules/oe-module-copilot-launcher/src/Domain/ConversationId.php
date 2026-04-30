<?php

/**
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\CopilotLauncher\Domain;

final readonly class ConversationId
{
    public function __construct(public string $value)
    {
        if ($value === '' || strlen($value) > 64) {
            throw new \DomainException('ConversationId must be 1..64 chars');
        }
        if (preg_match('/^[A-Za-z0-9._:\-]+$/', $value) !== 1) {
            throw new \DomainException('ConversationId contains illegal characters');
        }
    }
}
