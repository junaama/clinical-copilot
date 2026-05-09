<?php

/**
 * Co-Pilot Lab Writer bootstrap.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Naama <naama.paulemont@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Naama
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\CopilotLabWriter;

use OpenEMR\Common\Http\HttpRestRequest;
use OpenEMR\Events\RestApiExtend\RestApiCreateEvent;
use OpenEMR\Modules\CopilotLabWriter\Controller\LabResultApiController;
use OpenEMR\Modules\CopilotLabWriter\Service\LabResultWriter;
use OpenEMR\Modules\CopilotLabWriter\Service\QueryUtilsExecutor;
use OpenEMR\RestControllers\Config\RestConfig;
use Symfony\Component\EventDispatcher\EventDispatcherInterface;

final class Bootstrap
{
    public const MODULE_INSTALLATION_PATH = '/interface/modules/custom_modules/oe-module-copilot-lab-writer';
    public const MODULE_NAME = 'oe-module-copilot-lab-writer';

    public function __construct(
        private readonly EventDispatcherInterface $eventDispatcher,
    ) {
    }

    public function subscribeToEvents(): void
    {
        $this->eventDispatcher->addListener(
            RestApiCreateEvent::EVENT_HANDLE,
            $this->onRestApiCreate(...)
        );
    }

    public function onRestApiCreate(RestApiCreateEvent $event): void
    {
        $event->addToRouteMap(
            'POST /api/patient/:pid/lab_result',
            static function ($pid, HttpRestRequest $request) {
                RestConfig::request_authorization_check($request, 'patients', 'lab', ['write', 'addonly']);
                $body = json_decode((string) file_get_contents('php://input'), true);
                $payload = is_array($body) ? $body : [];
                $writer = new LabResultWriter(
                    db: new QueryUtilsExecutor(),
                    uuidGenerator: static fn (): string => random_bytes(16),
                );
                return (new LabResultApiController($writer))->post($pid, $payload);
            }
        );
    }
}
